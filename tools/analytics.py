"""
Analytics and Reporting Engine

Provides pipeline statistics, time-to-hire metrics, source tracking,
score distribution analysis, and recruitment funnel reporting.
"""
import json
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class AnalyticsEngine:
    """
    Analytics engine for recruitment pipeline reporting.
    Queries the candidate database for metrics and insights.
    """

    def __init__(self, db_path: str = "./knowledge/candidates.db"):
        """
        Initialize the analytics engine.

        Args:
            db_path: Path to the candidate SQLite database.
        """
        self.db_path = Path(db_path)

    def _get_conn(self) -> sqlite3.Connection:
        """Get a database connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def pipeline_stats(self) -> Dict[str, Any]:
        """
        Get comprehensive pipeline statistics.

        Returns:
            Dictionary with stage counts, totals, and conversion rates.
        """
        try:
            with self._get_conn() as conn:
                # Stage counts
                rows = conn.execute(
                    "SELECT stage, COUNT(*) as cnt FROM candidates GROUP BY stage"
                ).fetchall()
                stage_counts = {row["stage"]: row["cnt"] for row in rows}

                # Total count
                total = conn.execute("SELECT COUNT(*) as cnt FROM candidates").fetchone()
                total_count = total["cnt"] if total else 0

                # Average score
                avg_row = conn.execute(
                    "SELECT AVG(score) as avg_score FROM candidates WHERE score > 0"
                ).fetchone()
                avg_score = round(avg_row["avg_score"], 2) if avg_row and avg_row["avg_score"] else 0.0

                # Conversion rates
                conversions = {}
                stages_ordered = [
                    "NEW", "SCREENING", "INTERVIEW_SCHEDULED",
                    "INTERVIEWED", "RANKED", "OFFERED", "HIRED",
                ]
                for i in range(len(stages_ordered) - 1):
                    from_stage = stages_ordered[i]
                    to_stage = stages_ordered[i + 1]
                    from_count = stage_counts.get(from_stage, 0)
                    # Count candidates who have passed through to_stage or beyond
                    beyond_count = sum(
                        stage_counts.get(s, 0) for s in stages_ordered[i + 1:]
                    )
                    rate = round((beyond_count / from_count * 100), 1) if from_count > 0 else 0.0
                    conversions[f"{from_stage}_to_{to_stage}"] = rate

            return {
                "total_candidates": total_count,
                "stage_counts": stage_counts,
                "average_score": avg_score,
                "conversion_rates": conversions,
            }
        except Exception as e:
            logger.error("Failed to compute pipeline stats: %s", e)
            return {"error": str(e)}

    def time_to_hire_metrics(self) -> Dict[str, Any]:
        """
        Calculate time-to-hire metrics for hired candidates.

        Returns:
            Dictionary with average, min, max time-to-hire in days.
        """
        try:
            with self._get_conn() as conn:
                hired = conn.execute(
                    "SELECT id, created_at, updated_at FROM candidates WHERE stage = 'HIRED'"
                ).fetchall()

            if not hired:
                return {
                    "hired_count": 0,
                    "average_days": 0,
                    "min_days": 0,
                    "max_days": 0,
                    "message": "No hired candidates yet.",
                }

            durations = []
            for row in hired:
                try:
                    created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
                    updated = datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
                    days = (updated - created).days
                    durations.append(max(days, 0))
                except (ValueError, TypeError):
                    continue

            if not durations:
                return {"hired_count": len(hired), "average_days": 0, "min_days": 0, "max_days": 0}

            return {
                "hired_count": len(hired),
                "average_days": round(sum(durations) / len(durations), 1),
                "min_days": min(durations),
                "max_days": max(durations),
            }
        except Exception as e:
            logger.error("Failed to compute time-to-hire: %s", e)
            return {"error": str(e)}

    def source_tracking(self) -> Dict[str, Any]:
        """
        Track candidate sources and their effectiveness.

        Returns:
            Dictionary with source counts and average scores per source.
        """
        try:
            with self._get_conn() as conn:
                rows = conn.execute(
                    """
                    SELECT source,
                           COUNT(*) as count,
                           AVG(score) as avg_score,
                           SUM(CASE WHEN stage = 'HIRED' THEN 1 ELSE 0 END) as hired_count
                    FROM candidates
                    GROUP BY source
                    ORDER BY count DESC
                    """
                ).fetchall()

            sources = []
            for row in rows:
                sources.append({
                    "source": row["source"],
                    "count": row["count"],
                    "average_score": round(row["avg_score"], 2) if row["avg_score"] else 0.0,
                    "hired_count": row["hired_count"],
                    "hire_rate": round(
                        (row["hired_count"] / row["count"] * 100), 1
                    ) if row["count"] > 0 else 0.0,
                })

            return {"sources": sources, "total_sources": len(sources)}
        except Exception as e:
            logger.error("Failed to compute source tracking: %s", e)
            return {"error": str(e)}

    def score_distribution(self) -> Dict[str, Any]:
        """
        Analyze the distribution of candidate scores.

        Returns:
            Dictionary with score ranges, percentiles, and statistics.
        """
        try:
            with self._get_conn() as conn:
                rows = conn.execute(
                    "SELECT score FROM candidates WHERE score > 0 ORDER BY score"
                ).fetchall()

            scores = [row["score"] for row in rows]

            if not scores:
                return {"message": "No scored candidates yet.", "count": 0}

            # Score ranges
            ranges = {
                "0-20 (Weak)": 0,
                "20-40 (Below Average)": 0,
                "40-60 (Average)": 0,
                "60-80 (Good)": 0,
                "80-100 (Excellent)": 0,
            }
            for score in scores:
                if score < 20:
                    ranges["0-20 (Weak)"] += 1
                elif score < 40:
                    ranges["20-40 (Below Average)"] += 1
                elif score < 60:
                    ranges["40-60 (Average)"] += 1
                elif score < 80:
                    ranges["60-80 (Good)"] += 1
                else:
                    ranges["80-100 (Excellent)"] += 1

            n = len(scores)
            return {
                "count": n,
                "mean": round(sum(scores) / n, 2),
                "median": round(scores[n // 2], 2),
                "min": round(min(scores), 2),
                "max": round(max(scores), 2),
                "distribution": ranges,
            }
        except Exception as e:
            logger.error("Failed to compute score distribution: %s", e)
            return {"error": str(e)}

    def top_candidates(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get top-scored candidates.

        Args:
            limit: Maximum number of candidates to return.

        Returns:
            List of top candidate summaries.
        """
        try:
            with self._get_conn() as conn:
                rows = conn.execute(
                    """
                    SELECT id, name, email, score, stage, skills, experience_years
                    FROM candidates
                    WHERE score > 0
                    ORDER BY score DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()

            return [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "email": row["email"],
                    "score": row["score"],
                    "stage": row["stage"],
                    "skills": json.loads(row["skills"]) if row["skills"] else [],
                    "experience_years": row["experience_years"],
                }
                for row in rows
            ]
        except Exception as e:
            logger.error("Failed to get top candidates: %s", e)
            return []

    def full_report(self) -> Dict[str, Any]:
        """
        Generate a comprehensive analytics report.

        Returns:
            Dictionary with all analytics sections.
        """
        return {
            "pipeline": self.pipeline_stats(),
            "time_to_hire": self.time_to_hire_metrics(),
            "sources": self.source_tracking(),
            "scores": self.score_distribution(),
            "top_candidates": self.top_candidates(limit=5),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


def create_analytics_tools(engine: Optional[AnalyticsEngine] = None) -> List[Callable]:
    """
    Create analytics tool functions for agent use.

    Args:
        engine: AnalyticsEngine instance (creates default if None).

    Returns:
        List of callable tool functions.
    """
    if engine is None:
        engine = AnalyticsEngine()

    def get_pipeline_statistics() -> str:
        """
        Get comprehensive recruitment pipeline statistics including stage counts,
        conversion rates, and average scores.

        Returns:
            JSON with pipeline statistics.
        """
        return json.dumps(engine.pipeline_stats(), indent=2)

    def get_time_to_hire() -> str:
        """
        Get time-to-hire metrics for hired candidates.

        Returns:
            JSON with average, min, max time-to-hire in days.
        """
        return json.dumps(engine.time_to_hire_metrics(), indent=2)

    def get_source_analytics() -> str:
        """
        Get candidate source tracking analytics showing which sources
        produce the most and best candidates.

        Returns:
            JSON with source counts, average scores, and hire rates.
        """
        return json.dumps(engine.source_tracking(), indent=2)

    def get_score_distribution() -> str:
        """
        Get the distribution of candidate scores across ranges.

        Returns:
            JSON with score statistics and distribution.
        """
        return json.dumps(engine.score_distribution(), indent=2)

    def get_top_candidates(limit: int = 10) -> str:
        """
        Get the top-scored candidates in the pipeline.

        Args:
            limit: Maximum number of candidates to return.

        Returns:
            JSON array of top candidates with scores.
        """
        return json.dumps(engine.top_candidates(limit=limit), indent=2)

    def get_full_analytics_report() -> str:
        """
        Generate a comprehensive analytics report covering all metrics:
        pipeline stats, time-to-hire, source tracking, score distribution,
        and top candidates.

        Returns:
            JSON with complete analytics report.
        """
        return json.dumps(engine.full_report(), indent=2)

    return [
        get_pipeline_statistics,
        get_time_to_hire,
        get_source_analytics,
        get_score_distribution,
        get_top_candidates,
        get_full_analytics_report,
    ]
