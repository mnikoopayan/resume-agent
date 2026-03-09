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
from pydantic import BaseModel, Field, field_validator

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
from workflows.new_application import NewApplicationWorkflow
from workflows.interview_scheduling import InterviewSchedulingWorkflow
from workflows.candidate_ranking import CandidateRankingWorkflow

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


def _get_knowledge_tool() -> Optional[InsertKnowledgeTool]:
    if "knowledge_tool" not in _SINGLETONS:
        try:
            config = _get_config()
            knowledge_base = create_knowledge_base(config)
            _SINGLETONS["knowledge_tool"] = InsertKnowledgeTool(knowledge_base)
        except Exception as exc:
            logger.warning("Knowledge base unavailable for workflow endpoint: %s", exc)
            _SINGLETONS["knowledge_tool"] = None
    return _SINGLETONS["knowledge_tool"]


def _get_calendar_service() -> CalendarService:
    if "calendar_service" not in _SINGLETONS:
        _SINGLETONS["calendar_service"] = CalendarService(
            credentials_path=str(CREDENTIALS_FILE),
            token_path=str(TOKEN_FILE),
        )
    return _SINGLETONS["calendar_service"]


def _get_smtp_sender() -> SmtpGmailSender:
    if "smtp_sender" not in _SINGLETONS:
        _SINGLETONS["smtp_sender"] = SmtpGmailSender()
    return _SINGLETONS["smtp_sender"]


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
        smtp_sender = _get_smtp_sender()
        calendar_service = _get_calendar_service()

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


def _model_dump(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _parse_string_list(value: Optional[Any]) -> Optional[List[str]]:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except json.JSONDecodeError:
                pass
        return [item.strip() for item in text.split(",") if item.strip()]
    return [str(value).strip()]


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

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name cannot be empty")
        return value


class CandidateUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    score: Optional[float] = None
    stage: Optional[str] = None
    skills: Optional[List[str] | str] = None
    experience_years: Optional[int] = None
    notes: Optional[str] = None
    job_title_applied: Optional[str] = None
    source: Optional[str] = None


class CandidateAdvanceRequest(BaseModel):
    stage: str
    notes: str = ""
    changed_by: str = "api"


class ScoreRequest(BaseModel):
    skills: List[str] = Field(default_factory=list)
    experience_years: int = 0
    education: List[str] = Field(default_factory=list)
    resume_text: str = ""
    job_title: str = ""
    required_skills: List[str] = Field(default_factory=list)
    preferred_skills: List[str] = Field(default_factory=list)
    min_experience: int = 2


class ClassifyRequest(BaseModel):
    subject: str = ""
    body: str = ""
    from_email: str = ""
    from_name: str = ""
    has_attachment: bool = False


class TemplateRenderRequest(BaseModel):
    template_name: str
    variables: Dict[str, str] = Field(default_factory=dict)


class WorkflowApplicationRequest(BaseModel):
    subject: str = ""
    body: str = ""
    from_email: str = ""
    from_name: str = ""
    has_attachment: bool = False
    attachment_path: Optional[str] = None
    skills: List[str] = Field(default_factory=list)
    experience_years: int = 0
    education: List[str] = Field(default_factory=list)
    job_title: str = ""
    required_skills: List[str] = Field(default_factory=list)
    preferred_skills: List[str] = Field(default_factory=list)
    min_experience: int = 2
    dry_run: bool = True


class WorkflowScheduleRequest(BaseModel):
    candidate_id: int
    start_time: str
    end_time: Optional[str] = None
    position: str = ""
    location: str = ""
    interview_format: Optional[str] = None
    send_invitation: bool = False
    dry_run: bool = True


class WorkflowRankingRequest(BaseModel):
    stage: Optional[str] = None
    limit: int = Field(default=50, ge=1, le=200)
    auto_advance: bool = False
    advance_threshold: float = 60.0
    job_title: str = ""
    required_skills: List[str] = Field(default_factory=list)
    preferred_skills: List[str] = Field(default_factory=list)
    min_experience: int = 2


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
    return RedirectResponse(url="/chat-ui")


@app.get("/health", include_in_schema=False)
def health():
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
            "workflow_application": "POST /api/workflow/application",
            "workflow_schedule": "POST /api/workflow/schedule",
            "workflow_rank": "POST /api/workflow/rank",
        },
    }


@app.get("/auth")
def auth():
    if not CREDENTIALS_FILE.exists():
        raise HTTPException(
            status_code=500,
            detail=(
                "credentials.json not found. Download it from Google Cloud Console. "
                "See GOOGLE_CALENDAR_GMAIL_SETUP.md for steps."
            ),
        )

    from google_auth_oauthlib.flow import Flow

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
    if error:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="Missing 'code' in callback.")

    from google_auth_oauthlib.flow import Flow

    flow = None
    if state:
        with _OAUTH_STATE_LOCK:
            flow = _OAUTH_FLOW_CACHE.pop(state, None)

    if flow is None:
        flow_kwargs: Dict[str, Any] = {
            "scopes": SCOPES,
            "redirect_uri": REDIRECT_URI,
        }
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
            _attach_code_verifier(flow, code_verifier)

        if state and state in cache:
            cache.pop(state, None)
            _save_oauth_state_cache(cache)

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
# Calendar API
# ---------------------------------------------------------------------------
@app.get("/calendar/events")
def calendar_events(max_results: int = Query(default=10, ge=1, le=100)):
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
# Gmail API
# ---------------------------------------------------------------------------
@app.get("/gmail/messages")
def gmail_messages(max_results: int = Query(default=10, ge=1, le=100)):
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
# Chat endpoint
# ---------------------------------------------------------------------------
@app.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest):
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
# Candidate API
# ---------------------------------------------------------------------------
@app.get("/api/candidates")
def list_candidates(
    stage: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(default=50, le=200),
):
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
    db = _get_candidate_db()
    candidate = db.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found.")
    return candidate


