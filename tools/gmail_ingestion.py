"""
Enhanced Gmail Ingestion Pipeline

Features (preserved from sample):
- Fetches Gmail messages for a query
- Inserts message body into knowledge base
- Downloads supported attachments and inserts them into knowledge base
- Tracks processed message IDs in SQLite for idempotency

Enhancements:
- Auto-classifies incoming emails during ingestion
- Extracts applicant info and creates candidate profiles
- Routes classified emails to appropriate workflows
- Supports DOCX attachments in addition to PDF and TXT
"""
import base64
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Dict, List, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from tools.knowledge_tool import InsertKnowledgeTool

logger = logging.getLogger(__name__)




def _safe_write_token(token_path: Path, creds_json: str) -> None:
    """Write refreshed creds without accidentally shrinking OAuth scopes.

    Some components may load credentials with a narrower scope list, then refresh and
    overwrite token.json. That can break other features (Calendar, Gmail send).
    This helper preserves the existing scopes on disk if the refreshed creds would
    remove any previously-authorized scopes.
    """
    try:
        new_data = json.loads(creds_json)
    except Exception:
        token_path.write_text(creds_json, encoding="utf-8")
        return

    old_scopes: set[str] = set()
    try:
        if token_path.exists():
            old_data = json.loads(token_path.read_text(encoding="utf-8"))
            old_scopes = set(old_data.get("scopes") or [])
    except Exception:
        old_scopes = set()

    new_scopes = set(new_data.get("scopes") or [])

    # If refreshed token JSON lacks scopes, keep the old ones.
    if not new_scopes and old_scopes:
        new_data["scopes"] = sorted(old_scopes)
        token_path.write_text(json.dumps(new_data, indent=2), encoding="utf-8")
        return

    # If refreshed creds would drop any existing scopes, keep the union.
    if old_scopes and new_scopes and not old_scopes.issubset(new_scopes):
        logger.warning(
            "Refreshed token would shrink scopes. Preserving existing scopes. old=%s new=%s",
            sorted(old_scopes),
            sorted(new_scopes),
        )
        new_data["scopes"] = sorted(old_scopes.union(new_scopes))

    token_path.write_text(json.dumps(new_data, indent=2), encoding="utf-8")
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SUPPORTED_ATTACHMENT_EXTENSIONS = {".pdf", ".txt", ".docx"}


@dataclass
class GmailSyncSummary:
    """Summary of a Gmail sync operation."""
    fetched: int = 0
    skipped_existing: int = 0
    processed: int = 0
    failed: int = 0
    attachments_ingested: int = 0
    candidates_created: int = 0
    classifications: Dict[str, int] = None

    def __post_init__(self):
        if self.classifications is None:
            self.classifications = {}

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "fetched": self.fetched,
            "skipped_existing": self.skipped_existing,
            "processed": self.processed,
            "failed": self.failed,
            "attachments_ingested": self.attachments_ingested,
            "candidates_created": self.candidates_created,
            "classifications": self.classifications,
        }


