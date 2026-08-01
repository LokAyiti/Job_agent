"""ATS / Recruiter Scoring Agent — validates a tailored resume before export."""
import json
import logging
import re

from models.job_models import JobListing
from utils.llm_client import llm_client

logger = logging.getLogger(__name__)


SCORING_PROMPT = """You are an expert ATS recruiter. Evaluate the following tailored resume for the job below.

Score the resume on two dimensions from 0-100:
- ats_score: How parseable and keyword-aligned the resume is for an ATS.
- recruiter_score: How appealing and credible the resume is to a human recruiter.

Also provide a brief feedback string with 1-2 concrete improvements if any score is below 80.

Return ONLY a JSON object with keys:
- ats_score (int 0-100)
- recruiter_score (int 0-100)
- feedback (string, one or two sentences, or empty if both scores are 80+)

Job Title: {title}
Job Description/Requirements: {job_text}

Tailored Resume Content:
{resume_content}
"""


class ATSRecruiterScorer:
    """Score a drafted resume for ATS parseability and recruiter appeal."""

    def __init__(self, min_ats_score: int = 70, min_recruiter_score: int = 70):
        self.min_ats_score = min_ats_score
        self.min_recruiter_score = min_recruiter_score

    def score(self, content: dict, job: JobListing) -> dict:
        """Return dict with ats_score, recruiter_score, feedback, passed."""
        job_text = " ".join(
            str(field)
            for field in [job.title, job.description, job.requirements]
            if field
        )
        prompt = SCORING_PROMPT.format(
            title=job.title,
            job_text=job_text[:4000],
            resume_content=json.dumps(content, indent=2)[:4000],
        )
        messages = [
            {
                "role": "system",
                "content": "You evaluate resumes for ATS and recruiter quality. Return only valid JSON.",
            },
            {"role": "user", "content": prompt},
        ]
        raw = llm_client.chat(messages, temperature=0.3, max_tokens=512)
        return self._parse(raw)

    def _parse(self, raw: str) -> dict:
        text = raw.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{[^}]*\"ats_score\"[^}]*\}", text, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
            else:
                logger.warning(f"Could not parse ATS/recruiter score: {raw}")
                return {
                    "ats_score": 0,
                    "recruiter_score": 0,
                    "feedback": "Could not parse scorer response",
                    "passed": False,
                }

        ats = max(0, min(100, int(data.get("ats_score", 0))))
        rec = max(0, min(100, int(data.get("recruiter_score", 0))))
        feedback = str(data.get("feedback", "")).strip()
        passed = ats >= self.min_ats_score and rec >= self.min_recruiter_score
        return {
            "ats_score": ats,
            "recruiter_score": rec,
            "feedback": feedback,
            "passed": passed,
        }

    def passes(self, content: dict, job: JobListing) -> bool:
        return self.score(content, job)["passed"]
