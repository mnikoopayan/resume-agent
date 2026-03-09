"""
Enhanced Configuration Module for Knowledge Ingestion System

Extends the sample configuration with additional settings for:
- Candidate database paths
- Gmail ingestion settings
- Analytics configuration
- Multi-agent settings
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass
class KnowledgeConfig:
    """
    Configuration for the Knowledge Base and related services.
    All settings can be overridden via environment variables.
    """

    # LanceDB configuration
    table_name: str = "knowledge_base"
    uri: str = "./knowledge/lancedb"

    # Embedder configuration (OpenRouter only)
    openrouter_model: str = "openai/text-embedding-3-small"

    # Dropbox / file monitoring configuration
    dropbox_path: str = "./dropbox"

    # LLM Model configuration
    llm_model: str = "openai/gpt-4o-mini"

    # Candidate database
    candidate_db_path: str = "./knowledge/candidates.db"

    # Gmail ingestion database
    gmail_db_path: str = "./knowledge/gmail_ingestion.db"

    # Gmail attachments download directory
    gmail_attachments_dir: str = "./dropbox/gmail"

    # Gmail sync settings
    gmail_query: str = "in:inbox"
    gmail_max_results: int = 20
    gmail_unread_only: bool = False

    # Google API credentials
    google_credentials_path: str = "./google_api_server/credentials.json"
    google_token_path: str = "./google_api_server/token.json"

    # Scoring defaults
    scoring_advance_threshold: float = 60.0

    # Supported file extensions for ingestion
    supported_extensions: List[str] = field(
        default_factory=lambda: [".pdf", ".txt", ".docx", ".md"]
    )

    @classmethod
    def from_env(cls) -> "KnowledgeConfig":
        """
        Load configuration from environment variables with sensible defaults.

        Returns:
            KnowledgeConfig populated from environment.
        """
        return cls(
            table_name=os.getenv("KNOWLEDGE_TABLE_NAME", "knowledge_base"),
            uri=os.getenv("KNOWLEDGE_URI", "./knowledge/lancedb"),
            openrouter_model=os.getenv(
                "OPENROUTER_EMBEDDER_MODEL", "openai/text-embedding-3-small"
            ),
            dropbox_path=os.getenv("DROPBOX_PATH", "./dropbox"),
            llm_model=os.getenv("LLM_MODEL", "openai/gpt-4o-mini"),
            candidate_db_path=os.getenv(
                "CANDIDATE_DB_PATH", "./knowledge/candidates.db"
            ),
            gmail_db_path=os.getenv(
                "GMAIL_DB_PATH", "./knowledge/gmail_ingestion.db"
            ),
            gmail_attachments_dir=os.getenv(
                "GMAIL_ATTACHMENTS_DIR", "./dropbox/gmail"
            ),
            gmail_query=os.getenv("GMAIL_QUERY", "in:inbox"),
            gmail_max_results=max(1, _env_int("GMAIL_MAX_RESULTS", 20)),
            gmail_unread_only=os.getenv("GMAIL_UNREAD_ONLY", "false").lower() == "true",
            google_credentials_path=os.getenv(
                "GOOGLE_CREDENTIALS_PATH", "./google_api_server/credentials.json"
            ),
            google_token_path=os.getenv(
                "GOOGLE_TOKEN_PATH", "./google_api_server/token.json"
            ),
            scoring_advance_threshold=_env_float("SCORING_ADVANCE_THRESHOLD", 60.0),
        )

    def ensure_directories(self) -> None:
        """Ensure all required directories exist."""
        Path(self.uri).mkdir(parents=True, exist_ok=True)
        Path(self.dropbox_path).mkdir(parents=True, exist_ok=True)
        Path(self.gmail_attachments_dir).mkdir(parents=True, exist_ok=True)
        Path(self.candidate_db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.gmail_db_path).parent.mkdir(parents=True, exist_ok=True)
