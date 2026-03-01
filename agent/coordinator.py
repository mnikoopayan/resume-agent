"""
Coordinator / Router Agent

Main agent that routes user requests to specialized sub-agents.
Also serves as the backward-compatible single-agent interface,
incorporating all tools from the sample project plus enhancements.
"""
import logging
import os
from typing import Any, Callable, List, Optional

from agno.agent import Agent
from agno.knowledge.knowledge import Knowledge
from agno.models.openrouter import OpenRouter

from tools.knowledge_tool import InsertKnowledgeTool, create_knowledge_insert_tools
from tools.candidate_db import CandidateDB, create_candidate_db_tools
from tools.resume_scorer import ResumeScorer, create_scorer_tools
from tools.email_classifier import EmailClassifier, create_classifier_tools
from tools.email_templates import EmailTemplateEngine, create_template_tools
from tools.gmail_tools import (
    create_readonly_gmail_tools,
    create_full_gmail_tools,
    SmtpGmailSender,
    create_smtp_gmail_tools,
)
from tools.calendar_tools import CalendarService, create_calendar_tools
from tools.analytics import AnalyticsEngine, create_analytics_tools

logger = logging.getLogger(__name__)

COORDINATOR_INSTRUCTIONS = [
    "You are the Coordinator Agent for an advanced multi-agent recruitment pipeline system.",
    "You have access to ALL tools across the system and can handle any recruitment task.",
    "",
    "Your capabilities include:",
    "1. RESUME ANALYSIS — Parse resumes, extract structured data, insert into knowledge base.",
    "2. CANDIDATE MANAGEMENT — Create, update, search, and advance candidates through the pipeline.",
    "3. INTERVIEW SCHEDULING — Find available slots, create calendar events, check conflicts.",
    "4. CANDIDATE RANKING — Score candidates against job requirements with weighted criteria.",
    "5. EMAIL MANAGEMENT — Read emails, classify them, compose and send professional responses.",
    "6. ANALYTICS — Pipeline statistics, time-to-hire, source tracking, score distributions.",
    "7. KNOWLEDGE BASE — Search and insert information into the semantic knowledge base.",
    "",
    "Pipeline stages: NEW → SCREENING → INTERVIEW_SCHEDULED → INTERVIEWED → RANKED → OFFERED → HIRED",
    "Any stage can transition to REJECTED.",
    "",
    "When handling requests:",
    "- Determine which capability area the request falls into.",
    "- Use the appropriate tools to fulfill the request.",
    "- Provide clear, structured responses with relevant data.",
    "- For complex workflows, break them into steps and execute sequentially.",
    "",
    "Always be professional, thorough, and data-driven in your responses.",
    "When uncertain, ask for clarification rather than making assumptions.",
]


def create_coordinator_agent(
    knowledge_base: Knowledge,
    knowledge_tool: Optional[InsertKnowledgeTool] = None,
    candidate_db: Optional[CandidateDB] = None,
    scorer: Optional[ResumeScorer] = None,
    classifier: Optional[EmailClassifier] = None,
    template_engine: Optional[EmailTemplateEngine] = None,
    smtp_sender: Optional[SmtpGmailSender] = None,
    calendar_service: Optional[CalendarService] = None,
    analytics: Optional[AnalyticsEngine] = None,
    model_id: Optional[str] = None,
    enable_gmail_tools: bool = False,
    gmail_credentials_path: Optional[str] = None,
    gmail_token_path: Optional[str] = None,
    **agent_kwargs,
) -> Agent:
    """
    Create the Coordinator Agent with all tools.

    This is the main agent that has access to every tool in the system.
    It can handle any recruitment task directly or route to specialized logic.

    Args:
        knowledge_base: Knowledge instance for semantic search.
        knowledge_tool: InsertKnowledgeTool for ingestion.
        candidate_db: CandidateDB for pipeline management.
        scorer: ResumeScorer for candidate scoring.
        classifier: EmailClassifier for email classification.
        template_engine: EmailTemplateEngine for email templates.
        smtp_sender: SmtpGmailSender for sending emails.
        calendar_service: CalendarService for calendar operations.
        analytics: AnalyticsEngine for reporting.
        model_id: OpenRouter model ID.
        enable_gmail_tools: Whether to enable Agno Gmail read tools.
        gmail_credentials_path: Path to Google OAuth credentials.
        gmail_token_path: Path to Google OAuth token.
        **agent_kwargs: Additional Agent constructor arguments.

    Returns:
        Configured Coordinator Agent with all capabilities.
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY environment variable is required.")

    if model_id is None:
        model_id = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")

    openrouter_model = OpenRouter(id=model_id, api_key=api_key)
    logger.info("Coordinator using OpenRouter model: %s", model_id)

    tools: List[Any] = []

    # Knowledge tools (from sample)
    if knowledge_tool:
        tools.extend(create_knowledge_insert_tools(knowledge_tool))
        logger.info("Enabled knowledge insert tools")

    # Candidate DB tools (new)
    if candidate_db:
        tools.extend(create_candidate_db_tools(candidate_db))
        logger.info("Enabled candidate database tools")

    # Resume scorer tools (new)
    if scorer:
        tools.extend(create_scorer_tools(scorer))
        logger.info("Enabled resume scoring tools")

    # Email classifier tools (new)
    if classifier:
        tools.extend(create_classifier_tools(classifier))
        logger.info("Enabled email classification tools")

    # Email template tools (new)
    if template_engine:
        tools.extend(create_template_tools(template_engine))
        logger.info("Enabled email template tools")

    # SMTP Gmail send tools (new)
    tools.extend(create_smtp_gmail_tools(smtp_sender))
    logger.info("Enabled SMTP Gmail send tools")

    # Calendar tools (new)
    tools.extend(create_calendar_tools(calendar_service))
    logger.info("Enabled calendar tools")

    # Analytics tools (new)
    if analytics:
        tools.extend(create_analytics_tools(analytics))
        logger.info("Enabled analytics tools")

    # Agno Gmail read tools (from sample)
    if enable_gmail_tools:
        try:
            gmail_tools = create_readonly_gmail_tools(
                credentials_path=gmail_credentials_path,
                token_path=gmail_token_path,
            )
            tools.append(gmail_tools)
            logger.info("Enabled Gmail read-only tools")
        except Exception as e:
            logger.warning("Failed to enable Gmail tools: %s", e)

    # Add any additional tools from kwargs
    if "tools" in agent_kwargs:
        existing_tools = agent_kwargs.pop("tools")
        if isinstance(existing_tools, list):
            tools.extend(existing_tools)
        else:
            tools.append(existing_tools)

    agent = Agent(
        name="Coordinator",
        model=openrouter_model,
        knowledge=knowledge_base,
        search_knowledge=True,
        tools=tools if tools else None,
        instructions=COORDINATOR_INSTRUCTIONS,
        description=(
            "Main coordinator agent that routes to specialized sub-agents "
            "and has access to all recruitment pipeline tools."
        ),
        **agent_kwargs,
    )

    logger.info(
        "Coordinator Agent created with %d tool groups. Gmail=%s",
        len(tools), enable_gmail_tools,
    )
    return agent
