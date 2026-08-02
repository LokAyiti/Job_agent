"""Capture and archive job descriptions alongside tailored resumes.

Each tailored resume gets a paired job-description archive:
  - <resume_stem>_jd.md     clean, searchable text
  - <resume_stem>_jd.html   raw/fetched HTML snapshot for fidelity

Both files live in ``settings.job_descriptions_dir`` and are linked to the
resume PDF by matching filename stems.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup
from loguru import logger

from job_agent.config import Settings, get_settings
from job_agent.models import JobApplication


class JobDescriptionCapture:
    """Fetch and save a job posting as Markdown + HTML snapshots."""

    REQUEST_TIMEOUT = 20

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def capture(
        self, job: JobApplication, resume_pdf_path: Path
    ) -> tuple[Optional[Path], Optional[Path]]:
        """Save the JD as ``.md`` and ``.html`` archives next to the resume.

        Returns ``(md_path, html_path)``. The paths are also written back onto
        ``job.jd_path`` and ``job.jd_html_path`` so they can be logged.
        """
        self.settings.job_descriptions_dir.mkdir(parents=True, exist_ok=True)

        stem = resume_pdf_path.stem
        md_path = self.settings.job_descriptions_dir / f"{stem}_jd.md"
        html_path = self.settings.job_descriptions_dir / f"{stem}_jd.html"

        html_content: Optional[str] = None
        text_content: Optional[str] = None

        # Prefer the description already extracted by discovery agents.
        if job.description:
            parts = [job.description]
            if job.requirements:
                parts.append(f"\n## Requirements\n\n{job.requirements}")
            text_content = "\n\n".join(parts).strip()

        # Fetch a live copy for the HTML archive and as a fallback text source.
        if job.url:
            fetched_html = self._fetch_url(job.url)
            if fetched_html:
                html_content = fetched_html
                if not text_content:
                    text_content = self._html_to_text(fetched_html)

        if html_content is None:
            # No HTML snapshot possible; wrap the text description in a minimal page.
            html_content = self._wrap_text_in_html(job, text_content or "")

        if text_content is None:
            text_content = "Job description not available."

        frontmatter = self._build_frontmatter(job, resume_pdf_path, md_path, html_path)
        md_path.write_text(f"{frontmatter}\n\n{text_content}\n", encoding="utf-8")
        html_path.write_text(html_content, encoding="utf-8")

        job.jd_path = md_path
        job.jd_html_path = html_path

        logger.info(f"Saved job description archive: {md_path}, {html_path}")
        return md_path, html_path

    def _fetch_url(self, url: str) -> Optional[str]:
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            resp = requests.get(url, headers=headers, timeout=self.REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.text
        except Exception as exc:
            logger.warning(f"Could not fetch job description HTML from {url}: {exc}")
            return None

    def _html_to_text(self, html: str) -> str:
        try:
            soup = BeautifulSoup(html, "html.parser")
            # Drop non-content tags that add noise.
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n")
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            return "\n\n".join(lines)
        except Exception as exc:
            logger.warning(f"Could not convert HTML to text: {exc}")
            return ""

    def _wrap_text_in_html(self, job: JobApplication, text_content: str) -> str:
        escaped = (
            text_content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{job.title} at {job.company}</title>
</head>
<body>
<h1>{job.title}</h1>
<h2>{job.company}</h2>
<p><strong>URL:</strong> <a href="{job.url}">{job.url}</a></p>
<p><strong>Captured:</strong> {datetime.now().isoformat()}</p>
<pre>{escaped}</pre>
</body>
</html>"""

    def _build_frontmatter(
        self,
        job: JobApplication,
        resume_pdf_path: Path,
        md_path: Path,
        html_path: Path,
    ) -> str:
        lines = [
            "---",
            f"title: {job.title}",
            f"company: {job.company}",
            f"location: {job.location or ''}",
            f"url: {job.url}",
            f"resume_pdf: {resume_pdf_path}",
            f"jd_html: {html_path}",
            f"captured_at: {datetime.now().isoformat()}",
            "---",
        ]
        return "\n".join(lines)
