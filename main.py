"""
Enhanced Resume Agent — Main Entry Point

Modes:
  demo         — Run demo text/file insertion and agent queries
  interactive  — Interactive CLI with full agent access
  monitor      — Watch dropbox folder for new resumes
  gmail_sync   — Sync Gmail messages into knowledge base
  pipeline     — Manage candidate pipeline (list, add, advance)
  rank         — Rank candidates against job requirements
  schedule     — Schedule interviews via CLI
  analytics    — Print pipeline analytics report
  server       — Launch the FastAPI server

Usage:
  python main.py [mode]
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from agno.agent import Agent
from knowledge.config import KnowledgeConfig
from knowledge.setup import create_knowledge_base
from tools.knowledge_tool import InsertKnowledgeTool
from tools.candidate_db import CandidateDB
from tools.resume_scorer import ResumeScorer, JobRequirement
from tools.email_classifier import EmailClassifier
from tools.email_templates import EmailTemplateEngine
from tools.gmail_tools import SmtpGmailSender
from tools.calendar_tools import CalendarService
from tools.analytics import AnalyticsEngine
from tools.gmail_ingestion import GmailIngestionService
from ingestion.dropbox_monitor import DropboxMonitor
from agent.coordinator import create_coordinator_agent

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse boolean-like environment variable values."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    """Parse integer environment variable with fallback."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except ValueError:
        logger.warning("Invalid integer for %s=%s, using default=%s", name, value, default)
        return default


def _build_services(config: KnowledgeConfig):
    """
    Build all shared service instances.

    Returns:
        Tuple of (knowledge_base, knowledge_tool, candidate_db, scorer,
                  classifier, template_engine, smtp_sender, calendar_service, analytics).
    """
    knowledge_base = create_knowledge_base(config)
    knowledge_tool = InsertKnowledgeTool(knowledge_base)
    candidate_db = CandidateDB(db_path=config.candidate_db_path)
    scorer = ResumeScorer()
    classifier = EmailClassifier()
    template_engine = EmailTemplateEngine()
    smtp_sender = SmtpGmailSender()
    calendar_service = CalendarService(
        credentials_path=config.google_credentials_path,
        token_path=config.google_token_path,
    )
    analytics = AnalyticsEngine(db_path=config.candidate_db_path)

    return (
        knowledge_base, knowledge_tool, candidate_db, scorer,
        classifier, template_engine, smtp_sender, calendar_service, analytics,
    )


def _build_agent(config, knowledge_base, knowledge_tool, candidate_db, scorer,
                 classifier, template_engine, smtp_sender, calendar_service, analytics):
    """Build the Coordinator Agent with all tools."""
    enable_gmail = _env_bool("ENABLE_GMAIL_TOOLS", default=False)
    return create_coordinator_agent(
        knowledge_base=knowledge_base,
        knowledge_tool=knowledge_tool,
        candidate_db=candidate_db,
        scorer=scorer,
        classifier=classifier,
        template_engine=template_engine,
        smtp_sender=smtp_sender,
        calendar_service=calendar_service,
        analytics=analytics,
        enable_gmail_tools=enable_gmail,
        gmail_credentials_path=config.google_credentials_path,
        gmail_token_path=config.google_token_path,
    )


