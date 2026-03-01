"""
Advanced Resume Scoring Engine

Configurable job requirements with weighted criteria for:
- Skill matching (exact and fuzzy)
- Experience level scoring
- Education level scoring
- Composite score with detailed breakdowns
"""
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class JobRequirement:
    """Configurable job requirement specification."""
    title: str = "Software Engineer"
    required_skills: List[str] = field(default_factory=lambda: ["python", "sql"])
    preferred_skills: List[str] = field(default_factory=lambda: ["docker", "aws"])
    min_experience_years: int = 2
    preferred_experience_years: int = 5
    required_education: List[str] = field(default_factory=lambda: ["bachelor"])
    preferred_education: List[str] = field(default_factory=lambda: ["master", "phd"])
    weight_skills: float = 0.40
    weight_experience: float = 0.30
    weight_education: float = 0.20
    weight_keyword_match: float = 0.10
    custom_keywords: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "title": self.title,
            "required_skills": self.required_skills,
            "preferred_skills": self.preferred_skills,
            "min_experience_years": self.min_experience_years,
            "preferred_experience_years": self.preferred_experience_years,
            "required_education": self.required_education,
            "preferred_education": self.preferred_education,
            "weight_skills": self.weight_skills,
            "weight_experience": self.weight_experience,
            "weight_education": self.weight_education,
            "weight_keyword_match": self.weight_keyword_match,
            "custom_keywords": self.custom_keywords,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JobRequirement":
        """Deserialize from dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class ResumeScorer:
    """
    Weighted resume scoring engine that evaluates candidates against
    configurable job requirements.
    """

    def __init__(self, default_requirements: Optional[JobRequirement] = None):
        """
        Initialize the scorer.

        Args:
            default_requirements: Default job requirements to use when none specified.
        """
        self.default_requirements = default_requirements or JobRequirement()

    def score_skills(
        self,
        candidate_skills: List[str],
        requirements: JobRequirement,
    ) -> Dict[str, Any]:
        """
        Score skill match between candidate and job requirements.

        Args:
            candidate_skills: List of candidate's skills.
            requirements: Job requirements to score against.

        Returns:
            Dictionary with score (0-100), matched/missing skills, and details.
        """
        candidate_lower = [s.lower().strip() for s in candidate_skills]
        required_lower = [s.lower().strip() for s in requirements.required_skills]
        preferred_lower = [s.lower().strip() for s in requirements.preferred_skills]

        # Exact and fuzzy matching for required skills
        required_matched = []
        required_missing = []
        for skill in required_lower:
            if skill in candidate_lower or any(skill in cs for cs in candidate_lower):
                required_matched.append(skill)
            else:
                required_missing.append(skill)

        # Preferred skills matching
        preferred_matched = []
        for skill in preferred_lower:
            if skill in candidate_lower or any(skill in cs for cs in candidate_lower):
                preferred_matched.append(skill)

        # Score calculation
        if not required_lower and not preferred_lower:
            score = 50.0  # No requirements specified
        else:
            required_score = (len(required_matched) / max(len(required_lower), 1)) * 70
            preferred_score = (len(preferred_matched) / max(len(preferred_lower), 1)) * 30
            score = min(required_score + preferred_score, 100.0)

        return {
            "score": round(score, 2),
            "required_matched": required_matched,
            "required_missing": required_missing,
            "preferred_matched": preferred_matched,
            "total_candidate_skills": len(candidate_skills),
        }

    def score_experience(
        self,
        experience_years: int,
        requirements: JobRequirement,
    ) -> Dict[str, Any]:
        """
        Score experience level against requirements.

        Args:
            experience_years: Candidate's years of experience.
            requirements: Job requirements.

        Returns:
            Dictionary with score (0-100) and details.
        """
        min_years = requirements.min_experience_years
        preferred_years = requirements.preferred_experience_years

        if experience_years >= preferred_years:
            score = 100.0
        elif experience_years >= min_years:
            range_size = max(preferred_years - min_years, 1)
            score = 60.0 + ((experience_years - min_years) / range_size) * 40.0
        elif experience_years > 0:
            score = (experience_years / max(min_years, 1)) * 60.0
        else:
            score = 0.0

        return {
            "score": round(min(score, 100.0), 2),
            "candidate_years": experience_years,
            "min_required": min_years,
            "preferred": preferred_years,
            "meets_minimum": experience_years >= min_years,
        }

    def score_education(
        self,
        education_entries: List[str],
        requirements: JobRequirement,
    ) -> Dict[str, Any]:
        """
        Score education level against requirements.

        Args:
            education_entries: List of education strings from resume.
            requirements: Job requirements.

        Returns:
            Dictionary with score (0-100) and details.
        """
        edu_text = " ".join(education_entries).lower()

        education_levels = {
            "phd": 100,
            "ph.d": 100,
            "doctorate": 100,
            "master": 85,
            "mba": 85,
            "m.s.": 85,
            "m.a.": 85,
            "bachelor": 70,
            "b.s.": 70,
            "b.a.": 70,
            "associate": 50,
            "diploma": 40,
            "certificate": 30,
        }

        # Find highest education level
        highest_score = 0
        highest_level = "none"
        for level, level_score in education_levels.items():
            if level in edu_text and level_score > highest_score:
                highest_score = level_score
                highest_level = level

        # Check required education
        required_met = False
        for req in requirements.required_education:
            if req.lower() in edu_text:
                required_met = True
                break

        # Check preferred education
        preferred_met = False
        for pref in requirements.preferred_education:
            if pref.lower() in edu_text:
                preferred_met = True
                break

        if preferred_met:
            score = 100.0
        elif required_met:
            score = 75.0
        else:
            score = float(highest_score) * 0.7

        return {
            "score": round(min(score, 100.0), 2),
            "highest_level": highest_level,
            "required_met": required_met,
            "preferred_met": preferred_met,
            "education_entries": education_entries,
        }

    def score_keywords(
        self,
        resume_text: str,
        requirements: JobRequirement,
    ) -> Dict[str, Any]:
        """
        Score keyword density and relevance.

        Args:
            resume_text: Full resume text.
            requirements: Job requirements.

        Returns:
            Dictionary with score (0-100) and matched keywords.
        """
        text_lower = resume_text.lower()
        all_keywords = (
            requirements.required_skills
            + requirements.preferred_skills
            + requirements.custom_keywords
        )
        all_keywords = list(set(kw.lower() for kw in all_keywords))

        if not all_keywords:
            return {"score": 50.0, "matched_keywords": [], "total_keywords": 0}

        matched = [kw for kw in all_keywords if kw in text_lower]
        score = (len(matched) / len(all_keywords)) * 100.0

        return {
            "score": round(min(score, 100.0), 2),
            "matched_keywords": matched,
            "total_keywords": len(all_keywords),
            "match_ratio": round(len(matched) / len(all_keywords), 2),
        }

    def score_candidate(
        self,
        candidate_skills: List[str],
        experience_years: int,
        education_entries: List[str],
        resume_text: str = "",
        requirements: Optional[JobRequirement] = None,
    ) -> Dict[str, Any]:
        """
        Generate a composite score for a candidate.

        Args:
            candidate_skills: List of candidate's skills.
            experience_years: Years of experience.
            education_entries: Education entries.
            resume_text: Full resume text for keyword scoring.
            requirements: Job requirements (uses default if None).

        Returns:
            Dictionary with composite score, breakdown, and recommendation.
        """
        req = requirements or self.default_requirements

        skills_result = self.score_skills(candidate_skills, req)
        experience_result = self.score_experience(experience_years, req)
        education_result = self.score_education(education_entries, req)
        keywords_result = self.score_keywords(resume_text, req)

        composite = (
            skills_result["score"] * req.weight_skills
            + experience_result["score"] * req.weight_experience
            + education_result["score"] * req.weight_education
            + keywords_result["score"] * req.weight_keyword_match
        )
        composite = round(min(composite, 100.0), 2)

        # Recommendation based on composite score
        if composite >= 80:
            recommendation = "STRONG_MATCH"
        elif composite >= 60:
            recommendation = "GOOD_MATCH"
        elif composite >= 40:
            recommendation = "MODERATE_MATCH"
        else:
            recommendation = "WEAK_MATCH"

        return {
            "composite_score": composite,
            "recommendation": recommendation,
            "job_title": req.title,
            "breakdown": {
                "skills": {
                    "score": skills_result["score"],
                    "weight": req.weight_skills,
                    "weighted": round(skills_result["score"] * req.weight_skills, 2),
                    "details": skills_result,
                },
                "experience": {
                    "score": experience_result["score"],
                    "weight": req.weight_experience,
                    "weighted": round(experience_result["score"] * req.weight_experience, 2),
                    "details": experience_result,
                },
                "education": {
                    "score": education_result["score"],
                    "weight": req.weight_education,
                    "weighted": round(education_result["score"] * req.weight_education, 2),
                    "details": education_result,
                },
                "keywords": {
                    "score": keywords_result["score"],
                    "weight": req.weight_keyword_match,
                    "weighted": round(keywords_result["score"] * req.weight_keyword_match, 2),
                    "details": keywords_result,
                },
            },
        }


def create_scorer_tools(scorer: ResumeScorer) -> list:
    """
    Create tool functions for agent use.

    Args:
        scorer: ResumeScorer instance.

    Returns:
        List of callable tool functions.
    """

    def score_resume(
        skills: str = "[]",
        experience_years: int = 0,
        education: str = "[]",
        resume_text: str = "",
        job_title: str = "",
        required_skills: str = "",
        preferred_skills: str = "",
        min_experience: int = 2,
    ) -> str:
        """
        Score a resume against job requirements.

        Args:
            skills: JSON array of candidate skills, e.g. '["python", "sql"]'.
            experience_years: Years of professional experience.
            education: JSON array of education entries.
            resume_text: Full resume text for keyword matching.
            job_title: Job title to score against.
            required_skills: Comma-separated required skills.
            preferred_skills: Comma-separated preferred skills.
            min_experience: Minimum required years of experience.

        Returns:
            JSON with composite score, breakdown, and recommendation.
        """
        try:
            skills_list = json.loads(skills) if isinstance(skills, str) and skills.startswith("[") else [s.strip() for s in skills.split(",") if s.strip()]
        except json.JSONDecodeError:
            skills_list = [s.strip() for s in skills.split(",") if s.strip()]

        try:
            edu_list = json.loads(education) if isinstance(education, str) and education.startswith("[") else [education] if education else []
        except json.JSONDecodeError:
            edu_list = [education] if education else []

        req = JobRequirement(
            title=job_title or scorer.default_requirements.title,
            required_skills=[s.strip() for s in required_skills.split(",") if s.strip()] if required_skills else scorer.default_requirements.required_skills,
            preferred_skills=[s.strip() for s in preferred_skills.split(",") if s.strip()] if preferred_skills else scorer.default_requirements.preferred_skills,
            min_experience_years=min_experience,
        )

        result = scorer.score_candidate(
            candidate_skills=skills_list,
            experience_years=experience_years,
            education_entries=edu_list,
            resume_text=resume_text,
            requirements=req,
        )
        return json.dumps(result, indent=2)

    def get_scoring_criteria() -> str:
        """
        Get the current default scoring criteria and weights.

        Returns:
            JSON with current job requirements and scoring weights.
        """
        return json.dumps(scorer.default_requirements.to_dict(), indent=2)

    return [score_resume, get_scoring_criteria]
