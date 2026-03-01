# Enhanced Resume Agent

A production-grade, multi-agent recruitment pipeline built with the **Agno** framework and **OpenRouter** LLM integration. This system automates resume ingestion, candidate profiling, scoring, interview scheduling, email communication, and pipeline analytics through a team of specialized AI agents coordinated by a central orchestrator.

## Architecture Overview

The system follows a multi-agent architecture where a **Coordinator Agent** routes requests to specialized sub-agents, each responsible for a distinct domain of the recruitment workflow.

| Agent | Responsibility |
|-------|---------------|
| **Coordinator** | Routes requests to the appropriate specialist agent based on intent classification |
| **Resume Analyzer** | Parses resumes, extracts structured data (skills, experience, education), generates candidate profiles |
| **Interview Scheduler** | Manages Google Calendar integration, detects scheduling conflicts, proposes time slots |
| **Candidate Ranker** | Applies weighted scoring against configurable job requirements, produces ranked lists |
| **Email Composer** | Drafts and sends professional recruitment emails using customizable templates via Gmail API (OAuth) |
| **Pipeline Manager** | Manages the full candidate lifecycle from NEW through HIRED/REJECTED with stage tracking |

## Key Features

**Multi-Agent Orchestration** — Six specialized agents coordinated by a central router, each with domain-specific tools and instructions. The Coordinator analyzes incoming requests and delegates to the most appropriate specialist.

**Candidate Pipeline Database** — Full SQLite-backed candidate lifecycle management with stages (NEW, SCREENING, INTERVIEW_SCHEDULED, INTERVIEWED, RANKED, OFFERED, HIRED, REJECTED), audit logging, and search capabilities.

**Advanced Resume Scoring** — Weighted scoring engine with configurable job requirements. Evaluates candidates across four dimensions: skills match (40%), experience (25%), education (20%), and keyword relevance (15%). Produces composite scores with detailed breakdowns and recommendations.

**Email Classification** — Rule-based email classifier that categorizes incoming messages into recruitment-relevant categories (APPLICATION, INTERVIEW_REQUEST, FOLLOW_UP, REFERRAL, etc.) with confidence scores and priority levels.

**Email Templates** — Professional, customizable email templates for common recruitment communications including acknowledgments, interview invitations, rejections, offers, and follow-ups. All templates support variable substitution.

**Google Calendar Integration** — Full calendar management including event creation, conflict detection, and available slot discovery for interview scheduling.

**Gmail Integration** — Read and send via Gmail API (OAuth). Includes idempotent message sync with SQLite deduplication and attachment extraction. The web UI’s read-only tools use a separate token file so they don’t overwrite full-scope credentials.

**Pipeline Analytics** — Comprehensive reporting engine with stage distribution, scoring statistics, source analysis, time-to-hire metrics, and top candidate rankings.

**Knowledge Base** — LanceDB vector store with OpenRouter embeddings for semantic search across ingested resumes, emails, and documents. Supports PDF, TXT, DOCX, and Markdown formats.

**Dropbox Monitoring** — Watchdog-based file system monitor that auto-ingests new documents dropped into a watched folder, with SHA-256 deduplication and optional candidate auto-creation.

**Enhanced Web Dashboard** — FastAPI server with a tabbed browser UI featuring Chat (conversational agent access), Pipeline (candidate table with filtering), and Analytics (statistics and charts) views.

## Project Structure

```
resume-agent/
├── agent/                          # Multi-agent definitions
│   ├── coordinator.py              # Central routing agent
│   ├── resume_analyzer.py          # Resume parsing and profiling
│   ├── interview_scheduler.py      # Calendar and scheduling
│   ├── candidate_ranker.py         # Scoring and ranking
│   ├── email_composer.py           # Email drafting and sending
│   └── pipeline_manager.py         # Candidate lifecycle management
├── google_api_server/              # FastAPI server
│   ├── main.py                     # Enhanced server with all endpoints
│   ├── requirements.txt            # Server-specific dependencies
│   └── README.md                   # Server documentation
├── ingestion/                      # File ingestion
│   └── dropbox_monitor.py          # Enhanced folder watcher
├── knowledge/                      # Knowledge base
│   ├── config.py                   # Configuration management
│   └── setup.py                    # LanceDB initialization
├── tools/                          # Tool implementations
│   ├── knowledge_tool.py           # Vector store operations
│   ├── gmail_tools.py              # Gmail read + send via Gmail API (OAuth); read-only token isolation
│   ├── gmail_ingestion.py          # Gmail sync service
│   ├── calendar_tools.py           # Google Calendar operations
│   ├── candidate_db.py             # SQLite candidate pipeline
│   ├── resume_scorer.py            # Weighted scoring engine
│   ├── email_classifier.py         # Email categorization
│   ├── email_templates.py          # Template rendering
│   └── analytics.py                # Pipeline analytics
├── workflows/                      # Multi-step workflows
│   ├── new_application.py          # New application processing
│   ├── interview_scheduling.py     # Interview scheduling
│   └── candidate_ranking.py        # Batch ranking
├── main.py                         # CLI entry point
├── batch_upload.py                 # Batch file upload
├── .env                            # Environment configuration
├── config.example.env              # Example configuration
├── requirements.txt                # Python dependencies
├── GOOGLE_CALENDAR_GMAIL_SETUP.md  # Google API setup guide
└── DROPBOX_USAGE.md                # Dropbox monitor guide
```

