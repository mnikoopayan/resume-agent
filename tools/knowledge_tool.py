"""
Enhanced Knowledge Insert Tool

Supports inserting text and files into the knowledge base with structured
resume data extraction during ingestion and automatic candidate profile creation.

Enhancements over sample:
- Structured resume data extraction (name, email, phone, skills, experience, education)
- Automatic candidate profile creation in pipeline DB on resume ingestion
- DOCX support preserved from sample
- Metadata tagging for source tracking
"""
import json
import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agno.knowledge.knowledge import Knowledge
from agno.knowledge.reader.pdf_reader import PDFReader
from agno.knowledge.reader.text_reader import TextReader

logger = logging.getLogger(__name__)

DocumentChunking = None
try:
    from agno.knowledge.chunking.document import DocumentChunking
except ImportError:
    try:
        from agno.knowledge.chunking.document_chunking import DocumentChunking
    except ImportError:
        pass


def extract_structured_resume_data(text: str) -> Dict[str, Any]:
    """
    Extract structured data from resume text using regex heuristics.

    Returns a dictionary with keys: name, email, phone, skills, experience_years,
    education, job_titles, companies, summary.
    """
    data: Dict[str, Any] = {
        "name": "",
        "email": "",
        "phone": "",
        "skills": [],
        "experience_years": 0,
        "education": [],
        "job_titles": [],
        "companies": [],
        "summary": "",
    }

    # Email extraction
    email_match = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
    if email_match:
        data["email"] = email_match.group(0).strip()

    # Phone extraction
    phone_match = re.search(
        r"(?:\+?\d{1,3}[\s\-]?)?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{4}", text
    )
    if phone_match:
        data["phone"] = phone_match.group(0).strip()

    # Name extraction — first non-empty line that is not an email or phone
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    for line in lines[:5]:
        if "@" in line or re.search(r"\d{3}", line):
            continue
        if len(line) < 60 and not any(
            kw in line.lower()
            for kw in ["resume", "curriculum", "objective", "summary", "experience", "education", "http"]
        ):
            data["name"] = line
            break

    # Skills extraction
    skill_keywords = [
        "python", "java", "javascript", "typescript", "react", "angular", "vue",
        "node.js", "nodejs", "django", "flask", "fastapi", "sql", "nosql",
        "mongodb", "postgresql", "mysql", "redis", "docker", "kubernetes",
        "aws", "azure", "gcp", "git", "ci/cd", "machine learning", "deep learning",
        "nlp", "data science", "data analysis", "tensorflow", "pytorch",
        "html", "css", "rest", "graphql", "agile", "scrum", "linux",
        "c++", "c#", "go", "rust", "ruby", "php", "swift", "kotlin",
        "excel", "tableau", "power bi", "spark", "hadoop", "airflow",
        "communication", "leadership", "project management", "teamwork",
    ]
    text_lower = text.lower()
    found_skills = [sk for sk in skill_keywords if sk in text_lower]
    data["skills"] = list(set(found_skills))

    # Experience years extraction
    year_patterns = re.findall(r"(20\d{2}|19\d{2})", text)
    if year_patterns:
        years = sorted(set(int(y) for y in year_patterns))
        if len(years) >= 2:
            data["experience_years"] = years[-1] - years[0]

    # Education extraction
    edu_keywords = [
        "bachelor", "master", "phd", "ph.d", "mba", "b.s.", "m.s.",
        "b.a.", "m.a.", "associate", "diploma", "certificate",
        "computer science", "engineering", "business", "mathematics",
    ]
    for line in lines:
        line_lower = line.lower()
        if any(kw in line_lower for kw in edu_keywords):
            data["education"].append(line.strip())

    # Job titles extraction
    title_keywords = [
        "engineer", "developer", "manager", "analyst", "designer",
        "architect", "consultant", "director", "lead", "intern",
        "specialist", "coordinator", "administrator", "scientist",
    ]
    for line in lines:
        line_lower = line.lower()
        if any(kw in line_lower for kw in title_keywords) and len(line) < 80:
            data["job_titles"].append(line.strip())

    # Summary — first 300 chars
    data["summary"] = text[:300].replace("\n", " ").strip()

    return data


