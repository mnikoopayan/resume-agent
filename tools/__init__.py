"""
Tools Module — Enhanced Resume Agent

Provides all tool implementations for the multi-agent recruitment system:
- Knowledge ingestion (text, PDF, TXT, DOCX)
- Gmail read/write tools
- Google Calendar tools
- Candidate pipeline database (SQLite CRUD)
- Resume scoring engine
- Email classification
- Email templates
- Analytics and reporting
"""
from tools.knowledge_tool import InsertKnowledgeTool, create_knowledge_insert_tools
from tools.candidate_db import CandidateDB
from tools.resume_scorer import ResumeScorer
from tools.email_classifier import EmailClassifier
from tools.email_templates import EmailTemplateEngine
from tools.analytics import AnalyticsEngine

__all__ = [
    "InsertKnowledgeTool",
    "create_knowledge_insert_tools",
    "CandidateDB",
    "ResumeScorer",
    "EmailClassifier",
    "EmailTemplateEngine",
    "AnalyticsEngine",
]
