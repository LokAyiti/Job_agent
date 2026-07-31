"""Tailor resume content based on a job description and the base resume."""

import json
import logging
from pathlib import Path

from docx import Document

from models.job_models import JobListing
from utils.llm_client import llm_client
from agents.jd_analyzer import JDAnalyzer

logger = logging.getLogger(__name__)


class ResumeTailor:
    """Generate tailored resume content from a base resume and a job listing."""

    def __init__(self, base_resume_path: Path) -> None:
        self.base_resume_path = base_resume_path
        self.base_resume_text = self._extract_text(base_resume_path)
        self.jd_analyzer = JDAnalyzer()

    def _extract_text(self, path: Path) -> str:
        """Extract all text from the base resume docx."""
        doc = Document(str(path))
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        tables = []
        for table in doc.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    tables.append(" | ".join(cells))
        return "\n\n".join(paragraphs + tables)

    def tailor(self, job: JobListing) -> dict:
        """Return tailored resume content as a structured dict."""
        job_analysis = self.jd_analyzer.analyze(job)
        prompt = f"""You are an expert resume writer and ATS optimizer.

You will receive:
1. A base resume
2. A job description analysis

Your task is to tailor the resume for the specific job while preserving factual accuracy and the candidate's real experience.

Return ONLY a JSON object with these exact keys:
- professional_title: A concise title line for the resume (e.g., "Data Analyst | SQL | Python | Power BI | Statistics | 6+ Years")
- professional_summary: 3-5 sentences summarizing the candidate as a strong fit for this role
- technical_skills: A list of skill categories matching the template. Each item is an object with {{category: str, skills: str}}. Use the same categories as the base resume if possible.
- experience: A list of work experiences. Each item is an object with {{job_header: str, bullets: [str, str, ...]}}. Keep the same job headers as the base resume where possible, but rewrite bullets to emphasize data analysis, SQL, reporting, visualization, and insights relevant to the target role.

Base Resume:
{self.base_resume_text}

Job Analysis:
{json.dumps(job_analysis, indent=2)}

Rules:
- Do not invent degrees, companies, or tools the candidate has not used.
- Emphasize data analysis, SQL, Python, Excel, Power BI/Tableau, reporting, dashboards, and insights.
- Use strong action verbs and quantify achievements where possible.
- Keep bullets concise (1-2 lines each).
- Keep the same overall resume structure and section headings.
- The professional title and summary should be specifically tailored to the target job title: {job.title}
"""
        messages = [
            {
                "role": "system",
                "content": "You are an expert resume writer. Return only valid JSON.",
            },
            {"role": "user", "content": prompt},
        ]
        raw = llm_client.chat(messages, temperature=0.4, max_tokens=4000)

        try:
            text = raw.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            return json.loads(text.strip())
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse tailored resume JSON: %s\nRaw: %s", exc, raw)
            raise


if __name__ == "__main__":
    from config.settings import Settings
    sample = JobListing(
        title="Data Analyst",
        company="State of Texas",
        location="Austin, TX",
        description="We are seeking a data analyst to analyze public health data...",
        requirements="Bachelor's degree, 2 years of SQL and Python experience...",
    )
    tailor = ResumeTailor(Settings.BASE_RESUME_TEMPLATE)
    print(json.dumps(tailor.tailor(sample), indent=2))
