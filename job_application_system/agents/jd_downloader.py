"""Download and save job description text/HTML alongside generated resumes.

This keeps a permanent record of the job description that was used to tailor a
resume, so the candidate can review it later even if the posting is taken down.
"""
import logging
from pathlib import Path
from urllib.parse import urlparse

import requests

from models.job_models import JobListing

logger = logging.getLogger(__name__)


class JDDownloader:
    """Persist job description text and optionally the original posting HTML."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save(self, job: JobListing, base_name: str) -> tuple[Path | None, Path | None]:
        """Save JD text and HTML snapshots.

        Returns (text_path, html_path).  HTML is only saved if it can be fetched
        from the application URL.
        """
        text_path = self._save_text(job, base_name)
        html_path = self._save_html(job, base_name)
        return text_path, html_path

    def _save_text(self, job: JobListing, base_name: str) -> Path:
        """Write description + requirements to a text file named like the resume."""
        path = self.output_dir / f"{base_name}.txt"
        lines = [
            f"Title: {job.title}",
            f"Company: {job.company}",
            f"Location: {job.location}",
            f"URL: {job.application_url}",
            f"Job ID: {job.job_id}",
            "",
            "=== DESCRIPTION ===",
            job.description or "",
            "",
            "=== REQUIREMENTS ===",
            job.requirements or "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Saved JD text: %s", path)
        return path

    def _save_html(self, job: JobListing, base_name: str) -> Path | None:
        """Try to fetch the original posting HTML and save it."""
        url = job.application_url
        if not url or not urlparse(url).scheme.startswith("http"):
            return None
        try:
            response = requests.get(url, timeout=30, headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                )
            })
            response.raise_for_status()
            path = self.output_dir / f"{base_name}.html"
            path.write_text(response.text, encoding="utf-8")
            logger.info("Saved JD HTML: %s", path)
            return path
        except Exception as exc:
            logger.warning("Could not fetch JD HTML for %s: %s", job.job_id, exc)
            return None