class InsertKnowledgeTool:
    """
    Enhanced tool for inserting knowledge into the knowledge base.

    Supports text, PDF, TXT, and DOCX files. During resume ingestion,
    automatically extracts structured data and can create candidate profiles.
    """

    def __init__(
        self,
        knowledge_base: Knowledge,
        candidate_db: Optional[Any] = None,
    ):
        """
        Initialize the knowledge insert tool.

        Args:
            knowledge_base: Knowledge instance to insert into.
            candidate_db: Optional CandidateDB instance for auto-creating profiles.
        """
        self.knowledge_base = knowledge_base
        self.candidate_db = candidate_db

    @staticmethod
    def _extract_docx_text(path: Path) -> str:
        """Extract plain text from a .docx file without extra dependencies."""
        with zipfile.ZipFile(path) as archive:
            with archive.open("word/document.xml") as doc_xml:
                tree = ET.parse(doc_xml)

        root = tree.getroot()
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = []
        for paragraph in root.findall(".//w:p", namespace):
            texts = [node.text for node in paragraph.findall(".//w:t", namespace) if node.text]
            if texts:
                paragraphs.append("".join(texts))
        return "\n".join(paragraphs).strip()

    def _try_create_candidate_profile(self, text: str, source: str = "upload") -> Optional[Dict[str, Any]]:
        """
        Attempt to extract structured data and create a candidate profile.

        Returns the extracted data dict or None if extraction/creation fails.
        """
        if self.candidate_db is None:
            return None

        try:
            data = extract_structured_resume_data(text)
            if not data.get("name") and not data.get("email"):
                logger.debug("No name or email extracted; skipping candidate creation.")
                return data

            candidate_id = self.candidate_db.create_candidate(
                name=data.get("name", "Unknown"),
                email=data.get("email", ""),
                phone=data.get("phone", ""),
                skills=data.get("skills", []),
                experience_years=data.get("experience_years", 0),
                education=data.get("education", []),
                source=source,
                resume_text=text[:2000],
            )
            data["candidate_id"] = candidate_id
            logger.info("Auto-created candidate profile id=%s for '%s'", candidate_id, data.get("name"))
            return data
        except Exception as exc:
            logger.warning("Failed to auto-create candidate profile: %s", exc)
            return None

    def insert_knowledge(
        self,
        text: Optional[str] = None,
        file_path: Optional[str] = None,
        source: str = "manual",
        auto_profile: bool = True,
    ) -> str:
        """
        Insert knowledge into the knowledge base.

        Args:
            text: Raw text content to insert.
            file_path: Path to file (.txt, .pdf, .docx) to insert.
            source: Source label for tracking (e.g., 'upload', 'gmail', 'dropbox').
            auto_profile: Whether to attempt automatic candidate profile creation.

        Returns:
            Success message with details about what was inserted.
        """
        if not text and not file_path:
            raise ValueError("Either 'text' or 'file_path' must be provided")
        if text and file_path:
            raise ValueError("Provide either 'text' or 'file_path', not both")

        try:
            if text:
                self.knowledge_base.insert(text_content=text)
                logger.info("Inserted text content (%d characters)", len(text))
                profile_data = None
                if auto_profile:
                    profile_data = self._try_create_candidate_profile(text, source=source)
                msg = f"Successfully inserted text content ({len(text)} characters) into knowledge base"
                if profile_data and profile_data.get("candidate_id"):
                    msg += f" | Candidate profile created (id={profile_data['candidate_id']})"
                return msg

            elif file_path:
                path = Path(file_path)
                if not path.exists():
                    raise FileNotFoundError(f"File not found: {file_path}")

                ext = path.suffix.lower()
                raw_text = ""

                if ext == ".pdf":
                    chunking_strategy = DocumentChunking() if DocumentChunking is not None else None
                    reader = PDFReader(chunking_strategy=chunking_strategy) if chunking_strategy else PDFReader()
                    self.knowledge_base.insert(path=str(path), reader=reader)
                    logger.info("Inserted PDF file: %s", file_path)
                    # Try to extract text for profiling
                    try:
                        from pypdf import PdfReader as PyPdfReader
                        pdf = PyPdfReader(str(path))
                        raw_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
                    except Exception:
                        pass

                elif ext == ".txt":
                    reader = TextReader()
                    self.knowledge_base.insert(path=str(path), reader=reader)
                    logger.info("Inserted text file: %s", file_path)
                    try:
                        raw_text = path.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        pass

                elif ext == ".docx":
                    extracted_text = self._extract_docx_text(path)
                    if not extracted_text:
                        raise ValueError(f"No extractable text found in DOCX file: {file_path}")
                    docx_payload = f"Source file: {path.name}\n\n{extracted_text}"
                    self.knowledge_base.insert(text_content=docx_payload)
                    logger.info("Inserted DOCX file: %s", file_path)
                    raw_text = extracted_text

                else:
                    raise ValueError(
                        f"Unsupported file type: {ext}. Supported types: .txt, .pdf, .docx"
                    )

                profile_data = None
                if auto_profile and raw_text:
                    profile_data = self._try_create_candidate_profile(raw_text, source=source)

                msg = f"Successfully inserted {ext.upper().strip('.')} file '{path.name}' into knowledge base"
                if profile_data and profile_data.get("candidate_id"):
                    msg += f" | Candidate profile created (id={profile_data['candidate_id']})"
                return msg

        except Exception:
            logger.error("Failed to insert knowledge", exc_info=True)
            raise

        return "No action taken"

    def extract_resume_data(self, text: str) -> Dict[str, Any]:
        """
        Public method to extract structured resume data without inserting.

        Args:
            text: Resume text content.

        Returns:
            Dictionary of extracted structured data.
        """
        return extract_structured_resume_data(text)


