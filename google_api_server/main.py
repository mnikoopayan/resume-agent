"""
Enhanced FastAPI Server for Google Calendar, Gmail, and Resume Agent

Preserves all sample endpoints and adds:
- /api/candidates — CRUD for candidate pipeline
- /api/candidates/{id}/advance — Stage advancement
- /api/analytics — Pipeline analytics
- /api/analytics/report — Full analytics report
- /api/score — Score a candidate
- /api/classify — Classify an email
- /api/templates — List email templates
- /api/templates/render — Render a template
- /api/workflow/application — Run new application workflow
- /api/workflow/schedule — Schedule interview workflow
- /api/workflow/rank — Rank candidates workflow
- Enhanced chat UI with tabbed interface (Chat, Pipeline, Analytics)

Run: uvicorn google_api_server.main:app --reload --port 8000
Then open http://localhost:8000/auth to start OAuth (first time only).
"""
import json
import logging
import os
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config: paths relative to this file
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

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
from agent.coordinator import create_coordinator_agent

# Scopes for Calendar (full) and Gmail (read)
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

REDIRECT_URI = "http://localhost:8000/callback"

app = FastAPI(
    title="Resume Agent API Server",
    description=(
        "Multi-agent recruitment pipeline with Google Calendar, Gmail, "
        "candidate management, scoring, analytics, and chat."
    ),
    version="2.0.0",
)

# ---------------------------------------------------------------------------
# Lazy-initialized singletons
# ---------------------------------------------------------------------------
_SINGLETONS: Dict[str, Any] = {}

# ---------------------------------------------------------------------------
# OAuth PKCE state cache
#
# Newer google-auth-oauthlib / requests-oauthlib flows may use PKCE.
# If PKCE is used, Google expects `code_verifier` in the token exchange step.
# Because /auth and /callback are separate HTTP requests, we persist the
# code_verifier keyed by `state` (in-memory + on-disk) so /callback can reuse it.
# ---------------------------------------------------------------------------
_OAUTH_FLOW_CACHE: Dict[str, Any] = {}
_OAUTH_STATE_LOCK = threading.Lock()
_OAUTH_STATE_CACHE_FILE = BASE_DIR / ".oauth_state_cache.json"
_OAUTH_STATE_TTL_SECONDS = 20 * 60  # 20 minutes


