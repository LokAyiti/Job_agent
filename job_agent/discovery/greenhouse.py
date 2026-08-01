"""Greenhouse job-board discovery via the public API."""
import requests
from loguru import logger

from job_agent.discovery.base import JobDiscoverySource
from job_agent.models import JobApplication


class GreenhouseDiscovery(JobDiscoverySource):
    """Discover jobs from Greenhouse public boards using the v1 API."""

    name = "greenhouse"

    def __init__(self, board_tokens: list[str] | None = None, timeout: int = 30):
        self.board_tokens = board_tokens or []
        self.timeout = timeout

    async def discover(self, profile: dict) -> list[JobApplication]:
        preferences = profile.get("preferences", {})
        tokens = preferences.get("greenhouse_boards", self.board_tokens)
        if not tokens:
            logger.warning("No Greenhouse board tokens configured; skipping discovery")
            return []

        target_roles = preferences.get("target_roles", [])
        jobs: list[JobApplication] = []

        for token in tokens:
            try:
                board_jobs = self._fetch_board(token)
            except Exception as exc:
                logger.warning(f"Failed to fetch Greenhouse board {token}: {exc}")
                continue

            for item in board_jobs:
                title = item.get("title", "")
                job = JobApplication(
                    title=title,
                    company=item.get("location", {}).get("name", token),
                    url=item.get("absolute_url", ""),
                    location=item.get("location", {}).get("name", ""),
                    source=self.name,
                    platform="greenhouse",
                )
                if target_roles and not self._matches_preferences(job, profile):
                    continue
                # Basic description for scoring if available.
                job.description = item.get("content", "") or item.get("description", "")
                jobs.append(job)

        logger.info(f"Greenhouse discovery returned {len(jobs)} matching jobs")
        return jobs

    def _fetch_board(self, token: str) -> list[dict]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        return data.get("jobs", [])