@app.post("/api/candidates")
def create_candidate(body: CandidateCreate):
    db = _get_candidate_db()
    cid = db.create_candidate(
        name=body.name, email=body.email, phone=body.phone,
        source=body.source, job_title_applied=body.job_title_applied,
        notes=body.notes,
    )
    return {"candidate_id": cid, "message": "Candidate created."}


@app.patch("/api/candidates/{candidate_id}")
def update_candidate(candidate_id: int, body: CandidateUpdate):
    db = _get_candidate_db()
    if not db.get_candidate(candidate_id):
        raise HTTPException(status_code=404, detail="Candidate not found.")

    payload = {k: v for k, v in _model_dump(body).items() if v is not None}
    requested_stage = payload.pop("stage", None)
    if "skills" in payload:
        payload["skills"] = _parse_string_list(payload["skills"])

    updated = False
    if payload:
        updated = db.update_candidate(candidate_id=candidate_id, **payload)
        if not updated:
            raise HTTPException(status_code=400, detail="No supported fields to update.")

    stage_result = None
    if requested_stage:
        try:
            stage_result = db.advance_stage(candidate_id, requested_stage, changed_by="api")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not payload and stage_result is None:
        raise HTTPException(status_code=400, detail="No fields to update.")

    return {
        "message": "Candidate updated.",
        "candidate_id": candidate_id,
        "updated_fields": sorted(payload.keys()),
        "stage_transition": stage_result,
    }


