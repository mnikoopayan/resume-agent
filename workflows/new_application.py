"""
New Application Workflow

End-to-end processing of incoming job applications:
1. Classify the incoming email
2. Extract resume data from attachments or body
3. Create candidate profile in pipeline database
4. Insert resume into knowledge base
5. Score candidate against job requirements
6. Send acknowledgment email
7. Advance candidate to SCREENING stage
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tools.candidate_db import CandidateDB
from tools.email_classifier import EmailClassifier, EmailCategory
from tools.email_templates import EmailTemplateEngine
from tools.gmail_tools import SmtpGmailSender
from tools.knowledge_tool import InsertKnowledgeTool
from tools.resume_scorer import ResumeScorer, JobRequirement

logger = logging.getLogger(__name__)


@dataclass
class ApplicationResult:
    """Result of processing a new application."""
    success: bool = False
    candidate_id: int = 0
    candidate_name: str = ""
    candidate_email: str = ""
    classification: str = ""
    score: float = 0.0
    recommendation: str = ""
    acknowledgment_sent: bool = False
    knowledge_inserted: bool = False
    stage: str = "NEW"
    errors: List[str] = field(default_factory=list)
    steps_completed: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "success": self.success,
            "candidate_id": self.candidate_id,
            "candidate_name": self.candidate_name,
            "candidate_email": self.candidate_email,
            "classification": self.classification,
            "score": self.score,
            "recommendation": self.recommendation,
            "acknowledgment_sent": self.acknowledgment_sent,
            "knowledge_inserted": self.knowledge_inserted,
            "stage": self.stage,
            "errors": self.errors,
            "steps_completed": self.steps_completed,
        }


class NewApplicationWorkflow:
    """
    Orchestrates the full new-application pipeline from email receipt
    through acknowledgment.
    """

    def __init__(
        self,
        candidate_db: CandidateDB,
        classifier: EmailClassifier,
        scorer: ResumeScorer,
        template_engine: EmailTemplateEngine,
        smtp_sender: SmtpGmailSender,
        knowledge_tool: Optional[InsertKnowledgeTool] = None,
        default_job_requirements: Optional[JobRequirement] = None,
        auto_send_acknowledgment: bool = True,
    ):
        """
        Initialize the workflow.

        Args:
            candidate_db: Candidate pipeline database.
            classifier: Email classifier.
            scorer: Resume scorer.
            template_engine: Email template engine.
            smtp_sender: SMTP email sender.
            knowledge_tool: Knowledge base insert tool.
            default_job_requirements: Default job requirements for scoring.
            auto_send_acknowledgment: Whether to auto-send acknowledgment emails.
        """
        self.candidate_db = candidate_db
        self.classifier = classifier
        self.scorer = scorer
        self.template_engine = template_engine
        self.smtp_sender = smtp_sender
        self.knowledge_tool = knowledge_tool
        self.default_job_requirements = default_job_requirements or JobRequirement()
        self.auto_send_acknowledgment = auto_send_acknowledgment

    def process(
        self,
        subject: str = "",
        body: str = "",
        from_email: str = "",
        from_name: str = "",
        has_attachment: bool = False,
        attachment_path: Optional[str] = None,
        skills: Optional[List[str]] = None,
        experience_years: int = 0,
        education: Optional[List[str]] = None,
        job_requirements: Optional[JobRequirement] = None,
    ) -> ApplicationResult:
        """
        Process a new application through the full workflow.

        Args:
            subject: Email subject.
            body: Email body text.
            from_email: Sender email.
            from_name: Sender name.
            has_attachment: Whether email has attachments.
            attachment_path: Path to resume file attachment.
            skills: Pre-extracted skills list (optional).
            experience_years: Pre-extracted experience years.
            education: Pre-extracted education entries.
            job_requirements: Job requirements for scoring.

        Returns:
            ApplicationResult with processing details.
        """
        result = ApplicationResult()

        # Step 1: Classify the email
        try:
            classification = self.classifier.classify(
                subject=subject,
                body=body,
                from_email=from_email,
                from_name=from_name,
                has_attachment=has_attachment,
            )
            result.classification = classification.category.value
            result.candidate_name = classification.extracted_name or from_name or from_email
            result.candidate_email = classification.extracted_email or from_email
            result.steps_completed.append("email_classified")
            logger.info(
                "Email classified as %s (confidence: %.2f)",
                result.classification, classification.confidence,
            )

            # If not an application, still create a record but note it
            if classification.category != EmailCategory.APPLICATION:
                logger.info(
                    "Email classified as %s, not APPLICATION. Processing anyway.",
                    classification.category.value,
                )
        except Exception as e:
            logger.error("Email classification failed: %s", e)
            result.errors.append(f"Classification failed: {e}")
            result.candidate_name = from_name or from_email
            result.candidate_email = from_email

        # Step 2: Create candidate profile
        try:
            candidate_id = self.candidate_db.create_candidate(
                name=result.candidate_name,
                email=result.candidate_email,
                phone=getattr(classification, "extracted_phone", "") if "classification" in dir() else "",
                source="email",
                job_title_applied=getattr(classification, "extracted_position", "") if "classification" in dir() else "",
                notes=f"Application via email: {subject}",
            )
            result.candidate_id = candidate_id
            result.steps_completed.append("candidate_created")
            logger.info("Created candidate #%d: %s", candidate_id, result.candidate_name)
        except Exception as e:
            logger.error("Failed to create candidate: %s", e)
            result.errors.append(f"Candidate creation failed: {e}")

        # Step 3: Insert resume into knowledge base
        if self.knowledge_tool:
            try:
                if attachment_path:
                    self.knowledge_tool.insert_knowledge(
                        file_path=attachment_path, source="application"
                    )
                    result.knowledge_inserted = True
                    result.steps_completed.append("attachment_ingested")
                elif body:
                    resume_text = (
                        f"Application from: {result.candidate_name}\n"
                        f"Email: {result.candidate_email}\n"
                        f"Subject: {subject}\n\n{body}"
                    )
                    self.knowledge_tool.insert_knowledge(
                        text=resume_text, source="application"
                    )
                    result.knowledge_inserted = True
                    result.steps_completed.append("body_ingested")
            except Exception as e:
                logger.error("Knowledge base insertion failed: %s", e)
                result.errors.append(f"Knowledge insertion failed: {e}")

        # Step 4: Score candidate
        try:
            req = job_requirements or self.default_job_requirements
            candidate_skills = skills or []
            candidate_education = education or []

            score_result = self.scorer.score_candidate(
                candidate_skills=candidate_skills,
                experience_years=experience_years,
                education_entries=candidate_education,
                resume_text=body,
                requirements=req,
            )
            result.score = score_result["composite_score"]
            result.recommendation = score_result["recommendation"]
            result.steps_completed.append("candidate_scored")

            # Update score in database
            if result.candidate_id:
                try:
                    self.candidate_db.update_candidate(
                        candidate_id=result.candidate_id,
                        score=result.score,
                        skills=json.dumps(candidate_skills),
                        experience_years=experience_years,
                    )
                    result.steps_completed.append("score_saved")
                except Exception as e:
                    logger.warning("Failed to update candidate score: %s", e)
                    result.errors.append(f"Score save failed: {e}")

            logger.info(
                "Candidate scored: %.2f (%s)", result.score, result.recommendation
            )
        except Exception as e:
            logger.error("Scoring failed: %s", e)
            result.errors.append(f"Scoring failed: {e}")

        # Step 5: Send acknowledgment email
        if self.auto_send_acknowledgment and result.candidate_email:
            try:
                position = ""
                if "classification" in dir() and hasattr(classification, "extracted_position"):
                    position = classification.extracted_position
                position = position or "Open Position"

                rendered = self.template_engine.render(
                    "application_acknowledgment",
                    {
                        "candidate_name": result.candidate_name,
                        "position": position,
                    },
                )
                send_result = self.smtp_sender.send_email(
                    to=result.candidate_email,
                    subject=rendered["subject"],
                    body=rendered["body"],
                )
                result.acknowledgment_sent = send_result.get("success", False)
                if result.acknowledgment_sent:
                    result.steps_completed.append("acknowledgment_sent")
                else:
                    result.errors.append(
                        f"Acknowledgment send failed: {send_result.get('error', 'unknown')}"
                    )
            except Exception as e:
                logger.error("Failed to send acknowledgment: %s", e)
                result.errors.append(f"Acknowledgment failed: {e}")

        # Step 6: Advance to SCREENING
        if result.candidate_id:
            try:
                self.candidate_db.advance_stage(result.candidate_id, "SCREENING")
                result.stage = "SCREENING"
                result.steps_completed.append("advanced_to_screening")
            except Exception as e:
                logger.warning("Failed to advance to SCREENING: %s", e)
                result.errors.append(f"Stage advance failed: {e}")

        result.success = len(result.errors) == 0
        logger.info(
            "Application workflow complete for %s. Success=%s, Steps=%s",
            result.candidate_name, result.success, result.steps_completed,
        )
        return result


def create_workflow_tools(workflow: NewApplicationWorkflow) -> list:
    """
    Create tool functions for the new application workflow.

    Args:
        workflow: NewApplicationWorkflow instance.

    Returns:
        List of callable tool functions.
    """

    def process_new_application(
        subject: str = "",
        body: str = "",
        from_email: str = "",
        from_name: str = "",
        has_attachment: bool = False,
        attachment_path: str = "",
        skills: str = "[]",
        experience_years: int = 0,
        education: str = "[]",
    ) -> str:
        """
        Process a new job application through the full workflow pipeline:
        classify → create candidate → ingest resume → score → acknowledge → advance.

        Args:
            subject: Email subject line.
            body: Email body text / resume content.
            from_email: Applicant's email address.
            from_name: Applicant's name.
            has_attachment: Whether the email has a resume attachment.
            attachment_path: Local file path to resume attachment.
            skills: JSON array of candidate skills.
            experience_years: Years of professional experience.
            education: JSON array of education entries.

        Returns:
            JSON with complete workflow result.
        """
        try:
            skills_list = json.loads(skills) if isinstance(skills, str) and skills.startswith("[") else []
        except json.JSONDecodeError:
            skills_list = []

        try:
            edu_list = json.loads(education) if isinstance(education, str) and education.startswith("[") else []
        except json.JSONDecodeError:
            edu_list = []

        app_result = workflow.process(
            subject=subject,
            body=body,
            from_email=from_email,
            from_name=from_name,
            has_attachment=has_attachment,
            attachment_path=attachment_path or None,
            skills=skills_list,
            experience_years=experience_years,
            education=edu_list,
        )
        return json.dumps(app_result.to_dict(), indent=2)

    return [process_new_application]