class GmailIngestionService:
    """
    Enhanced Gmail ingestion service that synchronizes messages and attachments
    into the knowledge base with email classification and candidate profiling.
    """

    def __init__(
        self,
        knowledge_tool: InsertKnowledgeTool,
        credentials_path: str,
        token_path: str,
        db_path: str = "./knowledge/gmail_ingestion.db",
        attachments_dir: str = "./dropbox/gmail",
        query: str = "in:inbox",
        max_results: int = 20,
        unread_only: bool = False,
        classifier: Optional[Any] = None,
        candidate_db: Optional[Any] = None,
    ):
        """
        Initialize the Gmail ingestion service.

        Args:
            knowledge_tool: InsertKnowledgeTool for knowledge base insertion.
            credentials_path: Path to Google OAuth client credentials JSON.
            token_path: Path to OAuth token JSON.
            db_path: Path to SQLite state database.
            attachments_dir: Directory for downloaded attachments.
            query: Gmail search query.
            max_results: Maximum messages per sync.
            unread_only: Whether to filter unread only.
            classifier: Optional EmailClassifier instance.
            candidate_db: Optional CandidateDB instance.
        """
        self.knowledge_tool = knowledge_tool
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)
        self.db_path = Path(db_path)
        self.attachments_dir = Path(attachments_dir)
        self.query = query.strip() or "in:inbox"
        self.max_results = max_results
        self.unread_only = unread_only
        self.classifier = classifier
        self.candidate_db = candidate_db

        self.attachments_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Create the tracking database tables if they do not exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS processed_messages (
                    message_id TEXT PRIMARY KEY,
                    thread_id TEXT,
                    from_email TEXT,
                    subject TEXT,
                    processed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    classification TEXT DEFAULT '',
                    candidate_id INTEGER DEFAULT 0
                );
            """)

    def _is_processed(self, message_id: str) -> bool:
        """Check if a message has already been processed."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT status FROM processed_messages WHERE message_id = ?",
                (message_id,),
            ).fetchone()
        return bool(row and row[0] == "success")

    def _record_result(
        self,
        message_id: str,
        thread_id: str,
        from_email: str,
        subject: str,
        status: str,
        error: Optional[str] = None,
        classification: str = "",
        candidate_id: int = 0,
    ) -> None:
        """Record the processing result for a message."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO processed_messages (
                    message_id, thread_id, from_email, subject,
                    processed_at, status, error, classification, candidate_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    thread_id = excluded.thread_id,
                    from_email = excluded.from_email,
                    subject = excluded.subject,
                    processed_at = excluded.processed_at,
                    status = excluded.status,
                    error = excluded.error,
                    classification = excluded.classification,
                    candidate_id = excluded.candidate_id
                """,
                (
                    message_id, thread_id, from_email, subject,
                    datetime.now(timezone.utc).isoformat(),
                    status, error, classification, candidate_id,
                ),
            )

    def _load_credentials(self) -> Credentials:
        """Load and refresh Google OAuth credentials."""
        if not self.token_path.exists():
            raise FileNotFoundError(
                f"Gmail token file not found: {self.token_path}. "
                "Authorize first to generate token.json."
            )
        if not self.credentials_path.exists():
            raise FileNotFoundError(
                f"Gmail credentials file not found: {self.credentials_path}"
            )

        creds = Credentials.from_authorized_user_file(str(self.token_path))
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _safe_write_token(self.token_path, creds.to_json())
        return creds

    def _build_service(self):
        """Build the Gmail API service."""
        creds = self._load_credentials()
        return build("gmail", "v1", credentials=creds)

    def _build_query(self) -> str:
        """Build the Gmail search query."""
        if self.unread_only and "is:unread" not in self.query:
            return f"{self.query} is:unread"
        return self.query

    @staticmethod
    def _decode_data(data: str) -> str:
        """Decode base64url-encoded data."""
        raw = base64.urlsafe_b64decode(data.encode("utf-8"))
        return raw.decode("utf-8", errors="ignore")

    def _extract_text_body(self, payload: Dict) -> str:
        """Recursively extract text body from email payload."""
        mime_type = payload.get("mimeType", "")
        body = payload.get("body", {})
        if mime_type == "text/plain" and body.get("data"):
            return self._decode_data(body["data"])

        text_chunks: List[str] = []
        for part in payload.get("parts", []) or []:
            extracted = self._extract_text_body(part)
            if extracted:
                text_chunks.append(extracted)
        return "\n".join(chunk for chunk in text_chunks if chunk).strip()

    def _collect_attachments(self, payload: Dict) -> List[Dict[str, str]]:
        """Collect attachment metadata from email payload."""
        attachments: List[Dict[str, str]] = []
        for part in payload.get("parts", []) or []:
            filename = part.get("filename", "")
            body = part.get("body", {})
            attachment_id = body.get("attachmentId")
            if filename and attachment_id:
                attachments.append({"filename": filename, "attachment_id": attachment_id})
            attachments.extend(self._collect_attachments(part))
        return attachments

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """Sanitize a filename for safe filesystem storage."""
        keep = [ch for ch in name if ch.isalnum() or ch in ("-", "_", ".", " ")]
        cleaned = "".join(keep).strip().replace(" ", "_")
        return cleaned or "attachment.bin"

    def _download_attachment(
        self,
        service,
        message_id: str,
        attachment_id: str,
        filename: str,
    ) -> Path:
        """Download an email attachment to disk."""
        attachment = (
            service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=attachment_id)
            .execute()
        )
        data = attachment.get("data")
        if not data:
            raise ValueError(
                f"Attachment has no data: message_id={message_id}, filename={filename}"
            )
        binary = base64.urlsafe_b64decode(data.encode("utf-8"))

        target_dir = self.attachments_dir / message_id
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_name = self._sanitize_filename(filename)
        target_path = target_dir / safe_name
        target_path.write_bytes(binary)
        return target_path

    def _insert_email_text(
        self,
        message_id: str,
        thread_id: str,
        from_header: str,
        subject: str,
        date: str,
        body_text: str,
        snippet: str,
    ) -> None:
        """Insert email text into the knowledge base."""
        from_email = parseaddr(from_header)[1] or from_header
        payload_text = (
            f"gm:{message_id}\n"
            f"Source: gmail\n"
            f"Message-ID: {message_id}\n"
            f"Thread-ID: {thread_id}\n"
            f"From: {from_header}\n"
            f"From-Email: {from_email}\n"
            f"Subject: {subject}\n"
            f"Date: {date}\n\n"
            f"Body:\n{body_text or snippet}"
        )
        self.knowledge_tool.insert_knowledge(text=payload_text, source="gmail", auto_profile=False)

    def _classify_and_profile(
        self,
        subject: str,
        body_text: str,
        from_email: str,
        from_name: str,
        has_attachments: bool,
    ) -> tuple:
        """Classify email and optionally create candidate profile."""
        classification = ""
        candidate_id = 0

        if self.classifier:
            try:
                result = self.classifier.classify(
                    subject=subject,
                    body=body_text,
                    from_email=from_email,
                    from_name=from_name,
                    has_attachment=has_attachments,
                )
                classification = result.category.value

                # Auto-create candidate for APPLICATION emails
                if classification == "APPLICATION" and self.candidate_db:
                    try:
                        candidate_id = self.candidate_db.create_candidate(
                            name=result.extracted_name or from_name or from_email,
                            email=result.extracted_email or from_email,
                            phone=result.extracted_phone,
                            source="gmail",
                            job_title_applied=result.extracted_position,
                            notes=f"Auto-created from email: {subject}",
                        )
                    except Exception as exc:
                        logger.warning("Failed to create candidate from email: %s", exc)
            except Exception as exc:
                logger.warning("Email classification failed: %s", exc)

        return classification, candidate_id

    def sync(self) -> GmailSyncSummary:
        """
        Synchronize Gmail messages into the knowledge base.

        Returns:
            GmailSyncSummary with processing statistics.
        """
        summary = GmailSyncSummary()
        service = self._build_service()

        query = self._build_query()
        logger.info("Starting Gmail sync: query='%s', max_results=%s", query, self.max_results)

        listed = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=self.max_results)
            .execute()
        )
        messages = listed.get("messages", [])
        summary.fetched = len(messages)

        for item in messages:
            message_id = item["id"]
            if self._is_processed(message_id):
                summary.skipped_existing += 1
                continue

            try:
                msg = (
                    service.users()
                    .messages()
                    .get(userId="me", id=message_id, format="full")
                    .execute()
                )
                payload = msg.get("payload", {})
                headers = {
                    h.get("name", "").lower(): h.get("value", "")
                    for h in payload.get("headers", [])
                }

                thread_id = msg.get("threadId", "")
                subject = headers.get("subject", "")
                from_header = headers.get("from", "")
                from_email = parseaddr(from_header)[1] or from_header
                from_name = parseaddr(from_header)[0] or ""
                date = headers.get("date", "")
                body_text = self._extract_text_body(payload)
                snippet = msg.get("snippet", "")
                attachments = self._collect_attachments(payload)

                # Insert email text into knowledge base
                self._insert_email_text(
                    message_id=message_id,
                    thread_id=thread_id,
                    from_header=from_header,
                    subject=subject,
                    date=date,
                    body_text=body_text,
                    snippet=snippet,
                )

                # Process attachments
                for attachment_meta in attachments:
                    filename = attachment_meta["filename"]
                    extension = Path(filename).suffix.lower()
                    if extension not in SUPPORTED_ATTACHMENT_EXTENSIONS:
                        continue
                    attachment_path = self._download_attachment(
                        service=service,
                        message_id=message_id,
                        attachment_id=attachment_meta["attachment_id"],
                        filename=filename,
                    )
                    self.knowledge_tool.insert_knowledge(
                        file_path=str(attachment_path), source="gmail"
                    )
                    summary.attachments_ingested += 1

                # Classify and create candidate profile
                classification, candidate_id = self._classify_and_profile(
                    subject=subject,
                    body_text=body_text,
                    from_email=from_email,
                    from_name=from_name,
                    has_attachments=bool(attachments),
                )
                if classification:
                    summary.classifications[classification] = (
                        summary.classifications.get(classification, 0) + 1
                    )
                if candidate_id:
                    summary.candidates_created += 1

                self._record_result(
                    message_id=message_id,
                    thread_id=thread_id,
                    from_email=from_email,
                    subject=subject,
                    status="success",
                    classification=classification,
                    candidate_id=candidate_id,
                )
                summary.processed += 1

            except Exception as exc:
                logger.exception("Failed processing Gmail message: %s", message_id)
                self._record_result(
                    message_id=message_id,
                    thread_id="",
                    from_email="",
                    subject="",
                    status="failed",
                    error=str(exc),
                )
                summary.failed += 1

        logger.info(
            "Gmail sync done. fetched=%s processed=%s skipped=%s failed=%s "
            "attachments=%s candidates=%s",
            summary.fetched, summary.processed, summary.skipped_existing,
            summary.failed, summary.attachments_ingested, summary.candidates_created,
        )
        return summary

    def read_recent_records(self, limit: int = 20) -> List[Dict[str, str]]:
        """Read recent processing records from the tracking database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT message_id, thread_id, from_email, subject,
                       processed_at, status, error, classification, candidate_id
                FROM processed_messages
                ORDER BY processed_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [dict(row) for row in rows]

    def dump_recent_records_json(self, limit: int = 20) -> str:
        """Dump recent records as JSON string."""
        return json.dumps(self.read_recent_records(limit=limit), indent=2)
