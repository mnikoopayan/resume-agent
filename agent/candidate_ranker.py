"""
Candidate Ranker Agent

Specialized agent for weighted scoring of candidates against configurable
job requirements. Provides detailed score breakdowns and ranking comparisons.
"""
import logging
import os
from typing import Any, List, Optional

from agno.agent import Agent
from agno.knowledge.knowledge import Knowledge
from agno.models.openrouter import OpenRouter

from tools.resume_scorer import ResumeScorer, create_scorer_tools
from tools.candidate_db import CandidateDB, create_candidate_db_tools
from tools.analytics import AnalyticsEngine, create_analytics_tools

logger = logging.getLogger(__name__)

CANDIDATE_RANKER_INSTRUCTIONS = [
    "You are a specialized Candidate Ranker Agent for a recruitment pipeline system.",
    "Your primary responsibilities are:",
    "1. Score candidates against configurable job requirements using weighted criteria.",
    "2. Provide detailed score breakdowns across skills, experience, education, and keywords.",
    "3. Compare and rank multiple candidates for the same position.",
    "4. Generate recommendations (STRONG_MATCH, GOOD_MATCH, MODERATE_MATCH, WEAK_MATCH).",
    "5. Update candidate scores in the pipeline database.",
    "6. Advance qualified candidates to the RANKED stage.",
    "",
    "When ranking candidates:",
    "- Retrieve candidate data from the pipeline database.",
    "- Score each candidate using the resume scoring tool.",
    "- Update scores and breakdowns in the database.",
    "- Advance candidates with scores above 60 to the RANKED stage.",
    "- Provide a clear comparison table when ranking multiple candidates.",
    "",
    "Scoring weights (default):",
    "- Skills: 40% — Match against required and preferred skills.",
    "- Experience: 30% — Years of experience vs. requirements.",
    "- Education: 20% — Education level vs. requirements.",
    "- Keywords: 10% — Keyword density and relevance.",
    "",
    "Always explain your scoring rationale and highlight key differentiators.",
]


def create_candidate_ranker_agent(
    knowledge_base: Optional[Knowledge] = None,
    candidate_db: Optional[CandidateDB] = None,
    scorer: Optional[ResumeScorer] = None,
    analytics: Optional[AnalyticsEngine] = None,
    model_id: Optional[str] = None,
    **agent_kwargs,
) -> Agent:
    """
    Create the Candidate Ranker Agent.

    Args:
        knowledge_base: Knowledge instance for semantic search.
        candidate_db: CandidateDB for pipeline management.
        scorer: ResumeScorer for scoring.
        analytics: AnalyticsEngine for statistics.
        model_id: OpenRouter model ID.
        **agent_kwargs: Additional Agent constructor arguments.

    Returns:
        Configured Candidate Ranker Agent.
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY environment variable is required.")

    if model_id is None:
        model_id = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")

    openrouter_model = OpenRouter(id=model_id, api_key=api_key)

    tools: List[Any] = []

    if scorer:
        tools.extend(create_scorer_tools(scorer))

    if candidate_db:
        tools.extend(create_candidate_db_tools(candidate_db))

    if analytics:
        tools.extend(create_analytics_tools(analytics))

    agent = Agent(
        name="CandidateRanker",
        model=openrouter_model,
        knowledge=knowledge_base,
        search_knowledge=True if knowledge_base else False,
        tools=tools if tools else None,
        instructions=CANDIDATE_RANKER_INSTRUCTIONS,
        description="Specialized agent for candidate scoring, ranking, and comparison.",
        **agent_kwargs,
    )

    logger.info("Candidate Ranker Agent created with model: %s", model_id)
    return agent
