"""Analyze job descriptions and extract key tailoring signals."""

import json
import logging

from models.job_models import JobListing
from utils.llm_client import llm_client

logger = logging.getLogger(__name__)


JD_ANALYSIS_PROMPT = """You are an expert career analyst and ATS optimizer.

Analyze the following job description and extract the most important information for tailoring a resume.

Return ONLY a JSON object with these keys:
- role_title: the exact job title
- company_name: the hiring organization if known
- key_skills: list of 8-12 technical skills explicitly mentioned or strongly implied
- required_experience: summary of required years and type of experience
- soft_skills: list of 3-5 soft skills emphasized
- keywords_for_ats: list of 10-15 important keywords to include in the resume
- resume_title: a concise, ATS-friendly professional title for the candidate (e.g., "Data Analyst | SQL | Python | Power BI | Statistics")
- summary_focus: 2-3 sentences describing what the resume summary should emphasize for this role
- top_achievements_to_highlight: 3-5 bullet themes from typical data analyst experience that should be emphasized
- cover_letter_angle: 1-2 sentences on the main value proposition to the employer

Job Description:
{description}

Requirements:
{requirements}
"""


class JDAnalyzer:
    """Extract structured insights from a job description."""

    def analyze(self, job: JobListing) -> dict:
        """Return a structured analysis of the job listing."""
        prompt = JD_ANALYSIS_PROMPT.format(
            description=job.description or "Not provided",
            requirements=job.requirements or "Not provided",
        )
        messages = [
            {
                "role": "system",
                "content": "You are a precise career analyst. Return only valid JSON.",
            },
            {"role": "user", "content": prompt},
        ]
        raw = llm_client.chat(messages, temperature=0.2)

        try:
            # Extract JSON if wrapped in markdown fences
            text = raw.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            return json.loads(text.strip())
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse JD analysis JSON: %s\nRaw: %s", exc, raw)
            raise


if __name__ == "__main__":
    sample = JobListing(
        title="Data Analyst",
        company="State of Texas",
        location="Austin, TX",
        description="We are seeking a data analyst to analyze public health data...",
        requirements="Bachelor's degree, 2 years of SQL and Python experience...",
    )
    analyzer = JDAnalyzer()
    print(json.dumps(analyzer.analyze(sample), indent=2))
