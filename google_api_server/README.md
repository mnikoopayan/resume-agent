# Google API Server

Enhanced FastAPI server providing REST API access to the full recruitment pipeline.

## Quick Start

```bash
cd google_api_server
uvicorn main:app --reload --port 8000
```

Then open http://localhost:8000/auth to authorize Google Calendar and Gmail access.

## Endpoints

### OAuth
- `GET /auth` — Start Google OAuth flow
- `GET /callback` — OAuth callback handler
- `GET /success` — Post-authorization confirmation

### Google APIs
- `GET /calendar/events` — List upcoming calendar events
- `GET /gmail/messages` — List recent Gmail messages
- `GET /gmail/messages/{id}` — Get a specific message

### Chat
- `POST /chat` — Chat with the Coordinator Agent
- `GET /chat-ui` — Enhanced browser-based chat dashboard

### Candidate Pipeline
- `GET /api/candidates` — List candidates (filter by stage or search)
- `GET /api/candidates/{id}` — Get candidate details
- `POST /api/candidates` — Create a new candidate
- `PATCH /api/candidates/{id}` — Update candidate fields
- `POST /api/candidates/{id}/advance` — Advance pipeline stage

### Scoring & Classification
- `POST /api/score` — Score a candidate against job requirements
- `POST /api/classify` — Classify an email into recruitment categories

### Email Templates
- `GET /api/templates` — List available templates
- `POST /api/templates/render` — Render a template with variables

### Analytics
- `GET /api/analytics` — Pipeline statistics
- `GET /api/analytics/report` — Full analytics report
- `GET /api/analytics/top-candidates` — Top-scored candidates

## Chat UI

The enhanced chat UI at `/chat-ui` features a tabbed interface:
- **Chat** — Conversational interface with the Coordinator Agent
- **Pipeline** — Visual candidate pipeline with filtering and search
- **Analytics** — Dashboard with pipeline statistics and charts
