"""
Google Calendar Tools

Provides calendar integration for interview scheduling:
- List available time slots
- Create interview events
- Check for scheduling conflicts
- Send calendar invites
- Update/cancel events
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _safe_write_token(token_path: Path, creds_json: str) -> None:
    """Write refreshed creds without accidentally shrinking OAuth scopes."""
    try:
        new_data = json.loads(creds_json)
    except Exception:
        token_path.write_text(creds_json, encoding="utf-8")
        return

    old_scopes: set[str] = set()
    try:
        if token_path.exists():
            old_data = json.loads(token_path.read_text(encoding="utf-8"))
            old_scopes = set(old_data.get("scopes") or [])
    except Exception:
        old_scopes = set()

    new_scopes = set(new_data.get("scopes") or [])

    if not new_scopes and old_scopes:
        new_data["scopes"] = sorted(old_scopes)
        token_path.write_text(json.dumps(new_data, indent=2), encoding="utf-8")
        return

    if old_scopes and new_scopes and not old_scopes.issubset(new_scopes):
        logger.warning(
            "Refreshed token would shrink scopes. Preserving existing scopes. old=%s new=%s",
            sorted(old_scopes),
            sorted(new_scopes),
        )
        new_data["scopes"] = sorted(old_scopes.union(new_scopes))

    token_path.write_text(json.dumps(new_data, indent=2), encoding="utf-8")
class CalendarService:
    """
    Google Calendar service wrapper for interview scheduling.
    Uses the Google Calendar API via OAuth credentials.
    """

    def __init__(
        self,
        credentials_path: Optional[str] = None,
        token_path: Optional[str] = None,
    ):
        """
        Initialize the calendar service.

        Args:
            credentials_path: Path to Google OAuth client credentials JSON.
            token_path: Path to OAuth token JSON.
        """
        credentials_value = credentials_path or os.getenv(
            "GOOGLE_CREDENTIALS_PATH", "./google_api_server/credentials.json"
        )
        token_value = token_path or os.getenv("GOOGLE_TOKEN_PATH", "./google_api_server/token.json")
        self.credentials_path = self._resolve_path(credentials_value)
        self.token_path = self._resolve_path(token_value)
        self._service = None

    @staticmethod
    def _resolve_path(value: str) -> Path:
        path = Path(value).expanduser()
        if path.is_absolute():
            return path.resolve()
        return (PROJECT_ROOT / path).resolve()

    def _get_service(self):
        """Get or create the Google Calendar API service."""
        if self._service is not None:
            return self._service

        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            if not self.token_path.exists():
                raise FileNotFoundError(
                    f"Calendar token not found: {self.token_path}. "
                    "Complete OAuth authorization first."
                )

            creds = Credentials.from_authorized_user_file(str(self.token_path))
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                _safe_write_token(self.token_path, creds.to_json())

            self._service = build("calendar", "v3", credentials=creds)
            return self._service

        except ImportError:
            raise ImportError(
                "Google API dependencies not installed. "
                "Run: pip install google-api-python-client google-auth-oauthlib"
            )

    def list_events(
        self,
        max_results: int = 10,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List upcoming calendar events.

        Args:
            max_results: Maximum number of events to return.
            time_min: Start of time range (ISO format). Defaults to now.
            time_max: End of time range (ISO format).

        Returns:
            List of event dictionaries.
        """
        try:
            service = self._get_service()
            if not time_min:
                time_min = datetime.now(timezone.utc).isoformat()

            params = {
                "calendarId": "primary",
                "timeMin": time_min,
                "maxResults": max_results,
                "singleEvents": True,
                "orderBy": "startTime",
            }
            if time_max:
                params["timeMax"] = time_max

            result = service.events().list(**params).execute()
            events = result.get("items", [])

            return [
                {
                    "id": e.get("id"),
                    "summary": e.get("summary", ""),
                    "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "")),
                    "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date", "")),
                    "status": e.get("status", ""),
                    "attendees": [
                        a.get("email", "") for a in e.get("attendees", [])
                    ],
                    "location": e.get("location", ""),
                    "description": e.get("description", ""),
                }
                for e in events
            ]
        except FileNotFoundError as e:
            logger.warning("Calendar not configured: %s", e)
            return []
        except Exception as e:
            logger.error("Failed to list calendar events: %s", e)
            return []

    def find_available_slots(
        self,
        date: str,
        duration_minutes: int = 60,
        start_hour: int = 9,
        end_hour: int = 17,
    ) -> List[Dict[str, str]]:
        """
        Find available time slots on a given date.

        Args:
            date: Date string in YYYY-MM-DD format.
            duration_minutes: Required slot duration in minutes.
            start_hour: Business hours start (24h format).
            end_hour: Business hours end (24h format).

        Returns:
            List of available slot dictionaries with start and end times.
        """
        try:
            from dateutil import parser as date_parser

            duration_minutes = int(duration_minutes)
            if duration_minutes <= 0:
                return []

            target_date = date_parser.parse(date).date()
            day_start = datetime(
                target_date.year, target_date.month, target_date.day,
                start_hour, 0, 0, tzinfo=timezone.utc,
            )
            day_end = datetime(
                target_date.year, target_date.month, target_date.day,
                end_hour, 0, 0, tzinfo=timezone.utc,
            )

            # Get existing events for the day
            events = self.list_events(
                max_results=50,
                time_min=day_start.isoformat(),
                time_max=day_end.isoformat(),
            )

            # Parse busy times
            busy_times = []
            for event in events:
                try:
                    evt_start = date_parser.parse(event["start"])
                    evt_end = date_parser.parse(event["end"])
                    busy_times.append((evt_start, evt_end))
                except (ValueError, KeyError):
                    continue

            busy_times.sort(key=lambda x: x[0])

            # Find gaps
            available_slots = []
            current = day_start
            duration = timedelta(minutes=duration_minutes)

            for busy_start, busy_end in busy_times:
                if current + duration <= busy_start:
                    # There is a gap before this busy period
                    slot_end = min(busy_start, current + duration)
                    available_slots.append({
                        "start": current.isoformat(),
                        "end": slot_end.isoformat(),
                        "duration_minutes": duration_minutes,
                    })
                current = max(current, busy_end)

            # Check remaining time after last busy period
            if current + duration <= day_end:
                available_slots.append({
                    "start": current.isoformat(),
                    "end": (current + duration).isoformat(),
                    "duration_minutes": duration_minutes,
                })

            return available_slots

        except ImportError:
            logger.warning("python-dateutil not installed for slot finding.")
            return []
        except Exception as e:
            logger.error("Failed to find available slots: %s", e)
            return []

    def create_event(
        self,
        summary: str,
        start_time: str,
        end_time: str,
        description: str = "",
        location: str = "",
        attendees: Optional[List[str]] = None,
        send_notifications: bool = True,
    ) -> Dict[str, Any]:
        """
        Create a calendar event (interview).

        Args:
            summary: Event title.
            start_time: Start time in ISO format.
            end_time: End time in ISO format.
            description: Event description.
            location: Event location or meeting link.
            attendees: List of attendee email addresses.
            send_notifications: Whether to send email notifications.

        Returns:
            Dictionary with created event details.
        """
        try:
            service = self._get_service()

            event_body = {
                "summary": summary,
                "start": {"dateTime": start_time, "timeZone": "UTC"},
                "end": {"dateTime": end_time, "timeZone": "UTC"},
                "description": description,
                "location": location or "",
            }

            if attendees:
                event_body["attendees"] = [{"email": email} for email in attendees]

            insert_kwargs = {
                "calendarId": "primary",
                "body": event_body,
                "sendUpdates": "all" if send_notifications else "none",
            }
            event = service.events().insert(**insert_kwargs).execute()

            logger.info("Created calendar event: %s (id=%s)", summary, event.get("id"))
            return {
                "success": True,
                "event_id": event.get("id"),
                "summary": event.get("summary"),
                "start": event.get("start", {}).get("dateTime"),
                "end": event.get("end", {}).get("dateTime"),
                "html_link": event.get("htmlLink"),
            }

        except FileNotFoundError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error("Failed to create calendar event: %s", e)
            return {"success": False, "error": str(e)}

    def update_event(
        self,
        event_id: str,
        summary: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update an existing calendar event.

        Args:
            event_id: The event ID to update.
            summary: New event title.
            start_time: New start time.
            end_time: New end time.
            description: New description.
            location: New location.

        Returns:
            Dictionary with update result.
        """
        try:
            service = self._get_service()

            # Get existing event
            event = service.events().get(
                calendarId="primary", eventId=event_id
            ).execute()

            if summary:
                event["summary"] = summary
            if start_time:
                event["start"] = {"dateTime": start_time, "timeZone": "UTC"}
            if end_time:
                event["end"] = {"dateTime": end_time, "timeZone": "UTC"}
            if description is not None:
                event["description"] = description
            if location is not None:
                event["location"] = location

            updated = (
                service.events()
                .update(calendarId="primary", eventId=event_id, body=event)
                .execute()
            )

            return {
                "success": True,
                "event_id": updated.get("id"),
                "summary": updated.get("summary"),
            }

        except Exception as e:
            logger.error("Failed to update event %s: %s", event_id, e)
            return {"success": False, "error": str(e)}

    def cancel_event(self, event_id: str) -> Dict[str, Any]:
        """
        Cancel (delete) a calendar event.

        Args:
            event_id: The event ID to cancel.

        Returns:
            Dictionary with cancellation result.
        """
        try:
            service = self._get_service()
            service.events().delete(
                calendarId="primary", eventId=event_id
            ).execute()

            logger.info("Cancelled calendar event: %s", event_id)
            return {"success": True, "event_id": event_id, "status": "cancelled"}

        except Exception as e:
            logger.error("Failed to cancel event %s: %s", event_id, e)
            return {"success": False, "error": str(e)}

    def check_conflicts(
        self,
        start_time: str,
        end_time: str,
    ) -> Dict[str, Any]:
        """
        Check for scheduling conflicts in a time range.

        Args:
            start_time: Start time in ISO format.
            end_time: End time in ISO format.

        Returns:
            Dictionary with conflict information.
        """
        events = self.list_events(
            max_results=50,
            time_min=start_time,
            time_max=end_time,
        )

        return {
            "has_conflicts": len(events) > 0,
            "conflict_count": len(events),
            "conflicting_events": events,
        }


def create_calendar_tools(calendar_service: Optional[CalendarService] = None) -> List[Callable]:
    """
    Create calendar tool functions for agent use.

    Args:
        calendar_service: CalendarService instance (creates default if None).

    Returns:
        List of callable tool functions.
    """
    if calendar_service is None:
        calendar_service = CalendarService()

    def list_calendar_events(
        max_results: int = 10,
        date: str = "",
    ) -> str:
        """
        List upcoming calendar events.

        Args:
            max_results: Maximum number of events to return.
            date: Optional date filter in YYYY-MM-DD format.

        Returns:
            JSON array of upcoming events.
        """
        kwargs = {"max_results": max_results}
        if date:
            try:
                from dateutil import parser as dp
                d = dp.parse(date).date()
                kwargs["time_min"] = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=timezone.utc).isoformat()
                kwargs["time_max"] = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc).isoformat()
            except Exception:
                pass
        events = calendar_service.list_events(**kwargs)
        return json.dumps(events, indent=2)

    def find_available_interview_slots(
        date: str,
        duration_minutes: int = 60,
    ) -> str:
        """
        Find available time slots for scheduling an interview on a given date.

        Args:
            date: Date in YYYY-MM-DD format.
            duration_minutes: Required interview duration in minutes.

        Returns:
            JSON array of available time slots.
        """
        slots = calendar_service.find_available_slots(
            date=date, duration_minutes=duration_minutes
        )
        if not slots:
            return json.dumps({"message": "No available slots found for the given date.", "slots": []})
        return json.dumps({"slots": slots, "count": len(slots)}, indent=2)

    def create_interview_event(
        candidate_name: str,
        position: str,
        start_time: str,
        end_time: str,
        candidate_email: str = "",
        location: str = "Google Meet (link in invite)",
        description: str = "",
    ) -> str:
        """
        Create an interview event on Google Calendar.

        Args:
            candidate_name: Name of the candidate.
            position: Position being interviewed for.
            start_time: Interview start time in ISO format (e.g., 2025-03-15T10:00:00Z).
            end_time: Interview end time in ISO format.
            candidate_email: Candidate's email for calendar invite.
            location: Interview location or meeting link.
            description: Additional event description.

        Returns:
            JSON with created event details.
        """
        summary = f"Interview: {candidate_name} — {position}"
        if not description:
            description = (
                f"Interview for {position} position\n"
                f"Candidate: {candidate_name}\n"
                f"Email: {candidate_email}"
            )

        attendees = [candidate_email] if candidate_email else None
        result = calendar_service.create_event(
            summary=summary,
            start_time=start_time,
            end_time=end_time,
            description=description,
            location=location,
            attendees=attendees,
        )
        return json.dumps(result, indent=2)

    def check_calendar_conflicts(
        start_time: str,
        end_time: str,
    ) -> str:
        """
        Check for scheduling conflicts in a time range.

        Args:
            start_time: Start time in ISO format.
            end_time: End time in ISO format.

        Returns:
            JSON with conflict information.
        """
        result = calendar_service.check_conflicts(start_time, end_time)
        return json.dumps(result, indent=2)

    def update_calendar_event(
        event_id: str,
        summary: str = "",
        start_time: str = "",
        end_time: str = "",
        description: str = "",
    ) -> str:
        """
        Update an existing calendar event.

        Args:
            event_id: The event ID to update.
            summary: New event title (leave empty to keep current).
            start_time: New start time (leave empty to keep current).
            end_time: New end time (leave empty to keep current).
            description: New description (leave empty to keep current).

        Returns:
            JSON with update result.
        """
        result = calendar_service.update_event(
            event_id=event_id,
            summary=summary or None,
            start_time=start_time or None,
            end_time=end_time or None,
            description=description if description else None,
        )
        return json.dumps(result, indent=2)

    def cancel_calendar_event(event_id: str) -> str:
        """
        Cancel (delete) a calendar event.

        Args:
            event_id: The event ID to cancel.

        Returns:
            JSON with cancellation result.
        """
        result = calendar_service.cancel_event(event_id)
        return json.dumps(result, indent=2)

    return [
        list_calendar_events,
        find_available_interview_slots,
        create_interview_event,
        check_calendar_conflicts,
        update_calendar_event,
        cancel_calendar_event,
    ]
