"""
Candidate Pipeline Database (SQLite)

Tracks candidates through recruitment stages:
NEW → SCREENING → INTERVIEW_SCHEDULED → INTERVIEWED → RANKED → OFFERED → HIRED → REJECTED

Provides full CRUD, search/filter, stage advancement, and history tracking.
"""
import json
import logging
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _clean_text(value: Optional[str]) -> str:
    return (value or "").strip()


class CandidateStage(str, Enum):
    """Pipeline stages for candidate tracking."""
    NEW = "NEW"
    SCREENING = "SCREENING"
    INTERVIEW_SCHEDULED = "INTERVIEW_SCHEDULED"
    INTERVIEWED = "INTERVIEWED"
    RANKED = "RANKED"
    OFFERED = "OFFERED"
    HIRED = "HIRED"
    REJECTED = "REJECTED"


STAGE_ORDER = [
    CandidateStage.NEW,
    CandidateStage.SCREENING,
    CandidateStage.INTERVIEW_SCHEDULED,
    CandidateStage.INTERVIEWED,
    CandidateStage.RANKED,
    CandidateStage.OFFERED,
    CandidateStage.HIRED,
]

VALID_TRANSITIONS = {
    CandidateStage.NEW: [CandidateStage.SCREENING, CandidateStage.REJECTED],
    CandidateStage.SCREENING: [CandidateStage.INTERVIEW_SCHEDULED, CandidateStage.REJECTED],
    CandidateStage.INTERVIEW_SCHEDULED: [CandidateStage.INTERVIEWED, CandidateStage.REJECTED],
    CandidateStage.INTERVIEWED: [CandidateStage.RANKED, CandidateStage.REJECTED],
    CandidateStage.RANKED: [CandidateStage.OFFERED, CandidateStage.REJECTED],
    CandidateStage.OFFERED: [CandidateStage.HIRED, CandidateStage.REJECTED],
    CandidateStage.HIRED: [],
    CandidateStage.REJECTED: [],
}


