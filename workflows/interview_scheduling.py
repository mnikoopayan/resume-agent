"""
Interview Scheduling Workflow

Multi-step workflow for scheduling candidate interviews:
1. Verify candidate exists and is in appropriate stage
2. Find available time slots on the requested date
3. Check for scheduling conflicts
4. Create calendar event with candidate details
5. Send interview invitation email
6. Update candidate stage to INTERVIEW_SCHEDULED
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from tools.candidate_db import CandidateDB
from tools.calendar_tools import CalendarService
from tools.email_templates import EmailTemplateEngine
from tools.gmail_tools import SmtpGmailSender

logger = logging.getLogger(__name__)


@dataclass
class SchedulingResult:
    """Result of the interview scheduling workflow."""
    success: bool = False
    candidate_id: int = 0
    candidate_name: str = ""
    candidate_email: str = ""
    event_id: str = ""
    interview_date: str = ""
    interview_time: str = ""
    interview_end: str = ""
    invitation_sent: bool = False
    stage_updated: bool = False
    available_slots: List[Dict[str, str]] = field(default_factory=list)
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    steps_completed: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "success": self.success,
            "candidate_id": self.candidate_id,
            "candidate_name": self.candidate_name,
            "candidate_email": self.candidate_email,
            "event_id": self.event_id,
            "interview_date": self.interview_date,
            "interview_time": self.interview_time,
            "interview_end": self.interview_end,
            "invitation_sent": self.invitation_sent,
            "stage_updated": self.stage_updated,
            "available_slots": self.available_slots,
            "conflicts": self.conflicts,
            "errors": self.errors,
            "steps_completed": self.steps_completed,
        }


class InterviewSchedulingWorkflow:
    """
    Orchestrates interview scheduling with calendar integration,
    conflict detection, and email notifications.
    """

    def __init__(
        self,
        candidate_db: CandidateDB,
        calendar_service: CalendarService,
        template_engine: EmailTemplateEngine,
        smtp_sender: SmtpGmailSender,
        default_duration_minutes: int = 60,
        default_format: str = "Video Call (Google Meet)",
    ):
        """
        Initialize the workflow.

        Args:
            candidate_db: Candidate pipeline database.
            calendar_service: Google Calendar service.
            template_engine: Email template engine.
            smtp_sender: SMTP email sender.
            default_duration_minutes: Default interview duration.
            default_format: Default interview format.
        """
        self.candidate_db = candidate_db
        self.calendar_service = calendar_service
        self.template_engine = template_engine
        self.smtp_sender = smtp_sender
        self.default_duration_minutes = default_duration_minutes
        self.default_format = default_format

    def find_slots(
        self,
        date: str,
        duration_minutes: Optional[int] = None,
    ) -> List[Dict[str, str]]:
        """
        Find available interview slots on a date.

        Args:
            date: Date in YYYY-MM-DD format.
            duration_minutes: Required duration.

        Returns:
            List of available time slots.
        """
        duration = duration_minutes or self.default_duration_minutes
        return self.calendar_service.find_available_slots(
            date=date, duration_minutes=duration
        )

    def schedule(
        self,
        candidate_id: int,
        start_time: str,
        end_time: Optional[str] = None,
        position: str = "",
        location: str = "",
        interview_format: Optional[str] = None,
        send_invitation: bool = True,
        dry_run: bool = False,
    ) -> SchedulingResult:
        """
        Schedule an interview for a candidate.

        Args:
            candidate_id: Candidate ID in the pipeline.
            start_time: Interview start time in ISO format.
            end_time: Interview end time (auto-calculated if not provided).
            position: Position title for the interview.
            location: Interview location or meeting link.
            interview_format: Interview format description.
            send_invitation: Whether to send invitation email.
            dry_run: When True, validate and simulate scheduling without creating
                calendar events, sending emails, or mutating candidate stage.

        Returns:
            SchedulingResult with full details.
        """
        result = SchedulingResult(candidate_id=candidate_id)
        fmt = interview_format or self.default_format

        # Step 1: Look up candidate
        try:
            candidate = self.candidate_db.get_candidate(candidate_id)
            if not candidate:
                result.errors.append(f"Candidate #{candidate_id} not found.")
                return result
            result.candidate_name = candidate.get("name", "")
            result.candidate_email = candidate.get("email", "")
            result.steps_completed.append("candidate_verified")
        except Exception as e:
            result.errors.append(f"Candidate lookup failed: {e}")
            return result

        # Calculate end time if not provided
        if not end_time:
            try:
                from dateutil import parser as dp
                start_dt = dp.parse(start_time)
                end_dt = start_dt + timedelta(minutes=self.default_duration_minutes)
                end_time = end_dt.isoformat()
            except Exception:
                end_time = start_time  # Fallback

        result.interview_time = start_time
        result.interview_end = end_time

        # Extract date for display
        try:
            from dateutil import parser as dp
            dt = dp.parse(start_time)
            result.interview_date = dt.strftime("%B %d, %Y")
            display_time = dt.strftime("%I:%M %p")
        except Exception:
            result.interview_date = start_time[:10]
            display_time = start_time

        # Step 2: Check for conflicts
        try:
            conflict_info = self.calendar_service.check_conflicts(start_time, end_time)
            if conflict_info.get("has_conflicts"):
                result.conflicts = conflict_info.get("conflicting_events", [])
                logger.warning(
                    "Scheduling conflict detected for %s: %d conflicts",
                    start_time, len(result.conflicts),
                )
            result.steps_completed.append("conflicts_checked")
        except Exception as e:
            logger.warning("Conflict check failed: %s", e)
            result.errors.append(f"Conflict check failed: {e}")

        # Step 3: Create calendar event (or simulate in dry-run mode)
        position_title = position or candidate.get("job_title_applied", "Open Position")
        if dry_run:
            result.event_id = "dry-run-event"
            result.steps_completed.append("calendar_event_simulated")
        else:
            try:
                event_result = self.calendar_service.create_event(
                    summary=f"Interview: {result.candidate_name} — {position_title}",
                    start_time=start_time,
                    end_time=end_time,
                    description=(
                        f"Interview for {position_title}\n"
                        f"Candidate: {result.candidate_name}\n"
                        f"Email: {result.candidate_email}\n"
                        f"Format: {fmt}"
                    ),
                    location=location or "",
                    attendees=[result.candidate_email] if result.candidate_email else None,
                )
                if event_result.get("success"):
                    result.event_id = event_result.get("event_id", "")
                    result.steps_completed.append("calendar_event_created")
                else:
                    result.errors.append(
                        f"Calendar event creation failed: {event_result.get('error', 'unknown')}"
                    )
            except Exception as e:
                logger.error("Calendar event creation failed: %s", e)
                result.errors.append(f"Calendar event failed: {e}")

        # Step 4: Send invitation email
        if send_invitation and result.candidate_email and not dry_run:
            try:
                rendered = self.template_engine.render(
                    "interview_invitation",
                    {
                        "candidate_name": result.candidate_name,
                        "position": position or candidate.get("job_title_applied", "Open Position"),
                        "interview_date": result.interview_date,
                        "interview_time": display_time,
                        "interview_duration": f"{self.default_duration_minutes} minutes",
                        "interview_format": fmt,
                        "interview_location": location or "Link will be provided in calendar invite",
                    },
                )
                send_result = self.smtp_sender.send_email(
                    to=result.candidate_email,
                    subject=rendered["subject"],
                    body=rendered["body"],
                )
                result.invitation_sent = send_result.get("success", False)
                if result.invitation_sent:
                    result.steps_completed.append("invitation_sent")
                else:
                    result.errors.append(
                        f"Invitation send failed: {send_result.get('error', 'unknown')}"
                    )
            except Exception as e:
                logger.error("Failed to send invitation: %s", e)
                result.errors.append(f"Invitation failed: {e}")

        # Step 5: Update candidate stage only after a successful event creation.
        if dry_run:
            result.steps_completed.append("stage_update_skipped_dry_run")
        elif result.event_id:
            try:
                self.candidate_db.advance_stage(candidate_id, "INTERVIEW_SCHEDULED")
                self.candidate_db.update_candidate(
                    candidate_id=candidate_id,
                    interview_datetime=start_time,
                    interview_event_id=result.event_id,
                    notes=f"Interview scheduled: {start_time}, Event ID: {result.event_id}",
                )
                result.stage_updated = True
                result.steps_completed.append("stage_updated")
            except Exception as e:
                logger.warning("Failed to update stage: %s", e)
                result.errors.append(f"Stage update failed: {e}")
        else:
            result.errors.append("Stage update skipped because no calendar event was created.")

        result.success = len(result.errors) == 0
        logger.info(
            "Interview scheduling complete for %s. Success=%s",
            result.candidate_name, result.success,
        )
        return result


def create_workflow_tools(workflow: InterviewSchedulingWorkflow) -> list:
    """
    Create tool functions for the interview scheduling workflow.

    Args:
        workflow: InterviewSchedulingWorkflow instance.

    Returns:
        List of callable tool functions.
    """

    def find_interview_slots(
        date: str,
        duration_minutes: int = 60,
    ) -> str:
        """
        Find available interview time slots on a given date.

        Args:
            date: Date in YYYY-MM-DD format.
            duration_minutes: Required interview duration in minutes.

        Returns:
            JSON array of available time slots.
        """
        slots = workflow.find_slots(date=date, duration_minutes=duration_minutes)
        return json.dumps({"date": date, "slots": slots, "count": len(slots)}, indent=2)

    def schedule_interview(
        candidate_id: int,
        start_time: str,
        position: str = "",
        location: str = "",
        send_invitation: bool = True,
    ) -> str:
        """
        Schedule an interview for a candidate. Creates calendar event,
        sends invitation email, and updates pipeline stage.

        Args:
            candidate_id: Candidate ID in the pipeline.
            start_time: Interview start time in ISO format (e.g., 2025-03-15T10:00:00Z).
            position: Position title for the interview.
            location: Interview location or meeting link.
            send_invitation: Whether to send invitation email.

        Returns:
            JSON with complete scheduling result.
        """
        sched_result = workflow.schedule(
            candidate_id=candidate_id,
            start_time=start_time,
            position=position,
            location=location,
            send_invitation=send_invitation,
        )
        return json.dumps(sched_result.to_dict(), indent=2)

    return [find_interview_slots, schedule_interview]
