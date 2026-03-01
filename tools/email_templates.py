"""
Email Template Engine

Professional recruitment email templates for:
- Application acknowledgment
- Interview invitation
- Interview confirmation
- Interview rescheduling
- Rejection notification
- Offer letter
- Follow-up request
- General inquiry response
"""
import json
import logging
from datetime import datetime
from string import Template
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


TEMPLATES: Dict[str, Dict[str, str]] = {
    "application_acknowledgment": {
        "subject": "Application Received — $position at $company",
        "body": """Dear $candidate_name,

Thank you for your interest in the $position position at $company. We have received your application and it is currently under review.

Our recruitment team will carefully evaluate your qualifications and experience. You can expect to hear from us within $response_days business days regarding the next steps in our selection process.

If you have any questions in the meantime, please do not hesitate to reach out.

Best regards,
$sender_name
$sender_title
$company
$sender_email""",
    },
    "interview_invitation": {
        "subject": "Interview Invitation — $position at $company",
        "body": """Dear $candidate_name,

We are pleased to inform you that after reviewing your application for the $position position, we would like to invite you for an interview.

Interview Details:
- Date: $interview_date
- Time: $interview_time
- Duration: $interview_duration
- Format: $interview_format
- Location/Link: $interview_location

Please confirm your availability by replying to this email at your earliest convenience. If the proposed time does not work for you, please suggest alternative times and we will do our best to accommodate.

We look forward to speaking with you.

Best regards,
$sender_name
$sender_title
$company
$sender_email""",
    },
    "interview_confirmation": {
        "subject": "Interview Confirmed — $position at $company",
        "body": """Dear $candidate_name,

This is to confirm your interview for the $position position at $company.

Confirmed Details:
- Date: $interview_date
- Time: $interview_time
- Duration: $interview_duration
- Format: $interview_format
- Location/Link: $interview_location

Please arrive 10 minutes early if the interview is in person. For virtual interviews, please ensure your camera and microphone are working properly.

If you need to reschedule, please contact us at least 24 hours in advance.

Best regards,
$sender_name
$sender_title
$company
$sender_email""",
    },
    "interview_reschedule": {
        "subject": "Interview Rescheduled — $position at $company",
        "body": """Dear $candidate_name,

We need to reschedule your interview for the $position position. We apologize for any inconvenience.

New Interview Details:
- Date: $interview_date
- Time: $interview_time
- Duration: $interview_duration
- Format: $interview_format
- Location/Link: $interview_location

Reason: $reschedule_reason

Please confirm the new time works for you. If not, please suggest alternatives.

Best regards,
$sender_name
$sender_title
$company
$sender_email""",
    },
    "rejection": {
        "subject": "Update on Your Application — $position at $company",
        "body": """Dear $candidate_name,

Thank you for your interest in the $position position at $company and for taking the time to apply.

After careful consideration, we have decided to move forward with other candidates whose qualifications more closely match our current needs. This decision does not reflect on your abilities, and we encourage you to apply for future openings that match your profile.

We wish you the best in your career endeavors.

Sincerely,
$sender_name
$sender_title
$company
$sender_email""",
    },
    "offer": {
        "subject": "Job Offer — $position at $company",
        "body": """Dear $candidate_name,

We are delighted to extend an offer for the $position position at $company.

Offer Details:
- Position: $position
- Start Date: $start_date
- Compensation: $compensation
- Benefits: $benefits

Please review the attached offer letter for complete details. We kindly request your response by $response_deadline.

If you have any questions about the offer, please do not hesitate to contact us.

We look forward to welcoming you to the team.

Best regards,
$sender_name
$sender_title
$company
$sender_email""",
    },
    "follow_up_request": {
        "subject": "Follow-Up: $position Application at $company",
        "body": """Dear $candidate_name,

We hope this message finds you well. We are following up regarding your application for the $position position at $company.

$follow_up_message

Please let us know if you have any questions or need additional information.

Best regards,
$sender_name
$sender_title
$company
$sender_email""",
    },
    "inquiry_response": {
        "subject": "Re: Inquiry About Opportunities at $company",
        "body": """Dear $candidate_name,

Thank you for your interest in $company. We appreciate you reaching out.

$inquiry_response

Current Open Positions:
$open_positions

To apply, please send your resume and cover letter to $application_email.

Best regards,
$sender_name
$sender_title
$company
$sender_email""",
    },
}

# Default values for template variables
DEFAULT_VALUES = {
    "company": "Kashmir World Foundation",
    "sender_name": "Recruitment Team",
    "sender_title": "Human Resources",
    "sender_email": "apply@kashmirworldfoundation.org",
    "response_days": "5-7",
    "interview_duration": "45 minutes",
    "interview_format": "Video Call (Google Meet)",
    "interview_location": "Link will be provided in calendar invite",
    "application_email": "apply@kashmirworldfoundation.org",
    "reschedule_reason": "Scheduling conflict",
    "follow_up_message": "We wanted to check in on the status of your application.",
    "inquiry_response": "We are always looking for talented individuals to join our team.",
    "open_positions": "Please visit our careers page for current openings.",
    "compensation": "Competitive, commensurate with experience",
    "benefits": "Health insurance, PTO, professional development",
    "start_date": "To be discussed",
    "response_deadline": "Two weeks from the date of this letter",
}


