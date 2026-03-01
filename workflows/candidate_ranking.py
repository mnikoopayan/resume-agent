"""
Candidate Ranking Workflow

Multi-step workflow for ranking and comparing candidates:
1. Retrieve candidates from the pipeline (by stage or all)
2. Score each candidate against job requirements
3. Rank candidates by composite score
4. Generate comparison report
5. Advance top candidates to RANKED stage
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from tools.candidate_db import CandidateDB
from tools.resume_scorer import ResumeScorer, JobRequirement

logger = logging.getLogger(__name__)


@dataclass
class RankingResult:
    """Result of the candidate ranking workflow."""
    success: bool = False
    job_title: str = ""
    total_candidates: int = 0
    scored_candidates: int = 0
    advanced_candidates: int = 0
    rankings: List[Dict[str, Any]] = field(default_factory=list)
    score_summary: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    steps_completed: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "success": self.success,
            "job_title": self.job_title,
            "total_candidates": self.total_candidates,
            "scored_candidates": self.scored_candidates,
            "advanced_candidates": self.advanced_candidates,
            "rankings": self.rankings,
            "score_summary": self.score_summary,
            "errors": self.errors,
            "steps_completed": self.steps_completed,
        }


class CandidateRankingWorkflow:
    """
    Orchestrates candidate ranking with weighted scoring,
    comparison reports, and pipeline stage advancement.
    """

    def __init__(
        self,
        candidate_db: CandidateDB,
        scorer: ResumeScorer,
        advance_threshold: float = 60.0,
        auto_advance: bool = True,
    ):
        """
        Initialize the workflow.

        Args:
            candidate_db: Candidate pipeline database.
            scorer: Resume scoring engine.
            advance_threshold: Minimum score to advance to RANKED stage.
            auto_advance: Whether to auto-advance qualifying candidates.
        """
        self.candidate_db = candidate_db
        self.scorer = scorer
        self.advance_threshold = advance_threshold
        self.auto_advance = auto_advance

    def rank_candidates(
        self,
        stage: Optional[str] = None,
        job_requirements: Optional[JobRequirement] = None,
        limit: int = 50,
    ) -> RankingResult:
        """
        Rank candidates in the pipeline.

        Args:
            stage: Filter candidates by stage (e.g., "SCREENING").
            job_requirements: Job requirements for scoring.
            limit: Maximum candidates to rank.

        Returns:
            RankingResult with rankings and statistics.
        """
        result = RankingResult()
        req = job_requirements or self.scorer.default_requirements
        result.job_title = req.title

        # Step 1: Retrieve candidates
        try:
            if stage:
                candidates = self.candidate_db.list_candidates(stage=stage, limit=limit)
            else:
                candidates = self.candidate_db.list_candidates(limit=limit)
            result.total_candidates = len(candidates)
            result.steps_completed.append("candidates_retrieved")
            logger.info("Retrieved %d candidates for ranking", len(candidates))
        except Exception as e:
            result.errors.append(f"Failed to retrieve candidates: {e}")
            return result

        if not candidates:
            result.success = True
            result.steps_completed.append("no_candidates_to_rank")
            return result

        # Step 2: Score each candidate
        scored = []
        for candidate in candidates:
            try:
                skills = []
                if candidate.get("skills"):
                    try:
                        skills = json.loads(candidate["skills"])
                    except (json.JSONDecodeError, TypeError):
                        skills = []

                education = []
                if candidate.get("education"):
                    try:
                        education = json.loads(candidate["education"])
                    except (json.JSONDecodeError, TypeError):
                        education = [candidate["education"]] if candidate["education"] else []

                score_result = self.scorer.score_candidate(
                    candidate_skills=skills,
                    experience_years=candidate.get("experience_years", 0) or 0,
                    education_entries=education,
                    resume_text=candidate.get("notes", ""),
                    requirements=req,
                )

                scored.append({
                    "candidate_id": candidate["id"],
                    "name": candidate["name"],
                    "email": candidate["email"],
                    "current_stage": candidate["stage"],
                    "composite_score": score_result["composite_score"],
                    "recommendation": score_result["recommendation"],
                    "breakdown": score_result["breakdown"],
                })

                # Update score in database
                try:
                    self.candidate_db.update_candidate(
                        candidate_id=candidate["id"],
                        score=score_result["composite_score"],
                    )
                except Exception as ue:
                    logger.warning(
                        "Failed to update score for candidate #%d: %s",
                        candidate["id"], ue,
                    )

            except Exception as e:
                logger.warning(
                    "Failed to score candidate #%d: %s", candidate.get("id", 0), e
                )
                result.errors.append(
                    f"Scoring failed for candidate #{candidate.get('id', 0)}: {e}"
                )

        result.scored_candidates = len(scored)
        result.steps_completed.append("candidates_scored")

        # Step 3: Rank by score
        scored.sort(key=lambda x: x["composite_score"], reverse=True)
        for rank, entry in enumerate(scored, 1):
            entry["rank"] = rank
        result.rankings = scored
        result.steps_completed.append("candidates_ranked")

        # Step 4: Generate summary statistics
        if scored:
            scores = [s["composite_score"] for s in scored]
            result.score_summary = {
                "mean": round(sum(scores) / len(scores), 2),
                "median": round(sorted(scores)[len(scores) // 2], 2),
                "min": round(min(scores), 2),
                "max": round(max(scores), 2),
                "above_threshold": sum(1 for s in scores if s >= self.advance_threshold),
                "below_threshold": sum(1 for s in scores if s < self.advance_threshold),
                "threshold": self.advance_threshold,
            }
            result.steps_completed.append("summary_generated")

        # Step 5: Auto-advance qualifying candidates
        if self.auto_advance:
            advanced_count = 0
            for entry in scored:
                if entry["composite_score"] >= self.advance_threshold:
                    try:
                        current = entry.get("current_stage", "")
                        # Only advance if not already past RANKED
                        if current in ("NEW", "SCREENING", "INTERVIEW_SCHEDULED", "INTERVIEWED"):
                            self.candidate_db.advance_stage(entry["candidate_id"], "RANKED")
                            advanced_count += 1
                    except Exception as e:
                        logger.warning(
                            "Failed to advance candidate #%d: %s",
                            entry["candidate_id"], e,
                        )
            result.advanced_candidates = advanced_count
            if advanced_count > 0:
                result.steps_completed.append(f"advanced_{advanced_count}_candidates")

        result.success = len(result.errors) == 0
        logger.info(
            "Ranking complete. Scored=%d, Advanced=%d, Top score=%.2f",
            result.scored_candidates, result.advanced_candidates,
            result.rankings[0]["composite_score"] if result.rankings else 0,
        )
        return result


def create_workflow_tools(workflow: CandidateRankingWorkflow) -> list:
    """
    Create tool functions for the candidate ranking workflow.

    Args:
        workflow: CandidateRankingWorkflow instance.

    Returns:
        List of callable tool functions.
    """

    def rank_all_candidates(
        stage: str = "",
        job_title: str = "",
        required_skills: str = "",
        preferred_skills: str = "",
        min_experience: int = 2,
        limit: int = 50,
    ) -> str:
        """
        Rank all candidates (or by stage) against job requirements.
        Scores, ranks, and optionally advances qualifying candidates.

        Args:
            stage: Filter by pipeline stage (e.g., "SCREENING"). Empty for all.
            job_title: Job title for requirements.
            required_skills: Comma-separated required skills.
            preferred_skills: Comma-separated preferred skills.
            min_experience: Minimum required years of experience.
            limit: Maximum candidates to rank.

        Returns:
            JSON with rankings, scores, and summary statistics.
        """
        req = None
        if job_title or required_skills or preferred_skills:
            req = JobRequirement(
                title=job_title or "Open Position",
                required_skills=[s.strip() for s in required_skills.split(",") if s.strip()] if required_skills else [],
                preferred_skills=[s.strip() for s in preferred_skills.split(",") if s.strip()] if preferred_skills else [],
                min_experience_years=min_experience,
            )

        ranking_result = workflow.rank_candidates(
            stage=stage or None,
            job_requirements=req,
            limit=limit,
        )
        return json.dumps(ranking_result.to_dict(), indent=2)

    return [rank_all_candidates]