def _load_oauth_state_cache() -> Dict[str, Any]:
    try:
        if not _OAUTH_STATE_CACHE_FILE.exists():
            return {}
        return json.loads(_OAUTH_STATE_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_oauth_state_cache(cache: Dict[str, Any]) -> None:
    try:
        tmp = _OAUTH_STATE_CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        tmp.replace(_OAUTH_STATE_CACHE_FILE)
    except Exception:
        # Best-effort only (local dev server)
        pass


def _prune_oauth_state_cache(cache: Dict[str, Any]) -> Dict[str, Any]:
    now = time.time()
    pruned: Dict[str, Any] = {}
    for k, v in (cache or {}).items():
        try:
            created_at = float((v or {}).get("created_at", 0))
        except Exception:
            created_at = 0.0
        if created_at and (now - created_at) <= _OAUTH_STATE_TTL_SECONDS:
            pruned[k] = v
    return pruned


def _get_flow_code_verifier(flow: Any) -> Optional[str]:
    # google-auth-oauthlib may expose it as flow.code_verifier, while requests-oauthlib
    # keeps it at flow.oauth2session._client.code_verifier
    try:
        cv = getattr(flow, "code_verifier", None)
        if cv:
            return str(cv)
    except Exception:
        pass
    try:
        cv = flow.oauth2session._client.code_verifier
        if cv:
            return str(cv)
    except Exception:
        pass
    return None


def _attach_code_verifier(flow: Any, code_verifier: str) -> None:
    # Attach verifier in both likely places to satisfy requests-oauthlib
    try:
        setattr(flow, "code_verifier", code_verifier)
    except Exception:
        pass
    try:
        flow.oauth2session._client.code_verifier = code_verifier
    except Exception:
        pass



def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_project_path(value: str) -> str:
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((PROJECT_ROOT / path).resolve())


def _get_config() -> KnowledgeConfig:
    if "config" not in _SINGLETONS:
        config = KnowledgeConfig.from_env()
        config.uri = _resolve_project_path(config.uri)
        config.dropbox_path = _resolve_project_path(config.dropbox_path)
        config.candidate_db_path = _resolve_project_path(config.candidate_db_path)
        config.gmail_db_path = _resolve_project_path(config.gmail_db_path)
        config.ensure_directories()
        _SINGLETONS["config"] = config
    return _SINGLETONS["config"]


def _get_candidate_db() -> CandidateDB:
    if "candidate_db" not in _SINGLETONS:
        config = _get_config()
        _SINGLETONS["candidate_db"] = CandidateDB(db_path=config.candidate_db_path)
    return _SINGLETONS["candidate_db"]


def _get_scorer() -> ResumeScorer:
    if "scorer" not in _SINGLETONS:
        _SINGLETONS["scorer"] = ResumeScorer()
    return _SINGLETONS["scorer"]


def _get_classifier() -> EmailClassifier:
    if "classifier" not in _SINGLETONS:
        _SINGLETONS["classifier"] = EmailClassifier()
    return _SINGLETONS["classifier"]


def _get_template_engine() -> EmailTemplateEngine:
    if "template_engine" not in _SINGLETONS:
        _SINGLETONS["template_engine"] = EmailTemplateEngine()
    return _SINGLETONS["template_engine"]


def _get_analytics() -> AnalyticsEngine:
    if "analytics" not in _SINGLETONS:
        config = _get_config()
        _SINGLETONS["analytics"] = AnalyticsEngine(db_path=config.candidate_db_path)
    return _SINGLETONS["analytics"]


def _get_chat_agent():
    if "chat_agent" not in _SINGLETONS:
        config = _get_config()
        knowledge_base = create_knowledge_base(config)
        knowledge_tool = InsertKnowledgeTool(knowledge_base)
        candidate_db = _get_candidate_db()
        scorer = _get_scorer()
        classifier = _get_classifier()
        template_engine = _get_template_engine()
        analytics = _get_analytics()
        smtp_sender = SmtpGmailSender()
        calendar_service = CalendarService(
            credentials_path=str(CREDENTIALS_FILE),
            token_path=str(TOKEN_FILE),
        )

        enable_gmail = _env_bool("ENABLE_GMAIL_TOOLS", default=True)
        agent = create_coordinator_agent(
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
            gmail_credentials_path=str(CREDENTIALS_FILE),
            gmail_token_path=str(TOKEN_FILE),
        )
        _SINGLETONS["chat_agent"] = agent
        logger.info("Coordinator agent initialized. Gmail=%s", enable_gmail)
    return _SINGLETONS["chat_agent"]


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    answer: str

class CandidateCreate(BaseModel):
    name: str
    email: str = ""
    phone: str = ""
    source: str = "api"
    job_title_applied: str = ""
    notes: str = ""

class CandidateUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    score: Optional[float] = None
    stage: Optional[str] = None
    skills: Optional[str] = None
    experience_years: Optional[int] = None
    notes: Optional[str] = None

class ScoreRequest(BaseModel):
    skills: List[str] = []
    experience_years: int = 0
    education: List[str] = []
    resume_text: str = ""
    job_title: str = ""
    required_skills: List[str] = []
    preferred_skills: List[str] = []
    min_experience: int = 2

class ClassifyRequest(BaseModel):
    subject: str = ""
    body: str = ""
    from_email: str = ""
    from_name: str = ""
    has_attachment: bool = False

class TemplateRenderRequest(BaseModel):
    template_name: str
    variables: Dict[str, str] = {}


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------
def get_credentials():
    """Load credentials from token.json; return None if not yet authorized."""
    if not TOKEN_FILE.exists():
        return None
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_FILE, "w") as f:
                f.write(creds.to_json())
        return creds
    except Exception:
        return None


def require_credentials():
    creds = get_credentials()
    if not creds:
        raise HTTPException(
            status_code=401,
            detail="Not authorized. Open http://localhost:8000/auth in your browser first.",
        )
    return creds


