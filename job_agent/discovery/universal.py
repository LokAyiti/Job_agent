"""Universal job discovery using the Scrapling generic spider.

This source is not tied to a single job board. It accepts any list of career
page URLs and runs the Scrapling spider to discover job postings using adaptive
selectors and common ATS patterns. It is the default fallback when a board does
not have a dedicated adapter.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from loguru import logger

from job_agent.discovery.base import JobDiscoverySource
from job_agent.models import JobApplication
from job_agent.scrapling_client import ScraplingServiceError, get_scrapling_client


class UniversalDiscovery(JobDiscoverySource):
    """Generic discovery source for arbitrary job-board/career-page URLs."""

    name = "universal"

    def __init__(self, urls: list[str] | None = None, max_depth: int = 1, concurrent_requests: int = 4):
        self.urls = urls or []
        self.max_depth = max_depth
        self.concurrent_requests = concurrent_requests
        self.client = get_scrapling_client()

    async def discover(self, profile: dict) -> list[JobApplication]:
        preferences = profile.get("preferences", {})
        urls = preferences.get("universal_urls", self.urls)
        if not urls:
            logger.warning("No universal discovery URLs configured; skipping")
            return []

        target_roles = preferences.get("target_roles", [])
        jobs: list[JobApplication] = []
        seen: set[str] = set()

        for url in urls:
            try:
                items = self._run_for_url(url)
            except ScraplingServiceError as exc:
                logger.warning(f"Universal discovery failed for {url}: {exc}")
                continue
            except Exception as exc:
                logger.warning(f"Universal discovery unexpected error for {url}: {exc}")
                continue

            for item in items:
                title = item.get("title", "")
                job_url = item.get("url", "")
                if not title or not job_url:
                    continue
                if job_url in seen:
                    continue
                seen.add(job_url)

                job = JobApplication(
                    title=title,
                    company=self._extract_company(job_url),
                    url=job_url,
                    location=None,
                    description=None,
                    source=self.name,
                    platform=item.get("platform"),
                )
                if target_roles and not self._matches_preferences(job, profile):
                    continue
                jobs.append(job)

        logger.info(f"Universal discovery returned {len(jobs)} matching jobs")
        return jobs

    def _run_for_url(self, url: str) -> list[dict[str, Any]]:
        payload = {
            "start_urls": [url],
            "max_depth": self.max_depth,
            "use_stealth": True,
            "concurrent_requests": self.concurrent_requests,
            "crawldir": "./crawl_data",
        }
        return self.client.run_spider(payload)

    @staticmethod
    def _extract_company(url: str) -> str:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        parts = hostname.replace("www.", "").split(".")
        return parts[0].capitalize() if parts else "Unknown"
