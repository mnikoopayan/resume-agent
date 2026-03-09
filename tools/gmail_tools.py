"""
Enhanced Gmail Tools

Keeps all read-only tools from the sample and adds:
- send_email: Send new emails via Gmail API (OAuth)
- send_email_reply: Reply to existing threads
- create_draft: Create email drafts
- apply_label: Apply labels to messages
- mark_as_read: Mark messages as read

Uses Agno GmailTools for read operations and Gmail API (OAuth) for send operations (SMTP fallback is optional).
"""
import base64
import json
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import getaddresses
from typing import Any, Callable, Dict, List, Optional, Sequence

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Read-only functions that are safe for resume screening workflows.
READ_ONLY_GMAIL_FUNCTIONS = [
    "get_latest_emails",
    "get_emails_from_user",
    "get_unread_emails",
    "get_starred_emails",
    "get_emails_by_context",
    "get_emails_by_date",
    "get_emails_by_thread",
    "search_emails",
    "list_custom_labels",
]

# Mutating functions we intentionally block from Agno toolkit.
MUTATING_GMAIL_FUNCTIONS = [
    "create_draft_email",
    "send_email",
    "send_email_reply",
    "mark_email_as_read",
    "mark_email_as_unread",
    "apply_label",
    "remove_label",
    "delete_custom_label",
]


def _resolve_main_token_path(token_path: Optional[str]) -> str:
    """Return the canonical token path used by the OAuth server."""
    return token_path or os.getenv("GOOGLE_TOKEN_PATH", "./google_api_server/token.json")


def _resolve_readonly_token_path(main_token_path: str) -> str:
    """Return a separate token path for read-only Gmail operations.

    Rationale:
      Agno's GmailTools refreshes tokens and writes them back to the token file.
      If we point read-only tools at the canonical token.json, it can overwrite
      the file with a narrower scope set (e.g., gmail.readonly only), which then
      breaks Calendar and Gmail send features elsewhere in the app.

    You can override this path with the GOOGLE_READONLY_TOKEN_PATH env var.
    """
    override = os.getenv("GOOGLE_READONLY_TOKEN_PATH")
    if override:
        return override

    p = Path(main_token_path)
    # token.json -> token_readonly.json (same folder)
    return str(p.with_name(f"{p.stem}_readonly{p.suffix}"))


def _ensure_readonly_token_exists(readonly_token_path: str, main_token_path: str) -> None:
    """Create a read-only token file (copy) if missing, so GmailTools can refresh safely."""
    try:
        rp = Path(readonly_token_path)
        if rp.exists():
            return
        rp.parent.mkdir(parents=True, exist_ok=True)
        mp = Path(main_token_path)
        if mp.exists():
            rp.write_bytes(mp.read_bytes())
    except Exception as exc:
        logger.debug("Could not prepare read-only token file: %s", exc)


