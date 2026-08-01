"""Resume Retriever Agent — selects the best base resume template for a job."""
import re
from pathlib import Path
from typing import Any

from docx import Document
from loguru import logger

from models.job_models import JobListing


class ResumeRetriever:
    """Score base resume templates against a job and pick the best fit."""

    def __init__(self, base_resume_dir: Path, fallback_template: Path | None = None):
        self.base_resume_dir = Path(base_resume_dir)
        self.fallback_template = fallback_template

    def retrieve(self, job: JobListing, profile: dict[str, Any] | None = None) -> Path:
        """Return the best base resume template path for the job."""
        templates = self._list_templates()
        if not templates:
            if self.fallback_template and self.fallback_template.exists():
                logger.info(f"No templates in {self.base_resume_dir}; using fallback {self.fallback_template}")
                return self.fallback_template
            raise FileNotFoundError(f"No resume templates found in {self.base_resume_dir}")

        if len(templates) == 1:
            logger.info(f"Only one resume template available; using {templates[0]}")
            return templates[0]

        job_text = " ".join(
            str(field)
            for field in [job.title, job.description, job.requirements]
            if field
        )
        job_words = set(re.findall(r"[a-zA-Z0-9+#]+", job_text.lower()))

        scored = []
        for template in templates:
            score = self._score_template(template, job_words, profile)
            scored.append((score, template))
            logger.debug(f"Template {template.name} score: {score}")

        scored.sort(key=lambda x: x[0], reverse=True)
        best = scored[0][1]
        logger.info(f"Selected resume template {best.name} for {job.title} (score={scored[0][0]})")
        return best

    def _list_templates(self) -> list[Path]:
        if not self.base_resume_dir.exists():
            return []
        return [
            path
            for path in self.base_resume_dir.iterdir()
            if path.is_file() and path.suffix.lower() == ".docx"
        ]

    def _score_template(self, template: Path, job_words: set[str], profile: dict[str, Any] | None) -> int:
        score = 0
        # Filename keyword overlap.
        filename_words = set(re.findall(r"[a-zA-Z0-9+#]+", template.stem.lower()))
        score += len(job_words & filename_words) * 5

        # Text content overlap.
        try:
            doc = Document(str(template))
            content = " ".join(p.text for p in doc.paragraphs)
            content_words = set(re.findall(r"[a-zA-Z0-9+#]+", content.lower()))
            score += len(job_words & content_words) * 2
        except Exception as exc:
            logger.warning(f"Could not read template {template}: {exc}")

        # Profile target role overlap with filename.
        if profile:
            target_roles = profile.get("preferences", {}).get("target_roles", [])
            for role in target_roles:
                role_words = set(re.findall(r"[a-zA-Z0-9+#]+", role.lower()))
                if role_words & filename_words:
                    score += 10

        return score
