"""
Email Classification Engine

Auto-classifies incoming emails into categories:
- APPLICATION: New job application with resume
- FOLLOW_UP: Follow-up on existing application
- INQUIRY: General inquiry about positions
- INTERVIEW_RESPONSE: Response to interview invitation
- OTHER: Unclassified

Extracts applicant information and routes to appropriate workflows.
"""
import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class EmailCategory(str, Enum):
    """Email classification categories."""
    APPLICATION = "APPLICATION"
    FOLLOW_UP = "FOLLOW_UP"
    INQUIRY = "INQUIRY"
    INTERVIEW_RESPONSE = "INTERVIEW_RESPONSE"
    OTHER = "OTHER"


@dataclass
class ClassificationResult:
    """Result of email classification."""
    category: EmailCategory
    confidence: float
    extracted_name: str
    extracted_email: str
    extracted_phone: str
    extracted_position: str
    has_attachment: bool
    keywords_matched: List[str]
    suggested_action: str

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "category": self.category.value,
            "confidence": round(self.confidence, 2),
            "extracted_name": self.extracted_name,
            "extracted_email": self.extracted_email,
            "extracted_phone": self.extracted_phone,
            "extracted_position": self.extracted_position,
            "has_attachment": self.has_attachment,
            "keywords_matched": self.keywords_matched,
            "suggested_action": self.suggested_action,
        }


# Keyword patterns for each category
APPLICATION_KEYWORDS = [
    "application", "applying", "apply", "resume", "cv", "cover letter",
    "position", "job opening", "vacancy", "candidate", "attached resume",
    "attached cv", "interest in the role", "interest in the position",
    "enclosed resume", "please find attached", "for your consideration",
]

FOLLOW_UP_KEYWORDS = [
    "follow up", "following up", "follow-up", "checking in", "status update",
    "any update", "heard back", "application status", "next steps",
    "touching base", "circling back", "wanted to check",
]

INQUIRY_KEYWORDS = [
    "inquiry", "inquire", "interested in", "openings", "opportunities",
    "available positions", "hiring", "looking for", "career",
    "is there", "do you have", "are you hiring", "job listing",
]

INTERVIEW_RESPONSE_KEYWORDS = [
    "interview", "available for", "confirm the interview", "schedule works",
    "time slot", "looking forward to the interview", "accept the invitation",
    "reschedule", "cannot make it", "alternative time", "interview confirmation",
    "calendar invite", "meeting confirmed",
]


