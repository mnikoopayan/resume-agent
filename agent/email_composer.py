"""
Email Composer Agent

Specialized agent for drafting and sending professional recruitment emails.
Uses template engine for consistent communications and SMTP for delivery.
"""
import logging
import os
from typing import Any, List, Optional

from agno.agent import Agent
from agno.models.openrouter import OpenRouter

from tools.gmail_tools import SmtpGmailSender, create_smtp_gmail_tools
from tools.email_templates import EmailTemplateEngine, create_template_tools
from tools.email_classifier import EmailClassifier, create_classifier_tools
from tools.candidate_db import CandidateDB, create_candidate_db_tools

logger = logging.getLogger(__name__)

EMAIL_COMPOSER_INSTRUCTIONS = [
    "You are a specialized Email Composer Agent for a recruitment pipeline system.",
    "Your primary responsibilities are:",
    "1. Draft professional recruitment emails using templates.",
    "2. Send emails via Gmail SMTP (application acknowledgments, interview invitations, etc.).",
    "3. Classify incoming emails to determine appropriate responses.",
    "4. Compose personalized responses based on candidate context.",
    "5. Manage email drafts for review before sending.",
    "",
    "Available email templates:",
    "- application_acknowledgment: Confirm receipt of application.",
    "- interview_invitation: Invite candidate for interview.",
    "- interview_confirmation: Confirm interview details.",
    "- interview_reschedule: Notify of rescheduled interview.",
    "- rejection: Professional rejection notification.",
    "- offer: Job offer communication.",
    "- follow_up_request: Follow up with candidate.",
    "- inquiry_response: Respond to general inquiries.",
    "",
    "When composing emails:",
    "- Always use templates for consistency and professionalism.",
    "- Personalize with candidate name, position, and relevant details.",
    "- Review the draft before sending when possible.",
    "- Log all sent communications for tracking.",
    "",
    "Be professional, empathetic, and clear in all communications.",
    "Ensure all emails represent the organization positively.",
]


def create_email_composer_agent(
    candidate_db: Optional[CandidateDB] = None,
    template_engine: Optional[EmailTemplateEngine] = None,
    smtp_sender: Optional[SmtpGmailSender] = None,
    classifier: Optional[EmailClassifier] = None,
    gmail_tools_instance: Optional[Any] = None,
    model_id: Optional[str] = None,
    **agent_kwargs,
) -> Agent:
    """
    Create the Email Composer Agent.

    Args:
        candidate_db: CandidateDB for candidate lookup.
        template_engine: EmailTemplateEngine for templates.
        smtp_sender: SmtpGmailSender for sending emails.
        classifier: EmailClassifier for incoming email classification.
        gmail_tools_instance: Optional Agno GmailTools for read operations.
        model_id: OpenRouter model ID.
        **agent_kwargs: Additional Agent constructor arguments.

    Returns:
        Configured Email Composer Agent.
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY environment variable is required.")

    if model_id is None:
        model_id = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")

    openrouter_model = OpenRouter(id=model_id, api_key=api_key)

    tools: List[Any] = []

    if template_engine:
        tools.extend(create_template_tools(template_engine))

    tools.extend(create_smtp_gmail_tools(smtp_sender))

    if classifier:
        tools.extend(create_classifier_tools(classifier))

    if candidate_db:
        tools.extend(create_candidate_db_tools(candidate_db))

    if gmail_tools_instance:
        tools.append(gmail_tools_instance)

    agent = Agent(
        name="EmailComposer",
        model=openrouter_model,
        tools=tools if tools else None,
        instructions=EMAIL_COMPOSER_INSTRUCTIONS,
        description="Specialized agent for drafting and sending professional recruitment emails.",
        **agent_kwargs,
    )

    logger.info("Email Composer Agent created with model: %s", model_id)
    return agent
