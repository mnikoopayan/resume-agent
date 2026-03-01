"""
Resume Analyzer Agent

Specialized agent for parsing resumes, extracting structured data,
and generating comprehensive candidate profiles. Uses the knowledge
base for semantic search and the knowledge insert tool for ingestion.
"""
import logging
import os
from typing import Any, List, Optional

from agno.agent import Agent
from agno.knowledge.knowledge import Knowledge
from agno.models.openrouter import OpenRouter

from tools.knowledge_tool import InsertKnowledgeTool, create_knowledge_insert_tools
from tools.candidate_db import CandidateDB, create_candidate_db_tools

logger = logging.getLogger(__name__)

RESUME_ANALYZER_INSTRUCTIONS = [
    "You are a specialized Resume Analyzer Agent for a recruitment pipeline system.",
    "Your primary responsibilities are:",
    "1. Parse and analyze resumes from text, PDF, TXT, and DOCX files.",
    "2. Extract structured data: name, email, phone, skills, experience, education.",
    "3. Generate comprehensive candidate profiles with strengths and areas of concern.",
    "4. Insert resume content into the knowledge base for semantic search.",
    "5. Create candidate records in the pipeline database.",
    "",
    "When analyzing a resume:",
    "- First extract all structured data using the extract_resume_data tool.",
    "- Create a candidate profile in the database using create_candidate.",
    "- Insert the resume into the knowledge base using insert_text or insert_file.",
    "- Provide a detailed analysis including: key qualifications, notable achievements,",
    "  skill gaps relative to common job requirements, and overall assessment.",
    "",
    "Always be thorough and objective in your analysis. Highlight both strengths",
    "and potential concerns. Use the knowledge base to compare with other candidates",
    "when relevant.",
]


def create_resume_analyzer_agent(
    knowledge_base: Knowledge,
    knowledge_tool: Optional[InsertKnowledgeTool] = None,
    candidate_db: Optional[CandidateDB] = None,
    model_id: Optional[str] = None,
    **agent_kwargs,
) -> Agent:
    """
    Create the Resume Analyzer Agent.

    Args:
        knowledge_base: Knowledge instance for semantic search.
        knowledge_tool: InsertKnowledgeTool for ingestion.
        candidate_db: CandidateDB for candidate profile creation.
        model_id: OpenRouter model ID.
        **agent_kwargs: Additional Agent constructor arguments.

    Returns:
        Configured Resume Analyzer Agent.
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY environment variable is required.")

    if model_id is None:
        model_id = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")

    openrouter_model = OpenRouter(id=model_id, api_key=api_key)

    tools: List[Any] = []

    if knowledge_tool:
        tools.extend(create_knowledge_insert_tools(knowledge_tool))

    if candidate_db:
        tools.extend(create_candidate_db_tools(candidate_db))

    agent = Agent(
        name="ResumeAnalyzer",
        model=openrouter_model,
        knowledge=knowledge_base,
        search_knowledge=True,
        tools=tools if tools else None,
        instructions=RESUME_ANALYZER_INSTRUCTIONS,
        description="Specialized agent for resume parsing, data extraction, and candidate profiling.",
        **agent_kwargs,
    )

    logger.info("Resume Analyzer Agent created with model: %s", model_id)
    return agent