class CandidateDB:
    """SQLite-backed candidate pipeline database with full CRUD and stage management."""

    def __init__(self, db_path: str = "./knowledge/candidates.db"):
        """
        Initialize the candidate database.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a database connection with row factory."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        """Create tables if they do not exist."""
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT DEFAULT '',
                    phone TEXT DEFAULT '',
                    skills TEXT DEFAULT '[]',
                    experience_years INTEGER DEFAULT 0,
                    education TEXT DEFAULT '[]',
                    source TEXT DEFAULT 'manual',
                    resume_text TEXT DEFAULT '',
                    stage TEXT NOT NULL DEFAULT 'NEW',
                    score REAL DEFAULT 0.0,
                    score_breakdown TEXT DEFAULT '{}',
                    notes TEXT DEFAULT '',
                    job_title_applied TEXT DEFAULT '',
                    interview_datetime TEXT DEFAULT '',
                    interview_event_id TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS stage_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_id INTEGER NOT NULL,
                    from_stage TEXT NOT NULL,
                    to_stage TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    changed_by TEXT DEFAULT 'system',
                    notes TEXT DEFAULT '',
                    FOREIGN KEY (candidate_id) REFERENCES candidates(id)
                );

                CREATE INDEX IF NOT EXISTS idx_candidates_stage ON candidates(stage);
                CREATE INDEX IF NOT EXISTS idx_candidates_email ON candidates(email);
                CREATE INDEX IF NOT EXISTS idx_candidates_score ON candidates(score DESC);
                CREATE INDEX IF NOT EXISTS idx_stage_history_candidate ON stage_history(candidate_id);
            """)

    def create_candidate(
        self,
        name: str,
        email: str = "",
        phone: str = "",
        skills: Optional[List[str]] = None,
        experience_years: int = 0,
        education: Optional[List[str]] = None,
        source: str = "manual",
        resume_text: str = "",
        job_title_applied: str = "",
        notes: str = "",
    ) -> int:
        """
        Create a new candidate in the pipeline.

        Args:
            name: Candidate full name.
            email: Email address.
            phone: Phone number.
            skills: List of skill strings.
            experience_years: Total years of experience.
            education: List of education entries.
            source: How the candidate was sourced (e.g., 'gmail', 'upload', 'manual').
            resume_text: Raw resume text (truncated for storage).
            job_title_applied: Position applied for.
            notes: Additional notes.

        Returns:
            The new candidate ID.
        """
        name = _clean_text(name)
        email = _clean_text(email).lower()
        phone = _clean_text(phone)
        source = _clean_text(source) or "manual"
        job_title_applied = _clean_text(job_title_applied)
        notes = _clean_text(notes)
        resume_text = (resume_text or "").strip()
        if not name:
            raise ValueError("Candidate name is required.")

        now = datetime.now(timezone.utc).isoformat()
        skills_json = json.dumps(skills or [])
        education_json = json.dumps(education or [])

        with self._get_conn() as conn:
            # Check for duplicate by email (case-insensitive).
            if email:
                existing = conn.execute(
                    "SELECT id FROM candidates WHERE LOWER(email) = ?", (email,)
                ).fetchone()
                if existing:
                    logger.info("Candidate with email '%s' already exists (id=%s), updating.", email, existing["id"])
                    update_payload = {
                        "name": name,
                        "phone": phone,
                        "skills": skills,
                        "experience_years": experience_years,
                        "education": education,
                        "resume_text": resume_text,
                    }
                    if source:
                        update_payload["source"] = source
                    if job_title_applied:
                        update_payload["job_title_applied"] = job_title_applied
                    if notes:
                        update_payload["notes"] = notes
                    self.update_candidate(existing["id"], **update_payload)
                    return existing["id"]

            cursor = conn.execute(
                """
                INSERT INTO candidates (
                    name, email, phone, skills, experience_years, education,
                    source, resume_text, stage, job_title_applied, notes,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name, email, phone, skills_json, experience_years,
                    education_json, source, resume_text[:2000],
                    CandidateStage.NEW.value, job_title_applied, notes,
                    now, now,
                ),
            )
            candidate_id = cursor.lastrowid

            conn.execute(
                """
                INSERT INTO stage_history (candidate_id, from_stage, to_stage, changed_at, changed_by, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (candidate_id, "", CandidateStage.NEW.value, now, "system", "Candidate created"),
            )

        logger.info("Created candidate id=%s name='%s'", candidate_id, name)
        return candidate_id

    def get_candidate(self, candidate_id: int) -> Optional[Dict[str, Any]]:
        """
        Get a single candidate by ID.

        Args:
            candidate_id: The candidate's database ID.

        Returns:
            Dictionary with candidate data or None if not found.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list_candidates(
        self,
        stage: Optional[str] = None,
        min_score: Optional[float] = None,
        skill: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        List candidates with optional filters.

        Args:
            stage: Filter by pipeline stage.
            min_score: Minimum composite score.
            skill: Filter by skill (substring match in skills JSON).
            search: Full-text search across name, email, resume_text.
            limit: Maximum results.
            offset: Pagination offset.

        Returns:
            List of candidate dictionaries.
        """
        limit = max(1, min(int(limit), 1000))
        offset = max(0, int(offset))

        conditions = []
        params: List[Any] = []

        if stage:
            conditions.append("stage = ?")
            params.append(stage.upper())
        if min_score is not None:
            conditions.append("score >= ?")
            params.append(min_score)
        if skill:
            conditions.append("LOWER(skills) LIKE ?")
            params.append(f"%{skill.lower()}%")
        if search:
            conditions.append("(LOWER(name) LIKE ? OR LOWER(email) LIKE ? OR LOWER(resume_text) LIKE ?)")
            term = f"%{search.lower()}%"
            params.extend([term, term, term])

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT * FROM candidates WHERE {where_clause} ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def update_candidate(self, candidate_id: int, **kwargs) -> bool:
        """
        Update candidate fields.

        Args:
            candidate_id: The candidate's database ID.
            **kwargs: Fields to update (name, email, phone, skills, experience_years,
                      education, notes, score, score_breakdown, job_title_applied,
                      interview_datetime, interview_event_id, resume_text).

        Returns:
            True if the candidate was updated.
        """
        allowed_fields = {
            "name", "email", "phone", "skills", "experience_years", "education",
            "notes", "score", "score_breakdown", "job_title_applied",
            "interview_datetime", "interview_event_id", "resume_text", "source",
        }
        updates = {}
        for key, value in kwargs.items():
            if key in allowed_fields and value is not None:
                if key in ("skills", "education"):
                    updates[key] = json.dumps(value) if isinstance(value, list) else value
                elif key == "score_breakdown":
                    updates[key] = json.dumps(value) if isinstance(value, dict) else value
                else:
                    updates[key] = value

        if not updates:
            return False

        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [candidate_id]

        with self._get_conn() as conn:
            result = conn.execute(
                f"UPDATE candidates SET {set_clause} WHERE id = ?", params
            )
        return result.rowcount > 0

    def advance_stage(
        self,
        candidate_id: int,
        target_stage: str,
        changed_by: str = "system",
        notes: str = "",
    ) -> Dict[str, Any]:
        """
        Advance a candidate to the next pipeline stage.

        Args:
            candidate_id: The candidate's database ID.
            target_stage: The target stage to move to.
            changed_by: Who initiated the change.
            notes: Notes about the transition.

        Returns:
            Dictionary with transition result.

        Raises:
            ValueError: If the transition is not valid.
        """
        candidate = self.get_candidate(candidate_id)
        if candidate is None:
            raise ValueError(f"Candidate {candidate_id} not found")

        current = CandidateStage(candidate["stage"])
        try:
            target = CandidateStage(target_stage.upper())
        except ValueError:
            raise ValueError(
                f"Invalid stage: {target_stage}. Valid stages: {[s.value for s in CandidateStage]}"
            )

        valid_next = VALID_TRANSITIONS.get(current, [])
        if target not in valid_next:
            raise ValueError(
                f"Cannot transition from {current.value} to {target.value}. "
                f"Valid transitions: {[s.value for s in valid_next]}"
            )

        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE candidates SET stage = ?, updated_at = ? WHERE id = ?",
                (target.value, now, candidate_id),
            )
            conn.execute(
                """
                INSERT INTO stage_history (candidate_id, from_stage, to_stage, changed_at, changed_by, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (candidate_id, current.value, target.value, now, changed_by, notes),
            )

        logger.info(
            "Advanced candidate %s from %s to %s", candidate_id, current.value, target.value
        )
        return {
            "candidate_id": candidate_id,
            "from_stage": current.value,
            "to_stage": target.value,
            "changed_at": now,
        }

    def get_stage_history(self, candidate_id: int) -> List[Dict[str, Any]]:
        """
        Get the full stage transition history for a candidate.

        Args:
            candidate_id: The candidate's database ID.

        Returns:
            List of stage transition records.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM stage_history
                WHERE candidate_id = ?
                ORDER BY changed_at ASC
                """,
                (candidate_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def search_candidates(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Search candidates across name, email, job title, notes, and resume text.

        Args:
            query: Search string.
            limit: Maximum number of results.

        Returns:
            List of matching candidate dictionaries.
        """
        limit = max(1, min(int(limit), 1000))
        term = f"%{(query or '').lower()}%"
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM candidates
                WHERE LOWER(name) LIKE ?
                   OR LOWER(email) LIKE ?
                   OR LOWER(job_title_applied) LIKE ?
                   OR LOWER(notes) LIKE ?
                   OR LOWER(resume_text) LIKE ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (term, term, term, term, term, limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def delete_candidate(self, candidate_id: int) -> bool:
        """
        Delete a candidate and their stage history.

        Args:
            candidate_id: The candidate's database ID.

        Returns:
            True if the candidate was deleted.
        """
        with self._get_conn() as conn:
            conn.execute("DELETE FROM stage_history WHERE candidate_id = ?", (candidate_id,))
            result = conn.execute("DELETE FROM candidates WHERE id = ?", (candidate_id,))
        return result.rowcount > 0

    def count_by_stage(self) -> Dict[str, int]:
        """
        Count candidates in each pipeline stage.

        Returns:
            Dictionary mapping stage name to count.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT stage, COUNT(*) as cnt FROM candidates GROUP BY stage"
            ).fetchall()
        result = {s.value: 0 for s in CandidateStage}
        for row in rows:
            result[row["stage"]] = row["cnt"]
        return result

    def get_total_count(self) -> int:
        """Get total number of candidates."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM candidates").fetchone()
        return row["cnt"] if row else 0

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a sqlite3.Row to a dictionary with parsed JSON fields."""
        d = dict(row)
        for json_field in ("skills", "education"):
            if json_field in d and isinstance(d[json_field], str):
                try:
                    d[json_field] = json.loads(d[json_field])
                except (json.JSONDecodeError, TypeError):
                    d[json_field] = []
        if "score_breakdown" in d and isinstance(d["score_breakdown"], str):
            try:
                d["score_breakdown"] = json.loads(d["score_breakdown"])
            except (json.JSONDecodeError, TypeError):
                d["score_breakdown"] = {}
        return d


def create_candidate_db_tools(db: CandidateDB) -> list:
    """
    Create tool functions for agent use.

    Args:
        db: CandidateDB instance.

    Returns:
        List of callable tool functions.
    """

    def create_candidate(
        name: str,
        email: str = "",
        phone: str = "",
        skills: str = "[]",
        experience_years: int = 0,
        source: str = "manual",
        job_title_applied: str = "",
        notes: str = "",
    ) -> str:
        """
        Create a new candidate in the recruitment pipeline.

        Args:
            name: Full name of the candidate.
            email: Email address.
            phone: Phone number.
            skills: JSON array of skills, e.g. '["python", "java"]'.
            experience_years: Total years of professional experience.
            source: How the candidate was sourced.
            job_title_applied: Position the candidate applied for.
            notes: Additional notes.

        Returns:
            Confirmation message with the new candidate ID.
        """
        try:
            skills_list = json.loads(skills) if isinstance(skills, str) else skills
        except json.JSONDecodeError:
            skills_list = [s.strip() for s in skills.split(",") if s.strip()]

        cid = db.create_candidate(
            name=name,
            email=email,
            phone=phone,
            skills=skills_list,
            experience_years=experience_years,
            source=source,
            job_title_applied=job_title_applied,
            notes=notes,
        )
        return f"Candidate created successfully with ID {cid}."

    def get_candidate(candidate_id: int) -> str:
        """
        Get detailed information about a candidate by their ID.

        Args:
            candidate_id: The candidate's database ID.

        Returns:
            JSON string with candidate details.
        """
        candidate = db.get_candidate(candidate_id)
        if candidate is None:
            return f"Candidate with ID {candidate_id} not found."
        return json.dumps(candidate, indent=2, default=str)

    def list_candidates(
        stage: str = "",
        min_score: float = 0.0,
        skill: str = "",
        search: str = "",
        limit: int = 20,
    ) -> str:
        """
        List candidates with optional filters.

        Args:
            stage: Filter by pipeline stage (e.g., 'NEW', 'SCREENING', 'INTERVIEW_SCHEDULED').
            min_score: Minimum composite score filter.
            skill: Filter by skill keyword.
            search: Search across name, email, resume text.
            limit: Maximum number of results.

        Returns:
            JSON array of matching candidates.
        """
        candidates = db.list_candidates(
            stage=stage or None,
            min_score=min_score if min_score > 0 else None,
            skill=skill or None,
            search=search or None,
            limit=limit,
        )
        return json.dumps(candidates, indent=2, default=str)

    def advance_candidate_stage(
        candidate_id: int,
        target_stage: str,
        notes: str = "",
    ) -> str:
        """
        Advance a candidate to the next pipeline stage.

        Valid stages: NEW, SCREENING, INTERVIEW_SCHEDULED, INTERVIEWED, RANKED, OFFERED, HIRED, REJECTED.

        Args:
            candidate_id: The candidate's database ID.
            target_stage: The target stage to advance to.
            notes: Notes about the transition.

        Returns:
            Confirmation message with transition details.
        """
        try:
            result = db.advance_stage(candidate_id, target_stage, notes=notes)
            return (
                f"Candidate {result['candidate_id']} advanced from "
                f"{result['from_stage']} to {result['to_stage']}."
            )
        except ValueError as e:
            return f"Error: {e}"

    def get_pipeline_stats() -> str:
        """
        Get pipeline statistics showing candidate counts per stage.

        Returns:
            JSON object with stage counts and total.
        """
        counts = db.count_by_stage()
        total = db.get_total_count()
        return json.dumps({"stages": counts, "total": total}, indent=2)

    def update_candidate_info(
        candidate_id: int,
        notes: str = "",
        score: float = 0.0,
        job_title_applied: str = "",
    ) -> str:
        """
        Update candidate information.

        Args:
            candidate_id: The candidate's database ID.
            notes: Updated notes.
            score: Updated score.
            job_title_applied: Updated job title.

        Returns:
            Confirmation message.
        """
        kwargs = {}
        if notes:
            kwargs["notes"] = notes
        if score > 0:
            kwargs["score"] = score
        if job_title_applied:
            kwargs["job_title_applied"] = job_title_applied
        if not kwargs:
            return "No fields to update."
        success = db.update_candidate(candidate_id, **kwargs)
        return f"Candidate {candidate_id} updated: {success}"

    return [
        create_candidate,
        get_candidate,
        list_candidates,
        advance_candidate_stage,
        get_pipeline_stats,
        update_candidate_info,
    ]
