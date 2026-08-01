"""Lever job-board discovery via the public API."""
import re
from typing import Any

from loguru import logger

from job_agent.discovery.base import JobDiscoverySource
from job_agent.models import JobApplication
from job_agent.scrapling_client import get_scrapling_client


class LeverDiscovery(JobDiscoverySource):
    """Discover jobs from Lever public postings API.

    Lever exposes public job listings at:
        https://api.lever.co/v0/postings/{site}?mode=json

    The profile should list Lever site slugs under
    preferences.lever_sites (e.g., ["fivetran", "notion"]).
    """

    name = "lever"

    def __init__(self, site_slugs: list[str] | None = None, timeout: int = 30):
        self.site_slugs = site_slugs or []
        self.timeout = timeout
        self.client = get_scrapling_client()

    async def discover(self, profile: dict) -> list[JobApplication]:
        preferences = profile.get("preferences", {})
        slugs = preferences.get("lever_sites", self.site_slugs)
        if not slugs:
            logger.warning("No Lever site slugs configured; skipping discovery")
            return []

        target_roles = preferences.get("target_roles", [])
        jobs: list[JobApplication] = []

        for slug in slugs:
            try:
                postings = self._fetch_site(slug)
            except Exception as exc:
                logger.warning(f"Failed to fetch Lever site {slug}: {exc}")
                continue

            for posting in postings:
                job = self._posting_to_job(posting, slug)
                if target_roles and not self._matches_preferences(job, profile):
                    continue
                jobs.append(job)

        logger.info(f"Lever discovery returned {len(jobs)} matching jobs")
        return jobs

    def _fetch_site(self, slug: str) -> list[dict[str, Any]]:
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        response = self.client.fetch(url)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []

    def _posting_to_job(self, posting: dict[str, Any], site: str) -> JobApplication:
        text = posting.get("text", "")
        categories = posting.get("categories", {}) or {}
        location_parts = [
            categories.get("location"),
            categories.get("commitment"),
        ]
        location = ", ".join(part for part in location_parts if part)
        hosted_url = posting.get("hostedUrl", "")
        apply_url = posting.get("applyUrl", "")
        url = hosted_url or apply_url or f"https://jobs.lever.co/{site}"

        description = self._build_description(posting)

        return JobApplication(
            title=text,
            company=site,
            url=url,
            location=location or None,
            description=description,
            source=self.name,
            platform="lever",
        )

    @staticmethod
    def _build_description(posting: dict[str, Any]) -> str:
        parts: list[str] = []
        description = posting.get("description", "")
        if description:
            # Strip HTML tags roughly.
            parts.append(re.sub(r"<[^>]+>", "", description))

        lists = posting.get("lists", [])
        for item in lists:
            text = item.get("text", "")
            content = item.get("content", "")
            if text:
                parts.append(f"{text}:")
            if content:
                parts.append(re.sub(r"<[^>]+>", "", content))

        additional = posting.get("additional", "")
        if additional:
            parts.append(re.sub(r"<[^>]+>", "", additional))

        return "\n\n".join(parts)
