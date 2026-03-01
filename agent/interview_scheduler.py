"""
Interview Scheduler Agent

Specialized agent for Google Calendar integration, conflict detection,
time slot proposals, and interview event management.
"""
import logging
import os
from typing import Any, List, Optional

from agno.agent import Agent
from agno.models.openrouter import OpenRouter

from tools.calendar_tools import CalendarService, create_calendar_tools
from tools.candidate_db import CandidateDB, create_candidate_db_tools

logger = logging.getLogger(__name__)

INTERVIEW_SCHEDULER_INSTRUCTIONS = [
    "You are a specialized Interview Scheduler Agent for a recruitment pipeline system.",
    "Your primary responsibilities are:",
    "1. Find available time slots for interviews on Google Calendar.",
    "2. Check for scheduling conflicts before proposing times.",
    "3. Create interview events with proper details and attendee invitations.",
    "4. Update or reschedule existing interview events.",
    "5. Cancel interviews when needed.",
    "6. Update candidate pipeline status when interviews are scheduled.",
    "",
    "When scheduling an interview:",
    "- First check for available slots on the requested date.",
    "- Verify there are no conflicts with the proposed time.",
    "- Create the calendar event with candidate name, position, and email.",
    "- Update the candidate's stage to INTERVIEW_SCHEDULED in the pipeline.",
    "- Store the event ID on the candidate record for future reference.",
    "",
    "Always propose multiple time options when possible. Consider timezone",
    "differences and business hours (9 AM - 5 PM by default).",
    "Be professional and clear about scheduling details.",
]


def create_interview_scheduler_agent(
    candidate_db: Optional[CandidateDB] = None,
    calendar_service: Optional[CalendarService] = None,
    model_id: Optional[str] = None,
    **agent_kwargs,
) -> Agent:
    """
    Create the Interview Scheduler Agent.

    Args:
        candidate_db: CandidateDB for pipeline management.
        calendar_service: CalendarService for calendar operations.
        model_id: OpenRouter model ID.
        **agent_kwargs: Additional Agent constructor arguments.

    Returns:
        Configured Interview Scheduler Agent.
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY environment variable is required.")

    if model_id is None:
        model_id = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")

    openrouter_model = OpenRouter(id=model_id, api_key=api_key)

    tools: List[Any] = []

    tools.extend(create_calendar_tools(calendar_service))

    if candidate_db:
        tools.extend(create_candidate_db_tools(candidate_db))

    agent = Agent(
        name="InterviewScheduler",
        model=openrouter_model,
        tools=tools if tools else None,
        instructions=INTERVIEW_SCHEDULER_INSTRUCTIONS,
        description="Specialized agent for interview scheduling and calendar management.",
        **agent_kwargs,
    )

    logger.info("Interview Scheduler Agent created with model: %s", model_id)
    return agent
