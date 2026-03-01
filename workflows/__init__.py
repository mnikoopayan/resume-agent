"""
Workflows Module

Orchestrated multi-step workflows for the recruitment pipeline:
- new_application: Process incoming applications end-to-end
- interview_scheduling: Schedule interviews with conflict detection
- candidate_ranking: Rank and compare candidates for positions
"""
from workflows.new_application import NewApplicationWorkflow
from workflows.interview_scheduling import InterviewSchedulingWorkflow
from workflows.candidate_ranking import CandidateRankingWorkflow

__all__ = [
    "NewApplicationWorkflow",
    "InterviewSchedulingWorkflow",
    "CandidateRankingWorkflow",
]