## Installation

### Prerequisites

Python 3.10 or later is required. The project uses the Agno framework with OpenRouter for LLM access.

### Setup

```bash
# Clone or extract the project
cd resume-agent

# Create and activate a virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp config.example.env .env
# Edit .env with your credentials (already configured if using the provided .env)
```

### Google API Setup

For Gmail and Calendar integration, follow the detailed instructions in `GOOGLE_CALENDAR_GMAIL_SETUP.md`. In summary, you need to create a Google Cloud project, enable the Gmail and Calendar APIs, create OAuth 2.0 credentials, and download the `credentials.json` file to `google_api_server/`.


**OAuth scopes used by this project**

- Gmail read: `https://www.googleapis.com/auth/gmail.readonly`
- Gmail send: `https://www.googleapis.com/auth/gmail.send`
- Calendar: `https://www.googleapis.com/auth/calendar`

After authorizing once at `/auth`, the server stores credentials in `google_api_server/token.json`. The web dashboard’s read-only Gmail widgets may use `google_api_server/token_readonly.json` to avoid overwriting the full-scope token.


## Usage

### CLI Modes

The `main.py` script supports multiple operational modes:

```bash
# Demo mode — insert sample data and query the agent
python main.py demo

# Interactive mode — conversational CLI with the Coordinator Agent
python main.py interactive

# Monitor mode — watch the dropbox folder for new files
python main.py monitor

# Gmail sync — sync Gmail messages into the knowledge base
python main.py gmail_sync

# Pipeline management — interactive candidate pipeline CLI
python main.py pipeline

# Rank candidates — score and rank against job requirements
python main.py rank

# Analytics — print pipeline analytics report
python main.py analytics

# Server — launch the FastAPI web server
python main.py server
```

### Batch Upload

Upload all supported files from a directory:

```bash
# Basic upload
python batch_upload.py ./resumes/

# With candidate auto-creation and recursive scanning
python batch_upload.py ./resumes/ --create-candidates --recursive
```

### Web Dashboard

Start the FastAPI server and open the browser:

```bash
python main.py server
# Then open http://localhost:8000 (redirects to /chat-ui)
# Or open http://localhost:8000/chat-ui directly
```

The dashboard provides three tabs: **Chat** for conversational agent interaction, **Pipeline** for visual candidate management, and **Analytics** for pipeline statistics.

## Configuration

All settings are configurable via environment variables in the `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | — | OpenRouter API key for LLM and embeddings |
| `LLM_MODEL` | `openai/gpt-4o-mini` | Default LLM model via OpenRouter |
| `KNOWLEDGE_TABLE_NAME` | `knowledge_base` | LanceDB table name |
| `KNOWLEDGE_URI` | `./knowledge/lancedb` | LanceDB storage path |
| `DROPBOX_PATH` | `./dropbox` | Watched folder for file ingestion |
| `ENABLE_GMAIL_TOOLS` | `false` | Enable Gmail tools (Gmail API) |
| `GOOGLE_TOKEN_PATH` | `./google_api_server/token.json` | OAuth token used for full-scope Gmail+Calendar actions |
| `GOOGLE_READONLY_TOKEN_PATH` | `./google_api_server/token_readonly.json` | OAuth token used for read-only Gmail actions in the web UI (prevents scope overwrites) |
| `SCORING_ADVANCE_THRESHOLD` | `60.0` | Minimum score for auto-advancement |
| `SERVER_PORT` | `8000` | FastAPI server port |

## API Reference

The FastAPI server exposes a comprehensive REST API. Full endpoint documentation is available at `http://localhost:8000/docs` (Swagger UI) when the server is running. Key endpoint groups include candidate CRUD operations, scoring, email classification, template rendering, analytics, and the chat interface.

## Technology Stack

| Component | Technology |
|-----------|-----------|
| LLM Framework | Agno |
| LLM Provider | OpenRouter (GPT-4o-mini) |
| Embeddings | OpenAI text-embedding-3-small via OpenRouter |
| Vector Store | LanceDB |
| Database | SQLite (candidates, Gmail sync, ingestion state) |
| Web Server | FastAPI + Uvicorn |
| Email | Gmail API (OAuth) — read + send |
| Calendar | Google Calendar API |
| File Monitoring | Watchdog |
| Reranking | Cohere (optional) |

## License

This project is provided as-is for educational and demonstration purposes.
