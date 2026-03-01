"""
Enhanced Dropbox Folder Monitor

Automatically detects and ingests new files from the dropbox folder.
Enhancements over sample:
- DOCX support in addition to PDF and TXT
- Auto-creates candidate profiles on resume ingestion
- Tracks ingestion statistics
- Recursive directory monitoring
- Deduplication via file hash
"""
import asyncio
import hashlib
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Set

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from tools.knowledge_tool import InsertKnowledgeTool

logger = logging.getLogger(__name__)


class DropboxFileHandler(FileSystemEventHandler):
    """
    Handler for file system events in the dropbox folder.
    Processes new files by inserting them into the knowledge base
    and optionally creating candidate profiles.
    """

    def __init__(
        self,
        knowledge_tool: InsertKnowledgeTool,
        supported_extensions: Optional[Set[str]] = None,
        candidate_db: Optional[Any] = None,
        state_db_path: str = "./knowledge/dropbox_state.db",
    ):
        """
        Initialize the file handler.

        Args:
            knowledge_tool: InsertKnowledgeTool instance for inserting files.
            supported_extensions: Set of supported file extensions.
            candidate_db: Optional CandidateDB for auto-profiling.
            state_db_path: Path to SQLite state tracking database.
        """
        super().__init__()
        self.knowledge_tool = knowledge_tool
        self.supported_extensions = supported_extensions or {
            ".pdf", ".txt", ".docx", ".md"
        }
        self.candidate_db = candidate_db
        self.state_db_path = Path(state_db_path)
        self.state_db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_state_db()

        # In-memory dedup set for current session
        self.processed_files: Set[str] = set()
        self.stats = {"processed": 0, "skipped": 0, "failed": 0}

    def _ensure_state_db(self) -> None:
        """Create the state tracking database if it does not exist."""
        with sqlite3.connect(str(self.state_db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ingested_files (
                    file_hash TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    ingested_at TEXT NOT NULL,
                    candidate_id INTEGER DEFAULT 0,
                    status TEXT NOT NULL
                )
            """)

    def _file_hash(self, file_path: Path) -> str:
        """Compute SHA-256 hash of a file for deduplication."""
        hasher = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception:
            return ""

    def _is_already_ingested(self, file_hash: str) -> bool:
        """Check if a file hash has already been ingested."""
        if not file_hash:
            return False
        with sqlite3.connect(str(self.state_db_path)) as conn:
            row = conn.execute(
                "SELECT status FROM ingested_files WHERE file_hash = ?",
                (file_hash,),
            ).fetchone()
        return bool(row and row[0] == "success")

    def _record_ingestion(
        self,
        file_hash: str,
        file_path: str,
        file_name: str,
        status: str,
        candidate_id: int = 0,
    ) -> None:
        """Record file ingestion result."""
        with sqlite3.connect(str(self.state_db_path)) as conn:
            conn.execute(
                """
                INSERT INTO ingested_files (file_hash, file_path, file_name, ingested_at, candidate_id, status)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_hash) DO UPDATE SET
                    file_path = excluded.file_path,
                    ingested_at = excluded.ingested_at,
                    status = excluded.status,
                    candidate_id = excluded.candidate_id
                """,
                (
                    file_hash,
                    file_path,
                    file_name,
                    datetime.now(timezone.utc).isoformat(),
                    candidate_id,
                    status,
                ),
            )

    def on_created(self, event) -> None:
        """Handle file creation event."""
        if event.is_directory:
            return

        file_path = Path(event.src_path)

        if file_path.suffix.lower() not in self.supported_extensions:
            logger.debug("Ignoring unsupported file: %s", file_path)
            return

        if str(file_path) in self.processed_files:
            return

        # Wait for file to be fully written
        time.sleep(0.5)

        self._process_file(file_path)

    def on_modified(self, event) -> None:
        """Handle file modification event (re-ingest updated files)."""
        if event.is_directory:
            return

        file_path = Path(event.src_path)
        if file_path.suffix.lower() not in self.supported_extensions:
            return

        # Only re-process if content changed (new hash)
        file_hash = self._file_hash(file_path)
        if file_hash and not self._is_already_ingested(file_hash):
            time.sleep(0.5)
            self._process_file(file_path)

    def _process_file(self, file_path: Path) -> None:
        """Process a file: insert into knowledge base and optionally create candidate."""
        file_hash = self._file_hash(file_path)

        # Check deduplication
        if file_hash and self._is_already_ingested(file_hash):
            logger.info("Skipping already-ingested file: %s", file_path.name)
            self.stats["skipped"] += 1
            return

        try:
            logger.info("Processing new file: %s", file_path)
            result = self.knowledge_tool.insert_knowledge(
                file_path=str(file_path), source="dropbox"
            )
            self.processed_files.add(str(file_path))
            self.stats["processed"] += 1

            # Auto-create candidate profile if candidate_db is available
            candidate_id = 0
            if self.candidate_db:
                try:
                    candidate_id = self.candidate_db.create_candidate(
                        name=file_path.stem.replace("_", " ").replace("-", " ").title(),
                        email="",
                        source="dropbox",
                        notes=f"Auto-ingested from: {file_path.name}",
                    )
                    logger.info(
                        "Auto-created candidate #%d from file: %s",
                        candidate_id, file_path.name,
                    )
                except Exception as e:
                    logger.warning("Failed to auto-create candidate: %s", e)

            self._record_ingestion(
                file_hash=file_hash,
                file_path=str(file_path),
                file_name=file_path.name,
                status="success",
                candidate_id=candidate_id,
            )
            logger.info("Successfully processed file: %s — %s", file_path, result)

        except Exception as e:
            logger.error("Failed to process file %s: %s", file_path, e, exc_info=True)
            self.stats["failed"] += 1
            if file_hash:
                self._record_ingestion(
                    file_hash=file_hash,
                    file_path=str(file_path),
                    file_name=file_path.name,
                    status="failed",
                )


class DropboxMonitor:
    """
    Monitor a dropbox folder for new files and auto-ingest them
    into the knowledge base with deduplication and statistics.
    """

    def __init__(
        self,
        knowledge_tool: InsertKnowledgeTool,
        dropbox_path: str = "./dropbox",
        supported_extensions: Optional[Set[str]] = None,
        candidate_db: Optional[Any] = None,
        recursive: bool = True,
    ):
        """
        Initialize the dropbox monitor.

        Args:
            knowledge_tool: InsertKnowledgeTool instance.
            dropbox_path: Path to dropbox folder.
            supported_extensions: Set of supported file extensions.
            candidate_db: Optional CandidateDB for auto-profiling.
            recursive: Whether to monitor subdirectories.
        """
        self.knowledge_tool = knowledge_tool
        self.dropbox_path = Path(dropbox_path)
        self.supported_extensions = supported_extensions or {
            ".pdf", ".txt", ".docx", ".md"
        }
        self.candidate_db = candidate_db
        self.recursive = recursive

        # Ensure dropbox folder exists
        self.dropbox_path.mkdir(parents=True, exist_ok=True)

        # Setup file handler and observer
        self.event_handler = DropboxFileHandler(
            knowledge_tool=knowledge_tool,
            supported_extensions=self.supported_extensions,
            candidate_db=candidate_db,
        )
        self.observer = Observer()
        self.observer.schedule(
            self.event_handler,
            str(self.dropbox_path),
            recursive=self.recursive,
        )

        logger.info(
            "Dropbox monitor initialized: %s (recursive=%s, extensions=%s)",
            self.dropbox_path, self.recursive, self.supported_extensions,
        )

    def start(self) -> None:
        """Start monitoring the dropbox folder."""
        self.observer.start()
        logger.info("Dropbox monitor started watching: %s", self.dropbox_path)

    def stop(self) -> None:
        """Stop monitoring the dropbox folder."""
        self.observer.stop()
        self.observer.join()
        logger.info("Dropbox monitor stopped")

    def get_stats(self) -> Dict[str, int]:
        """Get ingestion statistics."""
        return dict(self.event_handler.stats)

    def ingest_existing_files(self) -> int:
        """
        Ingest any existing files in the dropbox folder.

        Returns:
            Number of files successfully ingested.
        """
        logger.info("Scanning for existing files in dropbox folder...")

        ingested_count = 0
        glob_pattern = "**/*" if self.recursive else "*"

        for file_path in self.dropbox_path.glob(glob_pattern):
            if file_path.is_file() and file_path.suffix.lower() in self.supported_extensions:
                try:
                    logger.info("Ingesting existing file: %s", file_path)
                    self.event_handler._process_file(file_path)
                    ingested_count += 1
                except Exception as e:
                    logger.error(
                        "Failed to ingest existing file %s: %s",
                        file_path, e, exc_info=True,
                    )

        logger.info("Ingested %d existing file(s)", ingested_count)
        return ingested_count

    async def run_async(self) -> None:
        """Run monitor asynchronously."""
        self.start()
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
        finally:
            self.stop()