# ---------------------------------------------------------------------------
# Root and OAuth routes
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def root():
    """Redirect browser users to the dashboard UI."""
    return RedirectResponse(url="/chat-ui")


@app.get("/health", include_in_schema=False)
def health():
    """Health and usage info (machine-friendly)."""
    has_token = TOKEN_FILE.exists()
    return {
        "service": "Resume Agent API Server",
        "version": "2.0.0",
        "status": "running",
        "authorized": has_token,
        "endpoints": {
            "auth": "/auth",
            "calendar_events": "/calendar/events",
            "gmail_messages": "/gmail/messages",
            "chat": "POST /chat",
            "chat_ui": "/chat-ui",
            "candidates": "/api/candidates",
            "analytics": "/api/analytics",
            "score": "POST /api/score",
            "classify": "POST /api/classify",
            "templates": "/api/templates",
        },
    }


@app.get("/auth")
def auth():
    """Redirect user to Google OAuth consent screen."""
    if not CREDENTIALS_FILE.exists():
        raise HTTPException(
            status_code=500,
            detail=(
                "credentials.json not found. Download it from Google Cloud Console. "
                "See GOOGLE_CALENDAR_GMAIL_SETUP.md for steps."
            ),
        )

    from google_auth_oauthlib.flow import Flow

    # NOTE: Recent google-auth-oauthlib versions may use PKCE (code_challenge/code_verifier).
    # We must keep the code_verifier generated during /auth and reuse it during /callback.
    flow = Flow.from_client_secrets_file(
        str(CREDENTIALS_FILE),
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )

    # Cache PKCE verifier (best-effort).
    code_verifier = _get_flow_code_verifier(flow)

    if state:
        with _OAUTH_STATE_LOCK:
            _OAUTH_FLOW_CACHE[state] = flow
            cache = _prune_oauth_state_cache(_load_oauth_state_cache())
            cache[state] = {"code_verifier": code_verifier, "created_at": time.time()}
            _save_oauth_state_cache(cache)

    return RedirectResponse(url=authorization_url)


@app.get("/callback")
def callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    """Handle OAuth callback from Google."""
    if error:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="Missing 'code' in callback.")

    from google_auth_oauthlib.flow import Flow

    # Prefer the in-memory flow (keeps PKCE verifier automatically).
    flow = None
    if state:
        with _OAUTH_STATE_LOCK:
            flow = _OAUTH_FLOW_CACHE.pop(state, None)

    # Fallback: recreate flow and re-attach PKCE code_verifier from disk cache.
    if flow is None:
        flow_kwargs: Dict[str, Any] = {
            "scopes": SCOPES,
            "redirect_uri": REDIRECT_URI,
        }
        # `state` is passed through to requests-oauthlib/OAuth2Session.
        if state:
            flow_kwargs["state"] = state

        flow = Flow.from_client_secrets_file(str(CREDENTIALS_FILE), **flow_kwargs)

        code_verifier = None
        with _OAUTH_STATE_LOCK:
            cache = _prune_oauth_state_cache(_load_oauth_state_cache())
            _save_oauth_state_cache(cache)
        if state and state in cache:
            entry = cache.get(state) or {}
            code_verifier = entry.get("code_verifier")

        if state and not code_verifier:
            raise HTTPException(
                status_code=400,
                detail=(
                    "OAuth PKCE state was not found (missing code_verifier). "
                    "Please restart the flow by opening http://localhost:8000/auth again. "
                    "Tip: keep the server running during the OAuth step (avoid restarts while using --reload)."
                ),
            )

        if code_verifier:
            # Attach verifier where requests-oauthlib expects it.
            try:
                setattr(flow, "code_verifier", code_verifier)
            except Exception:
                pass
            try:
                flow.oauth2session._client.code_verifier = code_verifier
            except Exception:
                pass

        # Remove used state from disk cache.
        if state and state in cache:
            cache.pop(state, None)
            _save_oauth_state_cache(cache)

    # Exchange the authorization code for tokens.
    flow.fetch_token(code=code)

    creds = flow.credentials
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f, indent=2)

    return RedirectResponse(url="/success")


