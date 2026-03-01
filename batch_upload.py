"""
Enhanced Batch Upload Script

Uploads all supported files (PDF, TXT, DOCX, MD) from a directory
into the knowledge base and optionally creates candidate profiles.

Usage:
  python batch_upload.py [directory] [--create-candidates] [--recursive]
"""
import asyncio
import logging
import sys
from pathlib import Path

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from knowledge.config import KnowledgeConfig
from knowledge.setup import create_knowledge_base
from tools.knowledge_tool import InsertKnowledgeTool
from tools.candidate_db import CandidateDB

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".docx", ".md"}


async def batch_upload_resumes(
    resume_dir: str,
    create_candidates: bool = False,
    recursive: bool = False,
):
    """
    Upload all supported files from a directory into the knowledge base.

    Args:
        resume_dir: Directory containing resume files.
        create_candidates: Whether to auto-create candidate profiles.
        recursive: Whether to scan subdirectories.
    """
    resume_path = Path(resume_dir)
    if not resume_path.exists():
        logger.error("Directory not found: %s", resume_dir)
        return

    # Initialize services
    config = KnowledgeConfig.from_env()
    config.ensure_directories()
    knowledge_base = create_knowledge_base(config)
    knowledge_tool = InsertKnowledgeTool(knowledge_base)

    candidate_db = None
    if create_candidates:
        candidate_db = CandidateDB(db_path=config.candidate_db_path)

    # Find all supported files
    pattern = "**/*" if recursive else "*"
    files = [
        f for f in resume_path.glob(pattern)
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not files:
        logger.warning("No supported files found in %s", resume_dir)
        return

    logger.info("Found %d file(s) to upload", len(files))
    logger.info("=" * 60)

    success_count = 0
    failed_count = 0
    candidates_created = 0

    for file_path in sorted(files):
        try:
            logger.info("Uploading: %s", file_path.name)
            result = knowledge_tool.insert_knowledge(
                file_path=str(file_path), source="batch_upload"
            )
            logger.info("OK: %s", result)
            success_count += 1

            # Auto-create candidate profile
            if candidate_db:
                try:
                    name = file_path.stem.replace("_", " ").replace("-", " ").title()
                    cid = candidate_db.create_candidate(
                        name=name,
                        email="",
                        source="batch_upload",
                        notes=f"Batch uploaded from: {file_path.name}",
                    )
                    candidates_created += 1
                    logger.info("Created candidate #%d: %s", cid, name)
                except Exception as e:
                    logger.warning("Failed to create candidate for %s: %s", file_path.name, e)

        except Exception as e:
            logger.error("Failed to upload %s: %s", file_path.name, e)
            failed_count += 1

    logger.info("=" * 60)
    logger.info(
        "Upload complete: %d successful, %d failed, %d candidates created",
        success_count, failed_count, candidates_created,
    )


if __name__ == "__main__":
    resume_dir = "./dropbox"
    create_candidates = False
    recursive = False

    args = sys.argv[1:]
    positional = []
    for arg in args:
        if arg == "--create-candidates":
            create_candidates = True
        elif arg == "--recursive":
            recursive = True
        else:
            positional.append(arg)

    if positional:
        resume_dir = positional[0]

    logger.info("Batch uploading from: %s (candidates=%s, recursive=%s)",
                resume_dir, create_candidates, recursive)
    asyncio.run(batch_upload_resumes(resume_dir, create_candidates, recursive))