# ---------------------------------------------------------------------------
# Mode: demo
# ---------------------------------------------------------------------------
async def run_demo(knowledge_tool: InsertKnowledgeTool, agent: Agent, dropbox_path: str):
    """Run demonstration of text insertion, file ingestion, and agent queries."""
    logger.info("=" * 60)
    logger.info("DEMO 1: Inserting text into knowledge base")
    logger.info("=" * 60)

    sample_text = (
        "Python is a high-level programming language known for its simplicity and readability. "
        "It was created by Guido van Rossum and first released in 1991. "
        "Python supports multiple programming paradigms including procedural, "
        "object-oriented, and functional programming."
    )
    try:
        result = knowledge_tool.insert_knowledge(text=sample_text)
        logger.info("OK: %s", result)
    except Exception as e:
        logger.error("Failed to insert text: %s", e)

    logger.info("=" * 60)
    logger.info("DEMO 2: File ingestion from dropbox folder")
    logger.info("=" * 60)

    dropbox_dir = Path(dropbox_path)
    sample_file = dropbox_dir / "sample_document.txt"
    sample_content = (
        "Machine Learning Fundamentals\n\n"
        "Machine learning is a subset of artificial intelligence that enables systems "
        "to learn and improve from experience without being explicitly programmed.\n\n"
        "Key concepts:\n"
        "1. Supervised Learning: Learning from labeled data\n"
        "2. Unsupervised Learning: Finding patterns in unlabeled data\n"
        "3. Reinforcement Learning: Learning through interaction and rewards\n"
    )
    try:
        sample_file.write_text(sample_content)
        logger.info("Created sample file: %s", sample_file)
        result = knowledge_tool.insert_knowledge(file_path=str(sample_file))
        logger.info("OK: %s", result)
    except Exception as e:
        logger.error("Failed to ingest file: %s", e)

    logger.info("=" * 60)
    logger.info("DEMO 3: Agent querying knowledge base")
    logger.info("=" * 60)

    queries = ["What is Python?", "What are the key concepts of machine learning?"]
    for query in queries:
        logger.info("Query: %s", query)
        try:
            response = await agent.arun(query)
            logger.info("Response: %s", response.content)
        except Exception as e:
            logger.error("Query failed: %s", e)


# ---------------------------------------------------------------------------
# Mode: interactive
# ---------------------------------------------------------------------------
async def run_interactive(agent: Agent, knowledge_tool: InsertKnowledgeTool):
    """Interactive CLI with full agent access."""
    print("=" * 60)
    print("INTERACTIVE MODE — Enhanced Resume Agent")
    print("=" * 60)
    print("Commands:")
    print("  <question>        — Ask the Coordinator Agent")
    print("  insert <text>     — Insert text into knowledge base")
    print("  insert            — Multi-line input mode")
    print("  file <path>       — Ingest a file")
    print("  exit              — Quit")
    print("=" * 60)

    while True:
        try:
            user_input = input("\n> ").strip()
            if not user_input:
                continue
            if user_input.lower() == "exit":
                break

            if user_input.lower() == "insert":
                print("Multi-line input (type END on a new line to finish):")
                lines = []
                while True:
                    try:
                        line = input("  ")
                        if line.strip().upper() == "END":
                            break
                        lines.append(line)
                    except (EOFError, KeyboardInterrupt):
                        break
                if lines:
                    text = "\n".join(lines)
                    result = knowledge_tool.insert_knowledge(text=text)
                    print(f"OK: {result}")
                else:
                    print("No text provided.")

            elif user_input.startswith("insert "):
                text = user_input[7:].strip()
                if text:
                    result = knowledge_tool.insert_knowledge(text=text)
                    print(f"OK: {result}")

            elif user_input.startswith("file "):
                file_path = user_input[5:].strip()
                if file_path:
                    result = knowledge_tool.insert_knowledge(file_path=file_path)
                    print(f"OK: {result}")

            else:
                print("Querying agent...")
                response = await agent.arun(user_input)
                print(f"\n{response.content}\n")

        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error("Error: %s", e, exc_info=True)

    print("Exiting interactive mode.")


# ---------------------------------------------------------------------------
# Mode: monitor
# ---------------------------------------------------------------------------
async def run_monitor(knowledge_tool: InsertKnowledgeTool, config: KnowledgeConfig, candidate_db: CandidateDB):
    """Start dropbox folder monitor with auto-ingestion."""
    logger.info("Starting dropbox monitor...")
    monitor = DropboxMonitor(
        knowledge_tool=knowledge_tool,
        dropbox_path=config.dropbox_path,
        supported_extensions=set(config.supported_extensions),
        candidate_db=candidate_db,
        recursive=True,
    )
    monitor.ingest_existing_files()
    try:
        await monitor.run_async()
    except KeyboardInterrupt:
        logger.info("Stopping monitor...")
        monitor.stop()