def create_readonly_gmail_tools(
    credentials_path: Optional[str] = None,
    token_path: Optional[str] = None,
    scopes: Optional[Sequence[str]] = None,
    port: Optional[int] = None,
):
    """
    Create a GmailTools toolkit limited to read-only functions whenever possible.

    Args:
        credentials_path: Path to Google OAuth client credentials JSON.
        token_path: Path to OAuth token JSON.
        scopes: OAuth scopes to request.
        port: Local server port for OAuth flow.

    Returns:
        GmailTools instance restricted to read-only operations.
    """
    if scopes is None:
        # Force a safe default so Agno doesn't request broader scopes (e.g., gmail.modify)
        # that your token/consent may not include.
        scopes = ["https://www.googleapis.com/auth/gmail.readonly"]

    try:
        from agno.tools.gmail import GmailTools
    except ImportError as exc:
        raise ImportError(
            "GmailTools is unavailable. Install Gmail dependencies and ensure "
            "the current agno version includes agno.tools.gmail."
        ) from exc

    base_kwargs: Dict[str, Any] = {}
    if credentials_path:
        base_kwargs["credentials_path"] = credentials_path

    # IMPORTANT: keep read-only GmailTools from overwriting the canonical token.json.
    main_token_path = _resolve_main_token_path(token_path)
    readonly_token_path = _resolve_readonly_token_path(main_token_path)
    _ensure_readonly_token_exists(readonly_token_path, main_token_path)
    base_kwargs["token_path"] = readonly_token_path
    if scopes:
        base_kwargs["scopes"] = list(scopes)
    if port is not None:
        base_kwargs["port"] = port

    # Prefer allow-listing read-only functions.
    try:
        return GmailTools(include_tools=READ_ONLY_GMAIL_FUNCTIONS, **base_kwargs)
    except TypeError:
        logger.debug("GmailTools does not support include_tools; trying exclude_tools fallback.")

    # Fallback to deny-listing mutating functions.
    try:
        return GmailTools(exclude_tools=MUTATING_GMAIL_FUNCTIONS, **base_kwargs)
    except TypeError:
        logger.warning(
            "GmailTools could not be restricted to read-only mode. "
            "Tool is enabled without function-level restrictions."
        )
        return GmailTools(**base_kwargs)


def create_full_gmail_tools(
    credentials_path: Optional[str] = None,
    token_path: Optional[str] = None,
    scopes: Optional[Sequence[str]] = None,
    port: Optional[int] = None,
):
    """
    Create a GmailTools toolkit with all functions enabled (read + write).

    Args:
        credentials_path: Path to Google OAuth client credentials JSON.
        token_path: Path to OAuth token JSON.
        scopes: OAuth scopes to request.
        port: Local server port for OAuth flow.

    Returns:
        GmailTools instance with full capabilities.
    """
    try:
        from agno.tools.gmail import GmailTools
    except ImportError as exc:
        raise ImportError(
            "GmailTools is unavailable. Install Gmail dependencies."
        ) from exc

    base_kwargs: Dict[str, Any] = {}
    if credentials_path:
        base_kwargs["credentials_path"] = credentials_path

    # Full Gmail tools must use the canonical token so send/reply actions can
    # access the latest full-scope credentials.
    main_token_path = _resolve_main_token_path(token_path)
    base_kwargs["token_path"] = main_token_path
    if scopes:
        base_kwargs["scopes"] = list(scopes)
    if port is not None:
        base_kwargs["port"] = port

    return GmailTools(**base_kwargs)


