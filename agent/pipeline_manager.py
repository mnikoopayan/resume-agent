"""
Pipeline Manager Agent

Specialized agent for orchestrating the full recruitment pipeline,
tracking candidate status, managing stage transitions, and providing
pipeline analytics and reporting.
"""
import logging
import os
from typing import Any, List, Optional

from agno.agent import Agent
from agno.knowledge.knowledge import Knowledge
from agno.models.openrouter import OpenRouter

from tools.candidate_db import CandidateDB, create_candidate_db_tools
from tools.analytics import AnalyticsEngine, create_analytics_tools
from tools.resume_scorer import ResumeScorer, create_scorer_tools

logger = logging.getLogger(__name__)

PIPELINE_MANAGER_INSTRUCTIONS = [
    "You are a specialized Pipeline Manager Agent for a recruitment pipeline system.",
    "Your primary responsibilities are:",
    "1. Manage the full recruitment pipeline from NEW to HIRED/REJECTED.",
    "2. Track candidate status and stage transitions.",
    "3. Provide pipeline analytics and reporting.",
    "4. Orchestrate workflows across the recruitment process.",
    "5. Ensure candidates move through stages appropriately.",
    "",
    "Pipeline stages (in order):",
    "NEW → SCREENING → INTERVIEW_SCHEDULED → INTERVIEWED → RANKED → OFFERED → HIRED",
    "Any stage can transition to REJECTED.",
    "",
    "When managing the pipeline:",
    "- List candidates by stage to identify bottlenecks.",
    "- Advance candidates when they meet stage requirements.",
    "- Track time spent in each stage.",
    "- Generate reports on pipeline health and conversion rates.",
    "- Flag candidates who have been in a stage too long.",
    "",
    "Use analytics tools to provide data-driven insights about:",
    "- Pipeline throughput and conversion rates.",
    "- Time-to-hire metrics.",
    "- Source effectiveness.",
    "- Score distributions.",
    "",
    "Always provide actionable recommendations based on pipeline data.",
]


def create_pipeline_manager_agent(
    knowledge_base: Optional[Knowledge] = None,
    candidate_db: Optional[CandidateDB] = None,
    analytics: Optional[AnalyticsEngine] = None,
    scorer: Optional[ResumeScorer] = None,
    model_id: Optional[str] = None,
    **agent_kwargs,
) -> Agent:
    """
    Create the Pipeline Manager Agent.

    Args:
        knowledge_base: Knowledge instance for semantic search.
        candidate_db: CandidateDB for pipeline management.
        analytics: AnalyticsEngine for reporting.
        scorer: ResumeScorer for candidate scoring.
        model_id: OpenRouter model ID.
        **agent_kwargs: Additional Agent constructor arguments.

    Returns:
        Configured Pipeline Manager Agent.
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY environment variable is required.")

    if model_id is None:
        model_id = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")

    openrouter_model = OpenRouter(id=model_id, api_key=api_key)

    tools: List[Any] = []

    if candidate_db:
        tools.extend(create_candidate_db_tools(candidate_db))

    if analytics:
        tools.extend(create_analytics_tools(analytics))

    if scorer:
        tools.extend(create_scorer_tools(scorer))

    agent = Agent(
        name="PipelineManager",
        model=openrouter_model,
        knowledge=knowledge_base,
        search_knowledge=True if knowledge_base else False,
        tools=tools if tools else None,
        instructions=PIPELINE_MANAGER_INSTRUCTIONS,
        description="Specialized agent for recruitment pipeline orchestration and analytics.",
        **agent_kwargs,
    )

    logger.info("Pipeline Manager Agent created with model: %s", model_id)
    return agent