class EmailTemplateEngine:
    """
    Template engine for generating professional recruitment emails.
    Supports all standard recruitment communication templates with
    customizable variables and default values.
    """

    def __init__(self, custom_defaults: Optional[Dict[str, str]] = None):
        """
        Initialize the template engine.

        Args:
            custom_defaults: Override default template variable values.
        """
        self.defaults = {**DEFAULT_VALUES}
        if custom_defaults:
            self.defaults.update(custom_defaults)
        self.templates = TEMPLATES

    def list_templates(self) -> List[str]:
        """
        List all available template names.

        Returns:
            List of template name strings.
        """
        return list(self.templates.keys())

    def get_template_info(self, template_name: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a template including required variables.

        Args:
            template_name: Name of the template.

        Returns:
            Dictionary with template info or None if not found.
        """
        template = self.templates.get(template_name)
        if not template:
            return None

        # Extract variable names from template
        subject_vars = set()
        body_vars = set()
        for match in Template(template["subject"]).pattern.finditer(template["subject"]):
            name = match.group("named") or match.group("braced")
            if name:
                subject_vars.add(name)
        for match in Template(template["body"]).pattern.finditer(template["body"]):
            name = match.group("named") or match.group("braced")
            if name:
                body_vars.add(name)

        all_vars = subject_vars | body_vars
        required = [v for v in all_vars if v not in self.defaults]
        optional = [v for v in all_vars if v in self.defaults]

        return {
            "name": template_name,
            "subject_template": template["subject"],
            "required_variables": sorted(required),
            "optional_variables": sorted(optional),
        }

    def render(
        self,
        template_name: str,
        variables: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """
        Render a template with the given variables.

        Args:
            template_name: Name of the template to render.
            variables: Dictionary of variable values to substitute.

        Returns:
            Dictionary with 'subject' and 'body' keys.

        Raises:
            ValueError: If template not found or required variables missing.
        """
        template = self.templates.get(template_name)
        if not template:
            raise ValueError(
                f"Template '{template_name}' not found. "
                f"Available: {self.list_templates()}"
            )

        # Merge defaults with provided variables
        merged = {**self.defaults}
        if variables:
            merged.update(variables)

        try:
            subject = Template(template["subject"]).safe_substitute(merged)
            body = Template(template["body"]).safe_substitute(merged)
        except Exception as e:
            raise ValueError(f"Template rendering failed: {e}")

        return {"subject": subject, "body": body}


def create_template_tools(engine: EmailTemplateEngine) -> list:
    """
    Create tool functions for agent use.

    Args:
        engine: EmailTemplateEngine instance.

    Returns:
        List of callable tool functions.
    """

    def list_email_templates() -> str:
        """
        List all available email templates for recruitment communications.

        Returns:
            JSON array of template names with descriptions.
        """
        templates = engine.list_templates()
        info = []
        for name in templates:
            template_info = engine.get_template_info(name)
            info.append({
                "name": name,
                "required_variables": template_info["required_variables"] if template_info else [],
                "optional_variables": template_info["optional_variables"] if template_info else [],
            })
        return json.dumps(info, indent=2)

    def render_email_template(
        template_name: str,
        candidate_name: str = "",
        position: str = "",
        interview_date: str = "",
        interview_time: str = "",
        extra_variables: str = "{}",
    ) -> str:
        """
        Render a recruitment email template with provided variables.

        Available templates: application_acknowledgment, interview_invitation,
        interview_confirmation, interview_reschedule, rejection, offer,
        follow_up_request, inquiry_response.

        Args:
            template_name: Name of the template to render.
            candidate_name: Name of the candidate.
            position: Job position title.
            interview_date: Interview date (for interview templates).
            interview_time: Interview time (for interview templates).
            extra_variables: JSON object with additional template variables.

        Returns:
            JSON with rendered 'subject' and 'body'.
        """
        variables = {}
        if candidate_name:
            variables["candidate_name"] = candidate_name
        if position:
            variables["position"] = position
        if interview_date:
            variables["interview_date"] = interview_date
        if interview_time:
            variables["interview_time"] = interview_time

        try:
            extra = json.loads(extra_variables) if extra_variables and extra_variables != "{}" else {}
            variables.update(extra)
        except json.JSONDecodeError:
            pass

        try:
            result = engine.render(template_name, variables)
            return json.dumps(result, indent=2)
        except ValueError as e:
            return json.dumps({"error": str(e)})

    return [list_email_templates, render_email_template]