# ---------------------------------------------------------------------------
# Mode: gmail_sync
# ---------------------------------------------------------------------------
def run_gmail_sync(knowledge_tool: InsertKnowledgeTool, config: KnowledgeConfig):
    """Sync Gmail messages and attachments into knowledge base."""
    sync_service = GmailIngestionService(
        knowledge_tool=knowledge_tool,
        credentials_path=config.google_credentials_path,
        token_path=config.google_token_path,
        db_path=config.gmail_db_path,
        attachments_dir=config.gmail_attachments_dir,
        query=config.gmail_query,
        max_results=config.gmail_max_results,
        unread_only=config.gmail_unread_only,
    )
    summary = sync_service.sync()
    logger.info(
        "Gmail sync: fetched=%s processed=%s skipped=%s failed=%s attachments=%s",
        summary.fetched, summary.processed, summary.skipped_existing,
        summary.failed, summary.attachments_ingested,
    )


# ---------------------------------------------------------------------------
# Mode: pipeline
# ---------------------------------------------------------------------------
def run_pipeline(candidate_db: CandidateDB):
    """Interactive candidate pipeline management."""
    print("=" * 60)
    print("CANDIDATE PIPELINE MANAGER")
    print("=" * 60)
    print("Commands: list [stage], add, advance <id> <stage>, search <query>, info <id>, exit")
    print("=" * 60)

    while True:
        try:
            cmd = input("\npipeline> ").strip()
            if not cmd:
                continue
            if cmd.lower() == "exit":
                break

            parts = cmd.split(None, 2)
            action = parts[0].lower()

            if action == "list":
                stage = parts[1].upper() if len(parts) > 1 else None
                candidates = candidate_db.list_candidates(stage=stage, limit=50)
                if not candidates:
                    print("No candidates found.")
                else:
                    print(f"\n{'ID':>4}  {'Name':<25} {'Stage':<22} {'Score':>6}  {'Source':<10}")
                    print("-" * 75)
                    for c in candidates:
                        print(f"{c['id']:>4}  {c['name']:<25} {c['stage']:<22} {c.get('score', 0):>6.1f}  {c.get('source', ''):<10}")

            elif action == "add":
                name = input("  Name: ").strip()
                email = input("  Email: ").strip()
                source = input("  Source [manual]: ").strip() or "manual"
                job = input("  Job title applied: ").strip()
                cid = candidate_db.create_candidate(
                    name=name, email=email, source=source, job_title_applied=job,
                )
                print(f"Created candidate #{cid}")

            elif action == "advance" and len(parts) >= 3:
                cid = int(parts[1])
                stage = parts[2].upper()
                candidate_db.advance_stage(cid, stage)
                print(f"Candidate #{cid} advanced to {stage}")

            elif action == "search" and len(parts) >= 2:
                query = " ".join(parts[1:])
                results = candidate_db.search_candidates(query=query, limit=20)
                if not results:
                    print("No results.")
                else:
                    for c in results:
                        print(f"  #{c['id']} {c['name']} — {c['stage']} (score: {c.get('score', 0):.1f})")

            elif action == "info" and len(parts) >= 2:
                cid = int(parts[1])
                c = candidate_db.get_candidate(cid)
                if c:
                    print(json.dumps(c, indent=2, default=str))
                else:
                    print("Candidate not found.")

            else:
                print("Unknown command. Try: list, add, advance, search, info, exit")

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")

    print("Exiting pipeline manager.")