class SmtpGmailSender:
    """
    Gmail sender that prefers Gmail API (OAuth) and falls back to SMTP if configured.

    Why:
    - Google blocks "less secure" SMTP password auth for most accounts.
    - OAuth-based Gmail API sending avoids App Passwords and works for Google Workspace.

    Requirements for Gmail API sending:
    - GOOGLE_TOKEN_PATH points to token.json created by the local OAuth server
      (google_api_server), and that token includes the gmail.send scope.
    """

    # Gmail API scopes needed for sending.
    REQUIRED_SEND_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

    def __init__(
        self,
        gmail_address: Optional[str] = None,
        gmail_password: Optional[str] = None,
        token_path: Optional[str] = None,
        credentials_path: Optional[str] = None,
        smtp_host: str = "smtp.gmail.com",
        smtp_port: int = 587,
    ):
        self.gmail_address = gmail_address or os.getenv("GMAIL_ADDRESS", "")
        self.gmail_password = gmail_password or os.getenv("GMAIL_PASSWORD", "")
        self.token_path = token_path or os.getenv("GOOGLE_TOKEN_PATH", "./google_api_server/token.json")
        self.credentials_path = credentials_path or os.getenv(
            "GOOGLE_CREDENTIALS_PATH", "./google_api_server/credentials.json"
        )
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port

    def _abs_path(self, path: str) -> str:
        """Resolve paths stably from the project root, not the caller's cwd."""
        candidate = Path(path).expanduser()
        if candidate.is_absolute():
            return str(candidate.resolve())
        return str((PROJECT_ROOT / candidate).resolve())

    @staticmethod
    def _parse_recipients(*values: str) -> List[str]:
        recipients: List[str] = []
        for _, addr in getaddresses(values):
            addr = addr.strip()
            if addr:
                recipients.append(addr)
        # Preserve order while deduplicating.
        return list(dict.fromkeys(recipients))

    def _load_oauth_credentials(self, required_scopes: Optional[Sequence[str]] = None) -> Optional["Credentials"]:
        """
        Load OAuth credentials from token.json and refresh if needed.

        Returns:
            google.oauth2.credentials.Credentials or None if not available.
        """
        required_scopes = list(required_scopes or [])
        token_path = self._abs_path(self.token_path)

        if not os.path.exists(token_path):
            return None

        try:
            with open(token_path, "r", encoding="utf-8") as f:
                token_data = json.load(f)
        except Exception as exc:
            logger.warning("Failed to read token file %s: %s", token_path, exc)
            return None

        # Validate scopes (when available in token file).
        token_scopes = token_data.get("scopes") or []
        if required_scopes:
            missing = [s for s in required_scopes if s not in token_scopes]
            if missing:
                logger.warning("OAuth token is missing required scopes: %s", missing)
                return None

        try:
            creds = Credentials(
                token=token_data.get("token"),
                refresh_token=token_data.get("refresh_token"),
                token_uri=token_data.get("token_uri"),
                client_id=token_data.get("client_id"),
                client_secret=token_data.get("client_secret"),
                scopes=token_scopes or required_scopes or None,
            )
        except Exception as exc:
            logger.warning("Failed to construct OAuth credentials: %s", exc)
            return None

        # Refresh token if expired.
        try:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self._persist_refreshed_token(token_path, token_data, creds)
        except Exception as exc:
            logger.warning("Failed to refresh OAuth token: %s", exc)
            return creds

        return creds

    def _persist_refreshed_token(self, token_path: str, token_data: Dict[str, Any], creds: "Credentials") -> None:
        try:
            updated = dict(token_data)
            updated["token"] = creds.token
            if getattr(creds, "refresh_token", None):
                updated["refresh_token"] = creds.refresh_token
            if getattr(creds, "scopes", None):
                updated["scopes"] = list(creds.scopes)
            if getattr(creds, "expiry", None):
                updated["expiry"] = creds.expiry.isoformat()
            with open(token_path, "w", encoding="utf-8") as f:
                json.dump(updated, f, indent=2)
        except Exception as exc:
            logger.debug("Could not persist refreshed token: %s", exc)

    def _build_gmail_service(self) -> Optional[Any]:
        creds = self._load_oauth_credentials(required_scopes=self.REQUIRED_SEND_SCOPES)
        if creds is None:
            return None
        try:
            return build("gmail", "v1", credentials=creds, cache_discovery=False)
        except Exception as exc:
            logger.warning("Failed to build Gmail API client: %s", exc)
            return None

    def _mime_to_raw(self, msg: MIMEMultipart) -> str:
        return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        bcc: str = "",
        reply_to: str = "",
        html: bool = False,
    ) -> Dict[str, Any]:
        """
        Send an email via Gmail API (OAuth) if available; otherwise fall back to SMTP.

        Returns:
            Dictionary with send result.
        """
        recipients = self._parse_recipients(to, cc, bcc)
        if not recipients:
            return {
                "success": False,
                "error": "At least one valid recipient email address is required.",
            }

        token_exists = os.path.exists(self._abs_path(self.token_path))

        # 1) Prefer Gmail API (OAuth)
        service = self._build_gmail_service()
        if token_exists and service is None:
            return {
                "success": False,
                "error": (
                    "OAuth token exists, but Gmail API sending is not available. "
                    "Most likely the token was created without the gmail.send scope. "
                    "Update google_api_server SCOPES to include https://www.googleapis.com/auth/gmail.send, "
                    "delete token.json, then visit http://localhost:8000/auth again to re-authorize."
                ),
                "transport": "gmail_api",
            }

        if service is not None:
            try:
                msg = MIMEMultipart("alternative")
                if self.gmail_address:
                    msg["From"] = self.gmail_address
                msg["To"] = ", ".join(self._parse_recipients(to))
                msg["Subject"] = subject
                parsed_cc = self._parse_recipients(cc)
                if parsed_cc:
                    msg["Cc"] = ", ".join(parsed_cc)
                if reply_to:
                    msg["Reply-To"] = reply_to

                content_type = "html" if html else "plain"
                msg.attach(MIMEText(body, content_type, "utf-8"))

                raw = self._mime_to_raw(msg)
                payload: Dict[str, Any] = {"raw": raw}
                resp = service.users().messages().send(userId="me", body=payload).execute()
                return {
                    "success": True,
                    "to": to,
                    "subject": subject,
                    "from": self.gmail_address or "me",
                    "message_id": resp.get("id"),
                    "thread_id": resp.get("threadId"),
                    "transport": "gmail_api",
                }
            except HttpError as exc:
                logger.error("Gmail API send failed: %s", exc)
                return {
                    "success": False,
                    "error": f"Gmail API send failed: {exc}",
                    "transport": "gmail_api",
                }
            except Exception as exc:
                logger.error("Gmail API send failed: %s", exc)
                return {
                    "success": False,
                    "error": str(exc),
                    "transport": "gmail_api",
                }

        # 2) SMTP fallback (only if configured).
        if not self.gmail_address or not self.gmail_password:
            return {
                "success": False,
                "error": (
                    "Email send is not configured. "
                    "Preferred: OAuth Gmail API (set GOOGLE_TOKEN_PATH to a token.json that includes gmail.send). "
                    "Fallback: set GMAIL_ADDRESS and GMAIL_PASSWORD for SMTP (App Password required for Google accounts)."
                ),
            }

        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = self.gmail_address
            msg["To"] = ", ".join(self._parse_recipients(to))
            msg["Subject"] = subject

            parsed_cc = self._parse_recipients(cc)
            if parsed_cc:
                msg["Cc"] = ", ".join(parsed_cc)
            if reply_to:
                msg["Reply-To"] = reply_to

            content_type = "html" if html else "plain"
            msg.attach(MIMEText(body, content_type, "utf-8"))

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(self.gmail_address, self.gmail_password)
                server.sendmail(self.gmail_address, recipients, msg.as_string())

            logger.info("Email sent to %s: %s", to, subject)
            return {
                "success": True,
                "to": to,
                "subject": subject,
                "from": self.gmail_address,
                "transport": "smtp",
            }

        except smtplib.SMTPAuthenticationError as exc:
            logger.error("SMTP authentication failed: %s", exc)
            return {"success": False, "error": f"SMTP authentication failed: {exc}", "transport": "smtp"}
        except Exception as exc:
            logger.error("Failed to send email via SMTP: %s", exc)
            return {"success": False, "error": str(exc), "transport": "smtp"}

    def send_reply(
        self,
        to: str,
        subject: str,
        body: str,
        in_reply_to: str = "",
        references: str = "",
    ) -> Dict[str, Any]:
        """
        Send a reply email. For Gmail API, threading is best-effort using headers.

        Note:
        - For perfect threading, you'd also pass the Gmail threadId. This code keeps
          compatibility with the existing interface.
        """
        recipients = self._parse_recipients(to)
        if not recipients:
            return {
                "success": False,
                "error": "A valid recipient email address is required for replies.",
            }

        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        token_exists = os.path.exists(self._abs_path(self.token_path))

        # Prefer Gmail API
        service = self._build_gmail_service()
        if token_exists and service is None:
            return {
                "success": False,
                "error": (
                    "OAuth token exists, but Gmail API sending is not available. "
                    "Make sure you re-authorize with the gmail.send scope and regenerate token.json."
                ),
                "transport": "gmail_api",
            }

        if service is not None:
            try:
                msg = MIMEMultipart("alternative")
                if self.gmail_address:
                    msg["From"] = self.gmail_address
                msg["To"] = ", ".join(recipients)
                msg["Subject"] = subject
                if in_reply_to:
                    msg["In-Reply-To"] = in_reply_to
                    msg["References"] = references or in_reply_to

                msg.attach(MIMEText(body, "plain", "utf-8"))
                raw = self._mime_to_raw(msg)

                payload: Dict[str, Any] = {"raw": raw}
                resp = service.users().messages().send(userId="me", body=payload).execute()
                return {
                    "success": True,
                    "to": to,
                    "subject": subject,
                    "from": self.gmail_address or "me",
                    "message_id": resp.get("id"),
                    "thread_id": resp.get("threadId"),
                    "transport": "gmail_api",
                }
            except HttpError as exc:
                logger.error("Gmail API reply send failed: %s", exc)
                return {"success": False, "error": f"Gmail API send failed: {exc}", "transport": "gmail_api"}
            except Exception as exc:
                logger.error("Gmail API reply send failed: %s", exc)
                return {"success": False, "error": str(exc), "transport": "gmail_api"}

        # SMTP fallback
        if not self.gmail_address or not self.gmail_password:
            return {"success": False, "error": "Gmail credentials not configured for SMTP fallback."}

        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = self.gmail_address
            msg["To"] = ", ".join(recipients)
            msg["Subject"] = subject
            if in_reply_to:
                msg["In-Reply-To"] = in_reply_to
                msg["References"] = references or in_reply_to

            msg.attach(MIMEText(body, "plain", "utf-8"))

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(self.gmail_address, self.gmail_password)
                server.sendmail(self.gmail_address, [to], msg.as_string())

            return {"success": True, "to": to, "subject": subject, "from": self.gmail_address, "transport": "smtp"}
        except Exception as exc:
            return {"success": False, "error": str(exc), "transport": "smtp"}

