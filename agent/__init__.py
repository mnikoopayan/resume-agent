"""
Agent Module — Enhanced Multi-Agent Resume System

Provides specialized agents for recruitment pipeline management:
- Coordinator: Routes requests to specialized sub-agents
- Resume Analyzer: Parse resumes, extract data, generate profiles
- Interview Scheduler: Calendar integration, conflict detection
- Candidate Ranker: Weighted scoring against job requirements
- Email Composer: Draft and send recruitment emails
- Pipeline Manager: Orchestrate pipeline, track candidate status
"""
from agent.coordinator import create_coordinator_agent
from agent.resume_analyzer import create_resume_analyzer_agent
from agent.interview_scheduler import create_interview_scheduler_agent
from agent.candidate_ranker import create_candidate_ranker_agent
from agent.email_composer import create_email_composer_agent
from agent.pipeline_manager import create_pipeline_manager_agent

__all__ = [
    "create_coordinator_agent",
    "create_resume_analyzer_agent",
    "create_interview_scheduler_agent",
    "create_candidate_ranker_agent",
    "create_email_composer_agent",
    "create_pipeline_manager_agent",
]