class EmailClassifier:
    """
    Rule-based email classifier with keyword matching and pattern analysis.
    Classifies emails and extracts applicant information.
    """

    def __init__(self):
        """Initialize the classifier with keyword patterns."""
        self.category_keywords = {
            EmailCategory.APPLICATION: APPLICATION_KEYWORDS,
            EmailCategory.FOLLOW_UP: FOLLOW_UP_KEYWORDS,
            EmailCategory.INQUIRY: INQUIRY_KEYWORDS,
            EmailCategory.INTERVIEW_RESPONSE: INTERVIEW_RESPONSE_KEYWORDS,
        }

    def classify(
        self,
        subject: str = "",
        body: str = "",
        from_email: str = "",
        from_name: str = "",
        has_attachment: bool = False,
    ) -> ClassificationResult:
        """
        Classify an email into a category.

        Args:
            subject: Email subject line.
            body: Email body text.
            from_email: Sender email address.
            from_name: Sender display name.
            has_attachment: Whether the email has attachments.

        Returns:
            ClassificationResult with category, confidence, and extracted data.
        """
        combined_text = f"{subject} {body}".lower()

        # Score each category
        scores: Dict[EmailCategory, float] = {}
        matched_keywords: Dict[EmailCategory, List[str]] = {}

        for category, keywords in self.category_keywords.items():
            matches = [kw for kw in keywords if kw in combined_text]
            matched_keywords[category] = matches
            base_score = len(matches) / max(len(keywords), 1)

            # Boost for attachments on APPLICATION
            if category == EmailCategory.APPLICATION and has_attachment:
                base_score += 0.25

            # Boost for subject-line matches (more intentional)
            subject_lower = subject.lower()
            subject_matches = sum(1 for kw in keywords if kw in subject_lower)
            base_score += subject_matches * 0.1

            scores[category] = min(base_score, 1.0)

        # Determine best category
        best_category = max(scores, key=lambda c: scores[c])
        best_score = scores[best_category]

        if best_score < 0.05:
            best_category = EmailCategory.OTHER
            best_score = 1.0 - max(scores.values()) if scores else 0.5

        # Extract applicant information
        extracted_name = self._extract_name(from_name, body)
        extracted_email = self._extract_email(from_email, body)
        extracted_phone = self._extract_phone(body)
        extracted_position = self._extract_position(subject, body)

        # Determine suggested action
        suggested_action = self._suggest_action(best_category, has_attachment)

        return ClassificationResult(
            category=best_category,
            confidence=best_score,
            extracted_name=extracted_name,
            extracted_email=extracted_email,
            extracted_phone=extracted_phone,
            extracted_position=extracted_position,
            has_attachment=has_attachment,
            keywords_matched=matched_keywords.get(best_category, []),
            suggested_action=suggested_action,
        )

    def _extract_name(self, from_name: str, body: str) -> str:
        """Extract applicant name from sender or body."""
        if from_name and from_name.strip():
            # Clean common email name formats
            name = from_name.strip().strip('"').strip("'")
            if "@" not in name:
                return name

        # Try to extract from body (common patterns)
        patterns = [
            r"(?:my name is|i am|i'm)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
            r"(?:sincerely|regards|best),?\s*\n\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                return match.group(1).strip()

        return from_name or ""

    def _extract_email(self, from_email: str, body: str) -> str:
        """Extract email address."""
        if from_email and "@" in from_email:
            return from_email.strip()
        match = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", body)
        return match.group(0) if match else ""

    def _extract_phone(self, body: str) -> str:
        """Extract phone number from body."""
        match = re.search(
            r"(?:\+?\d{1,3}[\s\-]?)?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{4}", body
        )
        return match.group(0).strip() if match else ""

    def _extract_position(self, subject: str, body: str) -> str:
        """Extract the position being applied for."""
        combined = f"{subject} {body}"
        patterns = [
            r"(?:applying for|application for|interest in|position of|role of)\s+(?:the\s+)?([A-Za-z\s]+?)(?:\s+position|\s+role|\s+opening|\.|,|\n)",
            r"(?:re:|subject:)\s*(?:application|apply)\s*[-:]\s*([A-Za-z\s]+?)(?:\n|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, combined, re.IGNORECASE)
            if match:
                position = match.group(1).strip()
                if len(position) < 60:
                    return position
        return ""

    def _suggest_action(self, category: EmailCategory, has_attachment: bool) -> str:
        """Suggest next action based on classification."""
        actions = {
            EmailCategory.APPLICATION: (
                "Run new_application_workflow: classify, extract resume, score, "
                "create candidate profile, send acknowledgment email."
            ),
            EmailCategory.FOLLOW_UP: (
                "Look up existing candidate in pipeline, provide status update, "
                "or escalate if candidate not found."
            ),
            EmailCategory.INQUIRY: (
                "Send available positions information, or route to HR for response."
            ),
            EmailCategory.INTERVIEW_RESPONSE: (
                "Update interview status in pipeline, confirm or reschedule "
                "interview in calendar."
            ),
            EmailCategory.OTHER: (
                "Review manually or archive."
            ),
        }
        return actions.get(category, "Review manually.")


def create_classifier_tools(classifier: EmailClassifier) -> list:
    """
    Create tool functions for agent use.

    Args:
        classifier: EmailClassifier instance.

    Returns:
        List of callable tool functions.
    """

    def classify_email(
        subject: str = "",
        body: str = "",
        from_email: str = "",
        from_name: str = "",
        has_attachment: bool = False,
    ) -> str:
        """
        Classify an incoming email into a recruitment category.

        Categories: APPLICATION, FOLLOW_UP, INQUIRY, INTERVIEW_RESPONSE, OTHER.

        Args:
            subject: Email subject line.
            body: Email body text.
            from_email: Sender's email address.
            from_name: Sender's display name.
            has_attachment: Whether the email has attachments.

        Returns:
            JSON with classification result including category, confidence,
            extracted applicant info, and suggested action.
        """
        result = classifier.classify(
            subject=subject,
            body=body,
            from_email=from_email,
            from_name=from_name,
            has_attachment=has_attachment,
        )
        return json.dumps(result.to_dict(), indent=2)

    return [classify_email]