def create_knowledge_insert_tools(knowledge_tool: InsertKnowledgeTool) -> List[Callable]:
    """
    Create tool functions that can be used by Agent.

    Args:
        knowledge_tool: InsertKnowledgeTool instance.

    Returns:
        List of tool functions for Agent.
    """

    def insert_text(text: str) -> str:
        """
        Insert text content into the knowledge base.
        Use this tool when the user wants to add text information to the knowledge base.

        Args:
            text: The text content to insert into the knowledge base.

        Returns:
            Success message confirming the text was inserted.
        """
        return knowledge_tool.insert_knowledge(text=text)

    def insert_file(file_path: str) -> str:
        """
        Insert a file (.pdf, .txt, or .docx) from the dropbox folder into the knowledge base.
        Use this tool when the user wants to add a file to the knowledge base.
        The file should be in the dropbox folder (default: ./dropbox/).

        Args:
            file_path: Path to the file to insert. Can be relative or absolute.

        Returns:
            Success message confirming the file was inserted.
        """
        path = Path(file_path)
        if not path.is_absolute():
            dropbox_path = Path("./dropbox") / file_path
            if dropbox_path.exists():
                file_path = str(dropbox_path)
        return knowledge_tool.insert_knowledge(file_path=file_path)

    def extract_resume_data(text: str) -> str:
        """
        Extract structured data from resume text without inserting into the knowledge base.
        Returns JSON with name, email, phone, skills, experience_years, education, etc.

        Args:
            text: The resume text to analyze.

        Returns:
            JSON string with extracted structured data.
        """
        data = knowledge_tool.extract_resume_data(text)
        return json.dumps(data, indent=2)

    return [insert_text, insert_file, extract_resume_data]