# ---------------------------------------------------------------------------
# Mode: rank
# ---------------------------------------------------------------------------
def run_rank(candidate_db: CandidateDB, scorer: ResumeScorer):
    """Rank candidates against job requirements."""
    from workflows.candidate_ranking import CandidateRankingWorkflow

    print("=" * 60)
    print("CANDIDATE RANKING")
    print("=" * 60)

    job_title = input("Job title [Open Position]: ").strip() or "Open Position"
    required = input("Required skills (comma-separated): ").strip()
    preferred = input("Preferred skills (comma-separated): ").strip()
    min_exp = input("Min experience years [2]: ").strip()
    stage = input("Filter by stage (blank for all): ").strip()

    req = JobRequirement(
        title=job_title,
        required_skills=[s.strip() for s in required.split(",") if s.strip()] if required else [],
        preferred_skills=[s.strip() for s in preferred.split(",") if s.strip()] if preferred else [],
        min_experience_years=int(min_exp) if min_exp.isdigit() else 2,
    )

    workflow = CandidateRankingWorkflow(
        candidate_db=candidate_db,
        scorer=scorer,
        advance_threshold=float(os.getenv("SCORING_ADVANCE_THRESHOLD", "60.0")),
    )
    result = workflow.rank_candidates(
        stage=stage.upper() if stage else None,
        job_requirements=req,
    )

    print(f"\nRanked {result.scored_candidates} candidates for '{result.job_title}':")
    print(f"{'Rank':>4}  {'Name':<25} {'Score':>7}  {'Recommendation':<20}")
    print("-" * 65)
    for entry in result.rankings:
        print(
            f"{entry['rank']:>4}  {entry['name']:<25} {entry['composite_score']:>7.1f}  {entry['recommendation']:<20}"
        )

    if result.score_summary:
        s = result.score_summary
        print(f"\nSummary: mean={s['mean']}, median={s['median']}, "
              f"min={s['min']}, max={s['max']}, above threshold={s['above_threshold']}")

    if result.advanced_candidates > 0:
        print(f"Auto-advanced {result.advanced_candidates} candidates to RANKED stage.")


# ---------------------------------------------------------------------------
# Mode: analytics
# ---------------------------------------------------------------------------
def run_analytics(analytics: AnalyticsEngine):
    """Print pipeline analytics report."""
    report = analytics.full_report()
    print(json.dumps(report, indent=2, default=str))


# ---------------------------------------------------------------------------
# Mode: server
# ---------------------------------------------------------------------------
def run_server():
    """Launch the FastAPI server."""
    import uvicorn
    port = _env_int("SERVER_PORT", 8000)
    logger.info("Starting FastAPI server on port %d", port)
    uvicorn.run(
        "google_api_server.main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    """Main entry point."""
    logger.info("Starting Enhanced Resume Agent")

    config = KnowledgeConfig.from_env()
    config.ensure_directories()
    logger.info("Configuration loaded: table=%s, uri=%s", config.table_name, config.uri)

    # Parse mode
    mode = sys.argv[1] if len(sys.argv) > 1 else "demo"

    # Server mode doesn't need full initialization
    if mode == "server":
        run_server()
        return

    # Build all services
    (
        knowledge_base, knowledge_tool, candidate_db, scorer,
        classifier, template_engine, smtp_sender, calendar_service, analytics,
    ) = _build_services(config)

    if mode == "demo":
        agent = _build_agent(
            config, knowledge_base, knowledge_tool, candidate_db, scorer,
            classifier, template_engine, smtp_sender, calendar_service, analytics,
        )
        await run_demo(knowledge_tool, agent, config.dropbox_path)

    elif mode == "interactive":
        agent = _build_agent(
            config, knowledge_base, knowledge_tool, candidate_db, scorer,
            classifier, template_engine, smtp_sender, calendar_service, analytics,
        )
        await run_interactive(agent, knowledge_tool)

    elif mode == "monitor":
        await run_monitor(knowledge_tool, config, candidate_db)

    elif mode == "gmail_sync":
        run_gmail_sync(knowledge_tool, config)

    elif mode == "pipeline":
        run_pipeline(candidate_db)

    elif mode == "rank":
        run_rank(candidate_db, scorer)

    elif mode == "analytics":
        run_analytics(analytics)

    else:
        logger.error("Unknown mode: %s", mode)
        print("Available modes: demo, interactive, monitor, gmail_sync, pipeline, rank, analytics, server")
        sys.exit(1)

    logger.info("Enhanced Resume Agent stopped.")


if __name__ == "__main__":
    # Avoid nested asyncio.run() when launching uvicorn (server mode).
    mode = sys.argv[1] if len(sys.argv) > 1 else "demo"
    if mode == "server":
        run_server()
    else:
        asyncio.run(main())