@app.post("/api/candidates/{candidate_id}/advance")
def advance_candidate(candidate_id: int, body: CandidateAdvanceRequest):
    db = _get_candidate_db()
    try:
        result = db.advance_stage(
            candidate_id,
            body.stage,
            changed_by=body.changed_by,
            notes=body.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": f"Candidate advanced to {body.stage}.", "candidate_id": candidate_id, "transition": result}


# ---------------------------------------------------------------------------
# Scoring API
# ---------------------------------------------------------------------------
@app.post("/api/score")
def score_candidate(body: ScoreRequest):
    scorer = _get_scorer()
    req = JobRequirement(
        title=body.job_title or "Open Position",
        required_skills=body.required_skills,
        preferred_skills=body.preferred_skills,
        min_experience_years=body.min_experience,
    )
    return scorer.score_candidate(
        candidate_skills=body.skills,
        experience_years=body.experience_years,
        education_entries=body.education,
        resume_text=body.resume_text,
        requirements=req,
    )


# ---------------------------------------------------------------------------
# Classification API
# ---------------------------------------------------------------------------
@app.post("/api/classify")
def classify_email(body: ClassifyRequest):
    classifier = _get_classifier()
    result = classifier.classify(
        subject=body.subject, body=body.body,
        from_email=body.from_email, from_name=body.from_name,
        has_attachment=body.has_attachment,
    )
    return result.to_dict()


# ---------------------------------------------------------------------------
# Template API
# ---------------------------------------------------------------------------
@app.get("/api/templates")
def list_templates():
    engine = _get_template_engine()
    return {"templates": [engine.get_template_info(name) for name in engine.list_templates()]}


@app.post("/api/templates/render")
def render_template(body: TemplateRenderRequest):
    engine = _get_template_engine()
    try:
        return engine.render(body.template_name, body.variables)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Workflow API
# ---------------------------------------------------------------------------
@app.post("/api/workflow/application")
def workflow_application(body: WorkflowApplicationRequest):
    req = JobRequirement(
        title=body.job_title or "Open Position",
        required_skills=body.required_skills,
        preferred_skills=body.preferred_skills,
        min_experience_years=body.min_experience,
    )
    workflow = NewApplicationWorkflow(
        candidate_db=_get_candidate_db(),
        classifier=_get_classifier(),
        scorer=_get_scorer(),
        template_engine=_get_template_engine(),
        smtp_sender=_get_smtp_sender(),
        knowledge_tool=_get_knowledge_tool(),
        default_job_requirements=req,
        auto_send_acknowledgment=not body.dry_run,
    )
    result = workflow.process(
        subject=body.subject,
        body=body.body,
        from_email=body.from_email,
        from_name=body.from_name,
        has_attachment=body.has_attachment,
        attachment_path=body.attachment_path,
        skills=body.skills,
        experience_years=body.experience_years,
        education=body.education,
        job_requirements=req,
        dry_run=body.dry_run,
    )
    payload = result.to_dict()
    payload["dry_run"] = body.dry_run
    return payload


@app.post("/api/workflow/schedule")
def workflow_schedule(body: WorkflowScheduleRequest):
    workflow = InterviewSchedulingWorkflow(
        candidate_db=_get_candidate_db(),
        calendar_service=_get_calendar_service(),
        template_engine=_get_template_engine(),
        smtp_sender=_get_smtp_sender(),
    )
    result = workflow.schedule(
        candidate_id=body.candidate_id,
        start_time=body.start_time,
        end_time=body.end_time,
        position=body.position,
        location=body.location,
        interview_format=body.interview_format,
        send_invitation=body.send_invitation and not body.dry_run,
        dry_run=body.dry_run,
    )
    payload = result.to_dict()
    payload["dry_run"] = body.dry_run
    return payload


@app.post("/api/workflow/rank")
def workflow_rank(body: WorkflowRankingRequest):
    req = JobRequirement(
        title=body.job_title or "Open Position",
        required_skills=body.required_skills,
        preferred_skills=body.preferred_skills,
        min_experience_years=body.min_experience,
    )
    workflow = CandidateRankingWorkflow(
        candidate_db=_get_candidate_db(),
        scorer=_get_scorer(),
        advance_threshold=body.advance_threshold,
        auto_advance=body.auto_advance,
    )
    return workflow.rank_candidates(
        stage=body.stage,
        job_requirements=req,
        limit=body.limit,
    ).to_dict()


# ---------------------------------------------------------------------------
# Analytics API
# ---------------------------------------------------------------------------
@app.get("/api/analytics")
def get_analytics():
    return _get_analytics().pipeline_stats()


@app.get("/api/analytics/report")
def get_full_report():
    return _get_analytics().full_report()


@app.get("/api/analytics/top-candidates")
def get_top_candidates(limit: int = Query(default=10, le=50)):
    return {"candidates": _get_analytics().top_candidates(limit=limit)}


# ---------------------------------------------------------------------------
# Enhanced Chat UI
# ---------------------------------------------------------------------------
@app.get("/chat-ui", response_class=HTMLResponse)
def chat_ui():
    return HTMLResponse(content=_enhanced_chat_ui_html())


def _enhanced_chat_ui_html() -> str:
    return """<!doctype html>
<html lang='en'>
<head>
  <meta charset='utf-8' />
  <meta name='viewport' content='width=device-width, initial-scale=1' />
  <title>Resume Agent Dashboard</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #07111f;
      --bg-soft: #0b1728;
      --panel: rgba(14, 24, 40, 0.88);
      --panel-strong: #101b2f;
      --panel-elevated: #14243e;
      --border: rgba(148, 163, 184, 0.16);
      --border-strong: rgba(96, 165, 250, 0.24);
      --text: #e5eefc;
      --muted: #94a3b8;
      --subtle: #c9d7ee;
      --brand: #6ea8fe;
      --brand-strong: #4f8df7;
      --accent: #22c55e;
      --warning: #f59e0b;
      --danger: #f87171;
      --shadow: 0 24px 60px rgba(2, 8, 23, 0.45);
      --radius-lg: 24px;
      --radius-md: 18px;
      --radius-sm: 12px;
    }

    * { box-sizing: border-box; }
    html, body { min-height: 100%; }
    body {
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background:
        radial-gradient(circle at top left, rgba(59, 130, 246, 0.18), transparent 30%),
        radial-gradient(circle at top right, rgba(14, 165, 233, 0.14), transparent 24%),
        linear-gradient(180deg, #08111f 0%, #0a1424 45%, #08111d 100%);
      color: var(--text);
      padding: 32px;
    }

    .app-shell {
      max-width: 1380px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr);
      gap: 24px;
      align-items: start;
    }

    .sidebar,
    .panel,
    .hero {
      backdrop-filter: blur(18px);
      background: var(--panel);
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
    }

    .sidebar {
      position: sticky;
      top: 24px;
      padding: 24px;
      border-radius: var(--radius-lg);
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 14px;
      margin-bottom: 28px;
    }

    .brand-mark {
      width: 44px;
      height: 44px;
      border-radius: 14px;
      background: linear-gradient(135deg, var(--brand), #8b5cf6);
      display: grid;
      place-items: center;
      color: white;
      font-weight: 800;
      letter-spacing: 0.04em;
      box-shadow: 0 12px 30px rgba(110, 168, 254, 0.32);
    }

    .brand-title { font-size: 1rem; font-weight: 700; }
    .brand-subtitle { color: var(--muted); font-size: 0.92rem; margin-top: 4px; }

    .nav-group { margin-top: 24px; }
    .nav-label {
      color: var(--muted);
      font-size: 0.74rem;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      margin-bottom: 12px;
    }

    .nav-button {
      width: 100%;
      border: 1px solid transparent;
      background: transparent;
      color: var(--subtle);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 12px 14px;
      border-radius: 14px;
      cursor: pointer;
      margin-bottom: 8px;
      font-size: 0.95rem;
      transition: 160ms ease;
    }

    .nav-button:hover,
    .nav-button.active {
      background: rgba(96, 165, 250, 0.12);
      border-color: rgba(96, 165, 250, 0.2);
      color: white;
    }

    .nav-button span:last-child {
      color: var(--muted);
      font-size: 0.78rem;
    }

    .sidebar-footer {
      margin-top: 26px;
      padding: 16px;
      background: rgba(15, 23, 42, 0.7);
      border: 1px solid var(--border);
      border-radius: 16px;
    }

    .sidebar-footer strong {
      display: block;
      margin-bottom: 8px;
      font-size: 0.92rem;
    }

    .sidebar-footer p {
      margin: 0;
      color: var(--muted);
      font-size: 0.88rem;
      line-height: 1.5;
    }

    .content {
      display: grid;
      gap: 20px;
    }

    .hero {
      padding: 26px;
      border-radius: var(--radius-lg);
      display: grid;
      grid-template-columns: minmax(0, 1.3fr) minmax(280px, 0.8fr);
      gap: 18px;
      align-items: stretch;
    }

    .eyebrow {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-size: 0.82rem;
      color: #bfdbfe;
      background: rgba(59, 130, 246, 0.12);
      border: 1px solid rgba(96, 165, 250, 0.18);
      border-radius: 999px;
      padding: 8px 12px;
      margin-bottom: 16px;
    }

    .hero h1 {
      margin: 0;
      font-size: clamp(2rem, 3vw, 3rem);
      line-height: 1.05;
      letter-spacing: -0.04em;
    }

    .hero p {
      margin: 14px 0 0;
      color: var(--muted);
      max-width: 720px;
      line-height: 1.7;
      font-size: 1rem;
    }

    .hero-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 22px;
    }

    .btn,
    button,
    input,
    textarea,
    select {
      font: inherit;
    }

    .btn,
    button {
      border: none;
      border-radius: 14px;
      padding: 12px 16px;
      cursor: pointer;
      transition: transform 160ms ease, box-shadow 160ms ease, background 160ms ease;
    }

    .btn-primary,
    button.btn-primary {
      background: linear-gradient(135deg, var(--brand), var(--brand-strong));
      color: #061120;
      font-weight: 700;
      box-shadow: 0 16px 34px rgba(59, 130, 246, 0.32);
    }

    .btn-secondary,
    button.btn-secondary {
      background: rgba(148, 163, 184, 0.1);
      border: 1px solid rgba(148, 163, 184, 0.14);
      color: var(--text);
    }

    .btn:hover,
    button:hover { transform: translateY(-1px); }
    .btn:disabled,
    button:disabled {
      opacity: 0.65;
      cursor: wait;
      transform: none;
    }

    .hero-side {
      background: linear-gradient(180deg, rgba(17, 27, 46, 0.88), rgba(12, 19, 33, 0.92));
      border: 1px solid var(--border);
      border-radius: 22px;
      padding: 20px;
      display: grid;
      gap: 14px;
      align-content: start;
    }

    .mini-metric {
      padding: 14px 16px;
      border-radius: 18px;
      background: rgba(96, 165, 250, 0.08);
      border: 1px solid rgba(96, 165, 250, 0.12);
    }

    .mini-metric-label { color: var(--muted); font-size: 0.83rem; }
    .mini-metric-value { font-size: 1.5rem; font-weight: 800; margin-top: 4px; }

    .summary-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 16px;
    }

    .stat-card,
    .panel {
      border-radius: 22px;
    }

    .stat-card {
      padding: 18px;
      background: linear-gradient(180deg, rgba(17, 27, 46, 0.88), rgba(12, 19, 33, 0.92));
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
    }

    .stat-label { color: var(--muted); font-size: 0.84rem; }
    .stat-value { font-size: 1.9rem; font-weight: 800; margin-top: 8px; }
    .stat-footnote { color: var(--muted); font-size: 0.84rem; margin-top: 8px; }

    .panel {
      padding: 22px;
    }

    .panel-header {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      margin-bottom: 18px;
    }

    .panel-title h2,
    .panel-title h3 {
      margin: 0;
      font-size: 1.15rem;
      letter-spacing: -0.02em;
    }

    .panel-title p {
      margin: 6px 0 0;
      color: var(--muted);
      font-size: 0.92rem;
    }

    .panel-grid {
      display: grid;
      grid-template-columns: 1.15fr 0.85fr;
      gap: 20px;
    }

    .field-row,
    .search-row {
      display: grid;
      gap: 12px;
    }

    .field-row.two,
    .search-row {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    label {
      display: block;
      margin-bottom: 8px;
      color: var(--subtle);
      font-size: 0.87rem;
      font-weight: 600;
    }

    input,
    textarea,
    select {
      width: 100%;
      background: rgba(8, 15, 28, 0.78);
      color: var(--text);
      border: 1px solid rgba(148, 163, 184, 0.14);
      border-radius: 14px;
      padding: 13px 14px;
      outline: none;
      transition: border-color 160ms ease, box-shadow 160ms ease, background 160ms ease;
    }

    textarea { min-height: 150px; resize: vertical; line-height: 1.6; }
    input::placeholder,
    textarea::placeholder { color: #64748b; }
    input:focus,
    textarea:focus,
    select:focus {
      border-color: var(--brand);
      box-shadow: 0 0 0 4px rgba(110, 168, 254, 0.12);
      background: rgba(10, 18, 33, 0.92);
    }

    .chat-layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 320px;
      gap: 18px;
    }

    .chat-log {
      min-height: 420px;
      max-height: 620px;
      overflow: auto;
      display: grid;
      gap: 14px;
      padding-right: 4px;
    }

    .message {
      border-radius: 18px;
      padding: 16px 18px;
      border: 1px solid var(--border);
      line-height: 1.65;
      white-space: pre-wrap;
      word-break: break-word;
      background: rgba(8, 15, 28, 0.76);
    }

    .message.user {
      background: linear-gradient(180deg, rgba(59, 130, 246, 0.16), rgba(59, 130, 246, 0.09));
      border-color: rgba(96, 165, 250, 0.22);
    }

    .message.agent {
      background: rgba(15, 23, 42, 0.74);
    }

    .message-meta {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
      font-size: 0.8rem;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    .aside-card {
      padding: 18px;
      border-radius: 18px;
      background: rgba(8, 15, 28, 0.65);
      border: 1px solid var(--border);
      margin-bottom: 14px;
    }

    .aside-card h4 {
      margin: 0 0 10px;
      font-size: 0.96rem;
    }

    .aside-card p,
    .aside-card li {
      color: var(--muted);
      line-height: 1.55;
      font-size: 0.9rem;
    }

    .chip-row,
    .filter-row,
    .action-row {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }

    .chip {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border-radius: 999px;
      padding: 8px 12px;
      background: rgba(148, 163, 184, 0.08);
      border: 1px solid rgba(148, 163, 184, 0.1);
      color: var(--subtle);
      font-size: 0.86rem;
    }

    .table-wrap {
      overflow: auto;
      border-radius: 18px;
      border: 1px solid var(--border);
      background: rgba(8, 15, 28, 0.55);
    }

    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 780px;
    }

    th,
    td {
      padding: 14px 16px;
      text-align: left;
      border-bottom: 1px solid rgba(148, 163, 184, 0.08);
      vertical-align: top;
    }

    th {
      color: var(--muted);
      font-size: 0.79rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      background: rgba(15, 23, 42, 0.8);
      position: sticky;
      top: 0;
    }

    tr:hover td {
      background: rgba(96, 165, 250, 0.04);
    }

    .stage-pill,
    .score-pill,
    .pill {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 30px;
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 0.8rem;
      font-weight: 700;
      letter-spacing: 0.03em;
    }

    .stage-pill { background: rgba(96, 165, 250, 0.12); color: #bfdbfe; }
    .score-pill.high { background: rgba(34, 197, 94, 0.14); color: #86efac; }
    .score-pill.medium { background: rgba(245, 158, 11, 0.14); color: #fcd34d; }
    .score-pill.low { background: rgba(248, 113, 113, 0.14); color: #fca5a5; }

    .analytics-grid {
      display: grid;
      grid-template-columns: 1.2fr 0.8fr;
      gap: 18px;
    }

    .stack { display: grid; gap: 14px; }

    .bar-list { display: grid; gap: 12px; }
    .bar-item { display: grid; gap: 8px; }
    .bar-meta {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      color: var(--subtle);
      font-size: 0.9rem;
    }

    .bar-track {
      width: 100%;
      height: 10px;
      border-radius: 999px;
      overflow: hidden;
      background: rgba(148, 163, 184, 0.12);
    }

    .bar-fill {
      height: 100%;
      border-radius: 999px;
      background: linear-gradient(90deg, var(--brand), #8b5cf6);
    }

    .empty-state,
    .loading-state,
    .error-state {
      padding: 26px;
      border: 1px dashed rgba(148, 163, 184, 0.18);
      border-radius: 18px;
      text-align: center;
      color: var(--muted);
      background: rgba(8, 15, 28, 0.45);
    }

    .loading-state::before {
      content: '';
      display: block;
      width: 36px;
      height: 36px;
      border-radius: 50%;
      margin: 0 auto 14px;
      border: 3px solid rgba(148, 163, 184, 0.18);
      border-top-color: var(--brand);
      animation: spin 0.9s linear infinite;
    }

    .error-state {
      border-style: solid;
      border-color: rgba(248, 113, 113, 0.22);
      color: #fecaca;
      background: rgba(127, 29, 29, 0.16);
    }

    .raw-output {
      margin-top: 16px;
      padding: 16px;
      border-radius: 16px;
      background: rgba(8, 15, 28, 0.7);
      border: 1px solid var(--border);
      max-height: 320px;
      overflow: auto;
      white-space: pre-wrap;
      color: #cbd5e1;
      font-size: 0.85rem;
      line-height: 1.6;
    }

    .hidden { display: none !important; }
    .tab-panel { display: none; }
    .tab-panel.active { display: block; }

    @keyframes spin {
      to { transform: rotate(360deg); }
    }

    @media (max-width: 1180px) {
      .app-shell,
      .hero,
      .panel-grid,
      .chat-layout,
      .analytics-grid,
      .summary-grid {
        grid-template-columns: 1fr;
      }

      .sidebar {
        position: static;
      }
    }

    @media (max-width: 720px) {
      body { padding: 18px; }
      .sidebar, .panel, .hero, .stat-card { border-radius: 18px; }
      .field-row.two, .search-row { grid-template-columns: 1fr; }
      .hero-actions, .filter-row, .action-row { flex-direction: column; align-items: stretch; }
      table { min-width: 640px; }
    }
  </style>
</head>
<body>
  <div class='app-shell'>
    <aside class='sidebar'>
      <div class='brand'>
        <div class='brand-mark'>RA</div>
        <div>
          <div class='brand-title'>Resume Agent</div>
          <div class='brand-subtitle'>Recruiting operations cockpit</div>
        </div>
      </div>

      <div class='nav-group'>
        <div class='nav-label'>Workspace</div>
        <button class='nav-button active' data-tab-target='chatTab'>
          <span>AI recruiter chat</span><span>Live</span>
        </button>
        <button class='nav-button' data-tab-target='pipelineTab'>
          <span>Candidate pipeline</span><span>Board</span>
        </button>
        <button class='nav-button' data-tab-target='analyticsTab'>
          <span>Analytics overview</span><span>Insights</span>
        </button>
      </div>

      <div class='nav-group'>
        <div class='nav-label'>Quick actions</div>
        <div class='chip-row'>
          <span class='chip'>/chat</span>
          <span class='chip'>/api/candidates</span>
          <span class='chip'>/api/analytics/report</span>
        </div>
      </div>

      <div class='sidebar-footer'>
        <strong>What changed</strong>
        <p>Cleaner information hierarchy, visual metrics, real content sections, and friendlier loading and empty states without changing your backend routes.</p>
      </div>
    </aside>

    <main class='content'>
      <section class='hero'>
        <div>
          <div class='eyebrow'>Modern recruiting workspace</div>
          <h1>Run chat, pipeline, and analytics from one polished dashboard.</h1>
          <p>Use the agent for recruiting tasks, inspect the candidate funnel, and review performance trends in a UI that feels closer to a modern SaaS product than a raw API console.</p>
          <div class='hero-actions'>
            <button class='btn btn-primary' type='button' onclick='focusPrompt()'>Start with chat</button>
            <button class='btn btn-secondary' type='button' onclick='refreshAll()'>Refresh all data</button>
          </div>
        </div>

        <div class='hero-side'>
          <div class='mini-metric'>
            <div class='mini-metric-label'>System status</div>
            <div class='mini-metric-value' id='systemStatus'>Ready</div>
          </div>
          <div class='mini-metric'>
            <div class='mini-metric-label'>Candidates loaded</div>
            <div class='mini-metric-value' id='candidateCount'>—</div>
          </div>
          <div class='mini-metric'>
            <div class='mini-metric-label'>Top funnel stage</div>
            <div class='mini-metric-value' id='topStage'>—</div>
          </div>
        </div>
      </section>

      <section class='summary-grid'>
        <div class='stat-card'>
          <div class='stat-label'>Total candidates</div>
          <div class='stat-value' id='statCandidates'>—</div>
          <div class='stat-footnote'>Updated from the candidate API</div>
        </div>
        <div class='stat-card'>
          <div class='stat-label'>Average score</div>
          <div class='stat-value' id='statAverageScore'>—</div>
          <div class='stat-footnote'>Across currently loaded candidates</div>
        </div>
        <div class='stat-card'>
          <div class='stat-label'>Active stages</div>
          <div class='stat-value' id='statStages'>—</div>
          <div class='stat-footnote'>Distinct stages in the current view</div>
        </div>
        <div class='stat-card'>
          <div class='stat-label'>Last refresh</div>
          <div class='stat-value' id='statRefresh'>Never</div>
          <div class='stat-footnote'>Keeps the dashboard grounded in live data</div>
        </div>
      </section>

      <section class='panel'>
        <div class='panel-header'>
          <div class='panel-title'>
            <h2>Operations workspace</h2>
            <p>Switch between agent chat, candidate pipeline, and analytics without losing context.</p>
          </div>
          <div class='chip-row'>
            <span class='chip'>Single-file UI</span>
            <span class='chip'>No backend contract changes</span>
            <span class='chip'>FastAPI compatible</span>
          </div>
        </div>

        <div id='chatTab' class='tab-panel active'>
          <div class='chat-layout'>
            <div>
              <div class='field-row'>
                <div>
                  <label for='prompt'>Ask the agent</label>
                  <textarea id='prompt' placeholder='Example: Summarize the strongest backend candidates and suggest who should move to interview.'></textarea>
                </div>
              </div>
              <div class='action-row' style='margin-top: 14px;'>
                <button id='sendChatButton' class='btn btn-primary' type='button' onclick='sendChat()'>Send message</button>
                <button class='btn btn-secondary' type='button' onclick='usePromptExample(`Find the top three candidates for a senior ML role and explain why.`)'>Use example prompt</button>
                <button class='btn btn-secondary' type='button' onclick='clearChat()'>Clear conversation</button>
              </div>
              <div class='panel' style='padding:18px; margin-top:18px; background: rgba(8, 15, 28, 0.38); border:1px solid var(--border);'>
                <div class='panel-header' style='margin-bottom:12px;'>
                  <div class='panel-title'>
                    <h3>Conversation feed</h3>
                    <p>Responses render as chat bubbles instead of raw JSON blocks.</p>
                  </div>
                </div>
                <div id='chatLog' class='chat-log'></div>
              </div>
            </div>

            <div>
              <div class='aside-card'>
                <h4>Recommended prompts</h4>
                <ul>
                  <li>Who should we prioritize for screening this week?</li>
                  <li>Draft an interview plan for shortlisted candidates.</li>
                  <li>Summarize the pipeline risk areas and bottlenecks.</li>
                </ul>
              </div>
              <div class='aside-card'>
                <h4>Chat status</h4>
                <p id='chatStatus'>Ready for your first prompt.</p>
              </div>
              <div class='aside-card'>
                <h4>Raw API response</h4>
                <div id='chatRaw' class='raw-output'>No response yet.</div>
              </div>
            </div>
          </div>
        </div>

        <div id='pipelineTab' class='tab-panel'>
          <div class='panel-grid'>
            <div>
              <div class='panel-header'>
                <div class='panel-title'>
                  <h3>Candidate pipeline</h3>
                  <p>Filter by stage and inspect the current hiring funnel in a readable table.</p>
                </div>
              </div>
              <div class='search-row'>
                <div>
                  <label for='stage'>Stage filter</label>
                  <input id='stage' placeholder='Optional stage filter, e.g. SCREENING' />
                </div>
                <div>
                  <label for='candidateLimit'>Limit</label>
                  <select id='candidateLimit'>
                    <option value='25'>25</option>
                    <option value='50' selected>50</option>
                    <option value='100'>100</option>
                    <option value='200'>200</option>
                  </select>
                </div>
              </div>
              <div class='action-row' style='margin-top:14px;'>
                <button id='loadCandidatesButton' class='btn btn-primary' type='button' onclick='loadCandidates()'>Load candidates</button>
                <button class='btn btn-secondary' type='button' onclick='clearStageFilter()'>Clear filter</button>
              </div>
              <div id='pipelineContent' style='margin-top:18px;'></div>
            </div>

            <div class='stack'>
              <div class='aside-card'>
                <h4>Pipeline summary</h4>
                <div id='pipelineSummary' class='bar-list'></div>
              </div>
              <div class='aside-card'>
                <h4>Current view notes</h4>
                <p id='pipelineStatus'>Load candidates to see stage distribution, top scores, and candidate details.</p>
              </div>
            </div>
          </div>
        </div>

        <div id='analyticsTab' class='tab-panel'>
          <div class='panel-header'>
            <div class='panel-title'>
              <h3>Analytics overview</h3>
              <p>Turn the analytics report into digestible metrics, top-stage distribution, and a readable JSON fallback.</p>
            </div>
            <button id='loadAnalyticsButton' class='btn btn-primary' type='button' onclick='loadAnalytics()'>Refresh analytics</button>
          </div>
          <div class='analytics-grid'>
            <div class='stack'>
              <div class='aside-card'>
                <h4>Stage distribution</h4>
                <div id='analyticsBars' class='bar-list'></div>
              </div>
              <div class='aside-card'>
                <h4>Highlights</h4>
                <div id='analyticsHighlights' class='chip-row'></div>
              </div>
            </div>
            <div class='stack'>
              <div class='aside-card'>
                <h4>Report payload</h4>
                <div id='analyticsRaw' class='raw-output'>No analytics loaded yet.</div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </main>
  </div>

  <script>
    const state = {
      chatHistory: [],
      candidates: [],
      analytics: null,
    };

    function setActiveTab(tabId) {
      document.querySelectorAll('.tab-panel').forEach((panel) => {
        panel.classList.toggle('active', panel.id === tabId);
      });
      document.querySelectorAll('.nav-button').forEach((button) => {
        button.classList.toggle('active', button.dataset.tabTarget === tabId);
      });
    }

    document.querySelectorAll('[data-tab-target]').forEach((button) => {
      button.addEventListener('click', () => setActiveTab(button.dataset.tabTarget));
    });

    function focusPrompt() {
      setActiveTab('chatTab');
      const prompt = document.getElementById('prompt');
      prompt.focus();
      prompt.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    function usePromptExample(text) {
      setActiveTab('chatTab');
      const prompt = document.getElementById('prompt');
      prompt.value = text;
      prompt.focus();
    }

    function clearChat() {
      state.chatHistory = [];
      renderChatLog();
      document.getElementById('chatRaw').textContent = 'No response yet.';
      document.getElementById('chatStatus').textContent = 'Conversation cleared.';
    }

    function setSystemStatus(text) {
      document.getElementById('systemStatus').textContent = text;
    }

    function formatTimestamp(date = new Date()) {
      return new Intl.DateTimeFormat([], {
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit'
      }).format(date);
    }

    function updateRefreshStamp() {
      document.getElementById('statRefresh').textContent = formatTimestamp();
    }

    function renderChatLog() {
      const chatLog = document.getElementById('chatLog');
      if (!state.chatHistory.length) {
        chatLog.innerHTML = "<div class='empty-state'>Start a conversation with the recruiting agent. Replies will appear here as a readable timeline.</div>";
        return;
      }

      chatLog.innerHTML = state.chatHistory.map((item) => `
        <div class='message ${item.role}'>
          <div class='message-meta'>
            <span>${item.role === 'user' ? 'You' : 'Resume Agent'}</span>
            <span>${item.time}</span>
          </div>
          <div>${escapeHtml(item.text)}</div>
        </div>
      `).join('');
      chatLog.scrollTop = chatLog.scrollHeight;
    }

    function escapeHtml(text) {
      return String(text)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
    }

    async function fetchJson(url, options = {}) {
      const response = await fetch(url, options);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const message = data.detail || data.message || `Request failed with status ${response.status}`;
        throw new Error(message);
      }
      return data;
    }

    async function sendChat() {
      const promptEl = document.getElementById('prompt');
      const prompt = promptEl.value.trim();
      if (!prompt) {
        document.getElementById('chatStatus').textContent = 'Enter a message to start the conversation.';
        promptEl.focus();
        return;
      }

      const sendButton = document.getElementById('sendChatButton');
      sendButton.disabled = true;
      setSystemStatus('Thinking…');
      document.getElementById('chatStatus').textContent = 'Contacting the agent…';

      state.chatHistory.push({ role: 'user', text: prompt, time: formatTimestamp() });
      renderChatLog();

      try {
        const data = await fetchJson('/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: prompt })
        });

        state.chatHistory.push({ role: 'agent', text: data.answer || '(empty response)', time: formatTimestamp() });
        document.getElementById('chatRaw').textContent = JSON.stringify(data, null, 2);
        document.getElementById('chatStatus').textContent = 'Reply received.';
        promptEl.value = '';
      } catch (error) {
        state.chatHistory.push({ role: 'agent', text: `Error: ${error.message}`, time: formatTimestamp() });
        document.getElementById('chatRaw').textContent = error.message;
        document.getElementById('chatStatus').textContent = 'Chat request failed.';
      } finally {
        sendButton.disabled = false;
        setSystemStatus('Ready');
        renderChatLog();
        updateRefreshStamp();
      }
    }

    function scoreClass(score) {
      const numeric = Number(score);
      if (!Number.isFinite(numeric)) return 'low';
      if (numeric >= 75) return 'high';
      if (numeric >= 50) return 'medium';
      return 'low';
    }

    function summarizeCandidates(candidates) {
      const count = candidates.length;
      const scores = candidates.map((candidate) => Number(candidate.score)).filter(Number.isFinite);
      const avgScore = scores.length ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length) : null;
      const stages = {};
      candidates.forEach((candidate) => {
        const stage = candidate.stage || 'UNKNOWN';
        stages[stage] = (stages[stage] || 0) + 1;
      });
      const topStageEntry = Object.entries(stages).sort((a, b) => b[1] - a[1])[0];
      return { count, avgScore, stages, topStageEntry };
    }

    function renderPipeline(candidates) {
      const content = document.getElementById('pipelineContent');
      const summary = document.getElementById('pipelineSummary');
      const status = document.getElementById('pipelineStatus');

      if (!candidates.length) {
        content.innerHTML = "<div class='empty-state'>No candidates matched this filter. Try another stage or remove the filter.</div>";
        summary.innerHTML = "<div class='empty-state'>Stage distribution will appear here once candidates are loaded.</div>";
        status.textContent = 'No candidates found in the current view.';
        document.getElementById('candidateCount').textContent = '0';
        document.getElementById('topStage').textContent = '—';
        document.getElementById('statCandidates').textContent = '0';
        document.getElementById('statAverageScore').textContent = '—';
        document.getElementById('statStages').textContent = '0';
        return;
      }

      const stats = summarizeCandidates(candidates);
      const maxStageCount = Math.max(...Object.values(stats.stages));

      summary.innerHTML = Object.entries(stats.stages)
        .sort((a, b) => b[1] - a[1])
        .map(([stage, count]) => `
          <div class='bar-item'>
            <div class='bar-meta'><span>${escapeHtml(stage)}</span><span>${count}</span></div>
            <div class='bar-track'><div class='bar-fill' style='width:${(count / maxStageCount) * 100}%;'></div></div>
          </div>
        `).join('');

      content.innerHTML = `
        <div class='table-wrap'>
          <table>
            <thead>
              <tr>
                <th>Candidate</th>
                <th>Stage</th>
                <th>Score</th>
                <th>Role</th>
                <th>Source</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              ${candidates.map((candidate) => `
                <tr>
                  <td>
                    <strong>${escapeHtml(candidate.name || 'Unnamed')}</strong><br />
                    <span style='color: var(--muted);'>${escapeHtml(candidate.email || 'No email')}</span>
                  </td>
                  <td><span class='stage-pill'>${escapeHtml(candidate.stage || 'UNKNOWN')}</span></td>
                  <td><span class='score-pill ${scoreClass(candidate.score)}'>${candidate.score ?? '—'}</span></td>
                  <td>${escapeHtml(candidate.job_title_applied || '—')}</td>
                  <td>${escapeHtml(candidate.source || '—')}</td>
                  <td>${escapeHtml(candidate.notes || '—').slice(0, 180)}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      `;

      status.textContent = `${stats.count} candidates loaded. ${stats.topStageEntry ? `${stats.topStageEntry[0]} is currently the largest stage.` : 'Stage mix unavailable.'}`;
      document.getElementById('candidateCount').textContent = String(stats.count);
      document.getElementById('topStage').textContent = stats.topStageEntry ? stats.topStageEntry[0] : '—';
      document.getElementById('statCandidates').textContent = String(stats.count);
      document.getElementById('statAverageScore').textContent = stats.avgScore !== null ? `${stats.avgScore}` : '—';
      document.getElementById('statStages').textContent = String(Object.keys(stats.stages).length);
    }

    async function loadCandidates() {
      const stage = document.getElementById('stage').value.trim();
      const limit = document.getElementById('candidateLimit').value;
      const loadButton = document.getElementById('loadCandidatesButton');
      const content = document.getElementById('pipelineContent');

      setActiveTab('pipelineTab');
      loadButton.disabled = true;
      setSystemStatus('Loading pipeline…');
      content.innerHTML = "<div class='loading-state'>Fetching candidates from the pipeline API…</div>";

      try {
        const query = new URLSearchParams();
        if (stage) query.set('stage', stage);
        if (limit) query.set('limit', limit);
        const data = await fetchJson(`/api/candidates?${query.toString()}`);
        state.candidates = Array.isArray(data.candidates) ? data.candidates : [];
        renderPipeline(state.candidates);
      } catch (error) {
        content.innerHTML = `<div class='error-state'>${escapeHtml(error.message)}</div>`;
        document.getElementById('pipelineSummary').innerHTML = `<div class='error-state'>Unable to compute stage summary.</div>`;
        document.getElementById('pipelineStatus').textContent = 'Pipeline request failed.';
      } finally {
        loadButton.disabled = false;
        setSystemStatus('Ready');
        updateRefreshStamp();
      }
    }

    function clearStageFilter() {
      document.getElementById('stage').value = '';
      loadCandidates();
    }

    function findStageBreakdown(payload) {
      if (!payload || typeof payload !== 'object') return [];
      const candidates = [];
      for (const [key, value] of Object.entries(payload)) {
        if (value && typeof value === 'object' && !Array.isArray(value)) {
          const normalized = Object.entries(value)
            .filter(([, count]) => typeof count === 'number')
            .map(([label, count]) => ({ label, count, source: key }));
          if (normalized.length >= 2) {
            return normalized;
          }
        }
      }
      return candidates;
    }

    function collectNumericHighlights(payload, prefix = '') {
      const results = [];
      if (!payload || typeof payload !== 'object') return results;
      for (const [key, value] of Object.entries(payload)) {
        const label = prefix ? `${prefix}.${key}` : key;
        if (typeof value === 'number' && Number.isFinite(value)) {
          results.push({ label, value });
        } else if (value && typeof value === 'object' && !Array.isArray(value)) {
          results.push(...collectNumericHighlights(value, label));
        }
      }
      return results;
    }

    function renderAnalytics(report) {
      const bars = document.getElementById('analyticsBars');
      const highlights = document.getElementById('analyticsHighlights');
      const raw = document.getElementById('analyticsRaw');
      raw.textContent = JSON.stringify(report, null, 2);

      const stageBreakdown = findStageBreakdown(report);
      if (stageBreakdown.length) {
        const maxCount = Math.max(...stageBreakdown.map((item) => item.count));
        bars.innerHTML = stageBreakdown
          .sort((a, b) => b.count - a.count)
          .map((item) => `
            <div class='bar-item'>
              <div class='bar-meta'><span>${escapeHtml(item.label)}</span><span>${item.count}</span></div>
              <div class='bar-track'><div class='bar-fill' style='width:${(item.count / maxCount) * 100}%;'></div></div>
            </div>
          `).join('');
      } else {
        bars.innerHTML = "<div class='empty-state'>No stage breakdown was found in the analytics payload, so the raw report is shown on the right.</div>";
      }

      const numericHighlights = collectNumericHighlights(report)
        .sort((a, b) => b.value - a.value)
        .slice(0, 8);
      if (numericHighlights.length) {
        highlights.innerHTML = numericHighlights
          .map((item) => `<span class='chip'>${escapeHtml(item.label)}: ${item.value}</span>`)
          .join('');
      } else {
        highlights.innerHTML = "<div class='empty-state'>No numeric highlights found in the analytics report.</div>";
      }
    }

    async function loadAnalytics() {
      const button = document.getElementById('loadAnalyticsButton');
      const bars = document.getElementById('analyticsBars');
      const highlights = document.getElementById('analyticsHighlights');
      setActiveTab('analyticsTab');
      button.disabled = true;
      setSystemStatus('Loading analytics…');
      bars.innerHTML = "<div class='loading-state'>Building analytics overview…</div>";
      highlights.innerHTML = '';

      try {
        const data = await fetchJson('/api/analytics/report');
        state.analytics = data;
        renderAnalytics(data);
      } catch (error) {
        bars.innerHTML = `<div class='error-state'>${escapeHtml(error.message)}</div>`;
        highlights.innerHTML = `<div class='error-state'>Analytics request failed.</div>`;
        document.getElementById('analyticsRaw').textContent = error.message;
      } finally {
        button.disabled = false;
        setSystemStatus('Ready');
        updateRefreshStamp();
      }
    }

    async function refreshAll() {
      await Promise.allSettled([loadCandidates(), loadAnalytics()]);
    }

    document.getElementById('prompt').addEventListener('keydown', (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
        sendChat();
      }
    });

    renderChatLog();
    loadCandidates();
    loadAnalytics();
  </script>
</body>
</html>
"""