@app.get("/success")
def success():
    return {"message": "Authorization successful.", "try": "/calendar/events or /gmail/messages"}


# ---------------------------------------------------------------------------
# Calendar API (preserved from sample)
# ---------------------------------------------------------------------------
@app.get("/calendar/events")
def calendar_events(max_results: int = 10):
    """List upcoming events from the primary calendar."""
    creds = require_credentials()
    try:
        from googleapiclient.discovery import build
        service = build("calendar", "v3", credentials=creds)
        now = datetime.now(timezone.utc).isoformat()
        events_result = (
            service.events()
            .list(calendarId="primary", timeMin=now, maxResults=max_results,
                  singleEvents=True, orderBy="startTime")
            .execute()
        )
        events = events_result.get("items", [])
        return {"calendar": "primary", "events": events, "count": len(events)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Gmail API (preserved from sample)
# ---------------------------------------------------------------------------
@app.get("/gmail/messages")
def gmail_messages(max_results: int = 10):
    """List recent messages from the inbox."""
    creds = require_credentials()
    try:
        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=creds)
        results = (
            service.users().messages().list(userId="me", maxResults=max_results).execute()
        )
        messages = results.get("messages", [])
        return {"userId": "me", "message_ids": [m["id"] for m in messages], "count": len(messages)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/gmail/messages/{message_id}")
def gmail_message(message_id: str):
    """Get one message by ID."""
    creds = require_credentials()
    try:
        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=creds)
        msg = service.users().messages().get(userId="me", id=message_id).execute()
        payload = msg.get("payload", {})
        headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
        return {
            "id": msg["id"],
            "threadId": msg.get("threadId"),
            "snippet": msg.get("snippet"),
            "subject": headers.get("Subject"),
            "from": headers.get("From"),
            "date": headers.get("Date"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Chat endpoint (enhanced)
# ---------------------------------------------------------------------------
@app.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest):
    """Chat with the Coordinator Agent."""
    prompt = payload.message.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Field 'message' cannot be empty.")
    try:
        agent = _get_chat_agent()
        response = await agent.arun(prompt)
        answer = getattr(response, "content", str(response))
        return ChatResponse(answer=answer)
    except Exception as e:
        logger.exception("Chat failed")
        raise HTTPException(status_code=500, detail=f"Agent chat failed: {e}")


# ---------------------------------------------------------------------------
# Candidate API (new)
# ---------------------------------------------------------------------------
@app.get("/api/candidates")
def list_candidates(
    stage: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(default=50, le=200),
):
    """List candidates, optionally filtered by stage or search term."""
    db = _get_candidate_db()
    if search:
        candidates = db.search_candidates(query=search, limit=limit)
    elif stage:
        candidates = db.list_candidates(stage=stage, limit=limit)
    else:
        candidates = db.list_candidates(limit=limit)
    return {"candidates": candidates, "count": len(candidates)}


@app.get("/api/candidates/{candidate_id}")
def get_candidate(candidate_id: int):
    """Get a single candidate by ID."""
    db = _get_candidate_db()
    candidate = db.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found.")
    return candidate


@app.post("/api/candidates")
def create_candidate(body: CandidateCreate):
    """Create a new candidate."""
    db = _get_candidate_db()
    cid = db.create_candidate(
        name=body.name, email=body.email, phone=body.phone,
        source=body.source, job_title_applied=body.job_title_applied,
        notes=body.notes,
    )
    return {"candidate_id": cid, "message": "Candidate created."}


@app.patch("/api/candidates/{candidate_id}")
def update_candidate(candidate_id: int, body: CandidateUpdate):
    """Update a candidate's fields."""
    db = _get_candidate_db()
    updates = {k: v for k, v in body.dict().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update.")
    db.update_candidate(candidate_id=candidate_id, **updates)
    return {"message": "Candidate updated.", "candidate_id": candidate_id}


@app.post("/api/candidates/{candidate_id}/advance")
def advance_candidate(candidate_id: int, stage: str = Query(...)):
    """Advance a candidate to a new pipeline stage."""
    db = _get_candidate_db()
    db.advance_stage(candidate_id, stage)
    return {"message": f"Candidate advanced to {stage}.", "candidate_id": candidate_id}


# ---------------------------------------------------------------------------
# Scoring API (new)
# ---------------------------------------------------------------------------
@app.post("/api/score")
def score_candidate(body: ScoreRequest):
    """Score a candidate against job requirements."""
    scorer = _get_scorer()
    req = JobRequirement(
        title=body.job_title or "Open Position",
        required_skills=body.required_skills,
        preferred_skills=body.preferred_skills,
        min_experience_years=body.min_experience,
    )
    result = scorer.score_candidate(
        candidate_skills=body.skills,
        experience_years=body.experience_years,
        education_entries=body.education,
        resume_text=body.resume_text,
        requirements=req,
    )
    return result


# ---------------------------------------------------------------------------
# Classification API (new)
# ---------------------------------------------------------------------------
@app.post("/api/classify")
def classify_email(body: ClassifyRequest):
    """Classify an email into a recruitment category."""
    classifier = _get_classifier()
    result = classifier.classify(
        subject=body.subject, body=body.body,
        from_email=body.from_email, from_name=body.from_name,
        has_attachment=body.has_attachment,
    )
    return result.to_dict()


# ---------------------------------------------------------------------------
# Template API (new)
# ---------------------------------------------------------------------------
@app.get("/api/templates")
def list_templates():
    """List available email templates."""
    engine = _get_template_engine()
    templates = []
    for name in engine.list_templates():
        info = engine.get_template_info(name)
        templates.append(info)
    return {"templates": templates}


@app.post("/api/templates/render")
def render_template(body: TemplateRenderRequest):
    """Render an email template with variables."""
    engine = _get_template_engine()
    try:
        result = engine.render(body.template_name, body.variables)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Analytics API (new)
# ---------------------------------------------------------------------------
@app.get("/api/analytics")
def get_analytics():
    """Get pipeline statistics."""
    analytics = _get_analytics()
    return analytics.pipeline_stats()


@app.get("/api/analytics/report")
def get_full_report():
    """Get comprehensive analytics report."""
    analytics = _get_analytics()
    return analytics.full_report()


@app.get("/api/analytics/top-candidates")
def get_top_candidates(limit: int = Query(default=10, le=50)):
    """Get top-scored candidates."""
    analytics = _get_analytics()
    return {"candidates": analytics.top_candidates(limit=limit)}


# ---------------------------------------------------------------------------
# Enhanced Chat UI
# ---------------------------------------------------------------------------
@app.get("/chat-ui", response_class=HTMLResponse)
def chat_ui():
    """Enhanced browser UI with tabbed interface."""
    return HTMLResponse(content=_enhanced_chat_ui_html())


def _enhanced_chat_ui_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Resume Agent — Enhanced Dashboard</title>
  <style>
    :root {
      --bg: #0b1020;
      --panel: #131a2e;
      --panel-2: #1b2440;
      --text: #edf2ff;
      --muted: #9fb0d0;
      --accent: #7aa2ff;
      --accent-hover: #5d84de;
      --user: #244a9a;
      --agent: #223055;
      --error: #9f2f2f;
      --border: #2f3f69;
      --success: #2e7d32;
      --warning: #f57c00;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: radial-gradient(1200px 500px at 20% -20%, #1f2b4d 0%, var(--bg) 50%);
      color: var(--text);
      min-height: 100vh;
    }
    .wrap { max-width: 1100px; margin: 0 auto; padding: 20px 16px 32px; }
    .header { margin-bottom: 14px; display: flex; justify-content: space-between; align-items: center; }
    .title { font-size: 22px; font-weight: 700; }
    .subtitle { color: var(--muted); font-size: 13px; margin-top: 4px; }
    .tabs { display: flex; gap: 2px; margin-bottom: 14px; }
    .tab {
      padding: 8px 18px; border-radius: 8px 8px 0 0; cursor: pointer;
      background: var(--panel); color: var(--muted); border: 1px solid var(--border);
      border-bottom: none; font-weight: 500; font-size: 14px; transition: all 0.2s;
    }
    .tab.active { background: var(--panel-2); color: var(--accent); border-color: var(--accent); }
    .tab-content { display: none; }
    .tab-content.active { display: block; }
    .panel {
      border: 1px solid var(--border);
      background: linear-gradient(180deg, var(--panel), var(--panel-2));
      border-radius: 0 14px 14px 14px; overflow: hidden;
      box-shadow: 0 18px 40px rgba(0,0,0,0.3);
    }
    #messages {
      height: 55vh; overflow-y: auto; padding: 16px;
      display: flex; flex-direction: column; gap: 10px;
    }
    .msg {
      padding: 10px 14px; border-radius: 10px; white-space: pre-wrap;
      line-height: 1.45; border: 1px solid rgba(255,255,255,0.08); font-size: 14px;
    }
    .msg.user { align-self: flex-end; background: var(--user); max-width: 80%; }
    .msg.agent { align-self: flex-start; background: var(--agent); max-width: 90%; }
    .msg.error { align-self: flex-start; background: var(--error); max-width: 90%; }
    .composer {
      border-top: 1px solid var(--border); padding: 12px;
      display: grid; grid-template-columns: 1fr auto; gap: 10px;
      background: rgba(4,9,20,0.35);
    }
    textarea {
      width: 100%; min-height: 60px; max-height: 160px; resize: vertical;
      border-radius: 10px; border: 1px solid var(--border);
      background: #0f1730; color: var(--text); padding: 10px; font: inherit; font-size: 14px;
    }
    button, .btn {
      border: 1px solid var(--accent-hover); background: var(--accent); color: #0b1430;
      border-radius: 8px; padding: 8px 16px; font-weight: 600; cursor: pointer;
      font-size: 13px; transition: opacity 0.2s;
    }
    button:disabled { opacity: 0.5; cursor: not-allowed; }
    .quick { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
    .quick button {
      background: transparent; color: var(--muted); border-color: var(--border);
      padding: 6px 10px; min-width: 0; font-size: 12px;
    }
    .quick button:hover { border-color: var(--accent); color: var(--accent); }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border); }
    th { color: var(--accent); font-weight: 600; background: rgba(0,0,0,0.2); }
    tr:hover { background: rgba(122,162,255,0.05); }
    .badge {
      display: inline-block; padding: 2px 8px; border-radius: 4px;
      font-size: 11px; font-weight: 600;
    }
    .badge-new { background: #1a237e; color: #7aa2ff; }
    .badge-screening { background: #0d47a1; color: #64b5f6; }
    .badge-interview { background: #004d40; color: #4db6ac; }
    .badge-ranked { background: #e65100; color: #ffb74d; }
    .badge-offered { background: #1b5e20; color: #81c784; }
    .badge-hired { background: var(--success); color: #c8e6c9; }
    .badge-rejected { background: #b71c1c; color: #ef9a9a; }
    .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; padding: 16px; }
    .stat-card {
      background: rgba(0,0,0,0.2); border: 1px solid var(--border);
      border-radius: 10px; padding: 16px; text-align: center;
    }
    .stat-value { font-size: 28px; font-weight: 700; color: var(--accent); }
    .stat-label { font-size: 12px; color: var(--muted); margin-top: 4px; }
    .pipeline-content, .analytics-content { padding: 16px; min-height: 400px; }
    .toolbar { display: flex; gap: 8px; margin-bottom: 12px; align-items: center; }
    .toolbar select, .toolbar input {
      background: #0f1730; color: var(--text); border: 1px solid var(--border);
      border-radius: 6px; padding: 6px 10px; font-size: 13px;
    }
    .loading { text-align: center; padding: 40px; color: var(--muted); }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="header">
      <div>
        <h1 class="title">Resume Agent Dashboard</h1>
        <p class="subtitle">Multi-agent recruitment pipeline — Chat, Pipeline, Analytics</p>
      </div>
    </div>

    <div class="tabs">
      <div class="tab active" data-tab="chat">Chat</div>
      <div class="tab" data-tab="pipeline">Pipeline</div>
      <div class="tab" data-tab="analytics">Analytics</div>
    </div>

    <!-- Chat Tab -->
    <div class="tab-content active" id="tab-chat">
      <div class="quick">
        <button data-prompt="Show latest 5 unread emails">Unread Emails</button>
        <button data-prompt="Show all candidates in the pipeline">All Candidates</button>
        <button data-prompt="Rank all candidates in SCREENING stage">Rank Candidates</button>
        <button data-prompt="Show pipeline analytics report">Analytics Report</button>
        <button data-prompt="What are the top 5 candidates by score?">Top Candidates</button>
        <button data-prompt="Search knowledge base for python developer resumes">Search Resumes</button>
      </div>
      <div class="panel">
        <div id="messages"></div>
        <form id="chat-form" class="composer">
          <textarea id="prompt" placeholder="Ask the Coordinator Agent anything..."></textarea>
          <button id="send-btn" type="submit">Send</button>
        </form>
      </div>
    </div>

    <!-- Pipeline Tab -->
    <div class="tab-content" id="tab-pipeline">
      <div class="panel">
        <div class="pipeline-content">
          <div class="toolbar">
            <select id="stage-filter">
              <option value="">All Stages</option>
              <option value="NEW">NEW</option>
              <option value="SCREENING">SCREENING</option>
              <option value="INTERVIEW_SCHEDULED">INTERVIEW_SCHEDULED</option>
              <option value="INTERVIEWED">INTERVIEWED</option>
              <option value="RANKED">RANKED</option>
              <option value="OFFERED">OFFERED</option>
              <option value="HIRED">HIRED</option>
              <option value="REJECTED">REJECTED</option>
            </select>
            <input type="text" id="search-input" placeholder="Search candidates..." />
            <button onclick="loadCandidates()">Refresh</button>
          </div>
          <div id="candidates-table"><div class="loading">Loading candidates...</div></div>
        </div>
      </div>
    </div>

    <!-- Analytics Tab -->
    <div class="tab-content" id="tab-analytics">
      <div class="panel">
        <div class="analytics-content">
          <div id="analytics-stats"><div class="loading">Loading analytics...</div></div>
          <div id="analytics-details" style="margin-top:16px;"></div>
        </div>
      </div>
    </div>
  </div>

  <script>
    // Tab switching
    document.querySelectorAll('.tab').forEach(tab => {
      tab.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
        if (tab.dataset.tab === 'pipeline') loadCandidates();
        if (tab.dataset.tab === 'analytics') loadAnalytics();
      });
    });

    // Chat
    const messages = document.getElementById("messages");
    const form = document.getElementById("chat-form");
    const input = document.getElementById("prompt");
    const sendBtn = document.getElementById("send-btn");

    function pushMessage(kind, text) {
      const div = document.createElement("div");
      div.className = "msg " + kind;
      div.textContent = text;
      messages.appendChild(div);
      messages.scrollTop = messages.scrollHeight;
    }

    async function sendPrompt(prompt) {
      const clean = prompt.trim();
      if (!clean) return;
      pushMessage("user", clean);
      sendBtn.disabled = true;
      try {
        const res = await fetch("/chat", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({message: clean})
        });
        const body = await res.json();
        if (!res.ok) throw new Error(body.detail || "Request failed");
        pushMessage("agent", body.answer || "(empty response)");
      } catch (err) {
        pushMessage("error", "Error: " + err.message);
      } finally {
        sendBtn.disabled = false;
      }
    }

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const value = input.value;
      input.value = "";
      await sendPrompt(value);
      input.focus();
    });

    document.querySelectorAll(".quick button").forEach(btn => {
      btn.addEventListener("click", () => sendPrompt(btn.dataset.prompt || ""));
    });

    pushMessage("agent", "Ready. I'm the Coordinator Agent with access to all recruitment tools. Try a quick prompt above or ask me anything.");

    // Pipeline
    function stageBadge(stage) {
      const cls = {
        'NEW': 'badge-new', 'SCREENING': 'badge-screening',
        'INTERVIEW_SCHEDULED': 'badge-interview', 'INTERVIEWED': 'badge-interview',
        'RANKED': 'badge-ranked', 'OFFERED': 'badge-offered',
        'HIRED': 'badge-hired', 'REJECTED': 'badge-rejected'
      }[stage] || 'badge-new';
      return '<span class="badge ' + cls + '">' + stage + '</span>';
    }

    async function loadCandidates() {
      const stage = document.getElementById('stage-filter').value;
      const search = document.getElementById('search-input').value;
      let url = '/api/candidates?limit=50';
      if (stage) url += '&stage=' + stage;
      if (search) url += '&search=' + encodeURIComponent(search);
      try {
        const res = await fetch(url);
        const data = await res.json();
        const candidates = data.candidates || [];
        if (!candidates.length) {
          document.getElementById('candidates-table').innerHTML = '<div class="loading">No candidates found.</div>';
          return;
        }
        let html = '<table><thead><tr><th>ID</th><th>Name</th><th>Email</th><th>Stage</th><th>Score</th><th>Source</th><th>Applied For</th></tr></thead><tbody>';
        candidates.forEach(c => {
          html += '<tr><td>' + c.id + '</td><td>' + (c.name||'') + '</td><td>' + (c.email||'') +
            '</td><td>' + stageBadge(c.stage) + '</td><td>' + (c.score||0).toFixed(1) +
            '</td><td>' + (c.source||'') + '</td><td>' + (c.job_title_applied||'') + '</td></tr>';
        });
        html += '</tbody></table>';
        document.getElementById('candidates-table').innerHTML = html;
      } catch (err) {
        document.getElementById('candidates-table').innerHTML = '<div class="loading">Error: ' + err.message + '</div>';
      }
    }

    document.getElementById('stage-filter').addEventListener('change', loadCandidates);
    document.getElementById('search-input').addEventListener('keyup', (e) => { if (e.key === 'Enter') loadCandidates(); });

    // Analytics
    async function loadAnalytics() {
      try {
        const res = await fetch('/api/analytics/report');
        const data = await res.json();
        const p = data.pipeline || {};
        const tth = data.time_to_hire || {};
        const scores = data.scores || {};

        let statsHtml = '<div class="stat-grid">';
        statsHtml += '<div class="stat-card"><div class="stat-value">' + (p.total_candidates||0) + '</div><div class="stat-label">Total Candidates</div></div>';
        statsHtml += '<div class="stat-card"><div class="stat-value">' + (p.average_score||0).toFixed(1) + '</div><div class="stat-label">Avg Score</div></div>';
        statsHtml += '<div class="stat-card"><div class="stat-value">' + (tth.average_days||0).toFixed(1) + '</div><div class="stat-label">Avg Days to Hire</div></div>';
        statsHtml += '<div class="stat-card"><div class="stat-value">' + (tth.hired_count||0) + '</div><div class="stat-label">Total Hired</div></div>';
        statsHtml += '</div>';
        document.getElementById('analytics-stats').innerHTML = statsHtml;

        // Stage breakdown
        let detailHtml = '<h3 style="color:var(--accent);margin-bottom:12px;">Pipeline Stages</h3>';
        detailHtml += '<table><thead><tr><th>Stage</th><th>Count</th></tr></thead><tbody>';
        const stages = p.stage_counts || {};
        for (const [stage, count] of Object.entries(stages)) {
          detailHtml += '<tr><td>' + stageBadge(stage) + '</td><td>' + count + '</td></tr>';
        }
        detailHtml += '</tbody></table>';

        // Top candidates
        const top = data.top_candidates || [];
        if (top.length) {
          detailHtml += '<h3 style="color:var(--accent);margin:16px 0 12px;">Top Candidates</h3>';
          detailHtml += '<table><thead><tr><th>#</th><th>Name</th><th>Score</th><th>Stage</th></tr></thead><tbody>';
          top.forEach((c, i) => {
            detailHtml += '<tr><td>' + (i+1) + '</td><td>' + c.name + '</td><td>' + c.score.toFixed(1) + '</td><td>' + stageBadge(c.stage) + '</td></tr>';
          });
          detailHtml += '</tbody></table>';
        }

        document.getElementById('analytics-details').innerHTML = detailHtml;
      } catch (err) {
        document.getElementById('analytics-stats').innerHTML = '<div class="loading">Error: ' + err.message + '</div>';
      }
    }
  </script>
</body>
</html>
"""