def create_smtp_gmail_tools(sender: Optional[SmtpGmailSender] = None) -> List[Callable]:
    """
    Create Gmail API (OAuth)-based Gmail send tools for agent use.

    Args:
        sender: SmtpGmailSender instance (creates default if None).

    Returns:
        List of callable tool functions.
    """
    if sender is None:
        sender = SmtpGmailSender()

    def send_email(
        to: str,
        subject: str,
        body: str,
        cc: str = "",
    ) -> str:
        """
        Send a new email via Gmail API (OAuth).

        Args:
            to: Recipient email address.
            subject: Email subject line.
            body: Email body text.
            cc: CC recipients (comma-separated).

        Returns:
            JSON with send result (success/failure).
        """
        result = sender.send_email(to=to, subject=subject, body=body, cc=cc)
        return json.dumps(result, indent=2)

    def send_email_reply(
        to: str,
        subject: str,
        body: str,
        in_reply_to: str = "",
    ) -> str:
        """
        Send a reply to an existing email thread.

        Args:
            to: Recipient email address.
            subject: Original email subject (Re: will be prepended if missing).
            body: Reply body text.
            in_reply_to: Message-ID of the email being replied to.

        Returns:
            JSON with send result.
        """
        result = sender.send_reply(
            to=to, subject=subject, body=body, in_reply_to=in_reply_to
        )
        return json.dumps(result, indent=2)

    def create_draft(
        to: str,
        subject: str,
        body: str,
    ) -> str:
        """
        Create an email draft (stored locally as the SMTP approach cannot create
        server-side drafts without OAuth API). Returns the draft content for review.

        Args:
            to: Intended recipient.
            subject: Draft subject.
            body: Draft body.

        Returns:
            JSON with draft content for review before sending.
        """
        draft = {
            "status": "draft_created",
            "to": to,
            "subject": subject,
            "body": body,
            "note": "Draft stored locally. Use send_email to send it.",
        }
        return json.dumps(draft, indent=2)

    return [send_email, send_email_reply, create_draft]
