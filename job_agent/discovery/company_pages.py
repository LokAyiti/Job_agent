"""Generic company career page discovery.

This source visits a configured list of company career pages and extracts
job links using common ATS patterns. It is intentionally lightweight and
experimental; complex JavaScript portals should use a dedicated source or the
Chrome-extension bridge.
"""
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from loguru import logger

from job_agent.discovery.base import JobDiscoverySource
from job_agent.models import JobApplication


# URL path/hostname fragments that strongly suggest a job posting.
JOB_LINK_PATTERNS = [
    re.compile(r"/jobs?/\d+", re.IGNORECASE),
    re.compile(r"/careers?/\d+", re.IGNORECASE),
    re.compile(r"/postings?/\d+", re.IGNORECASE),
    re.compile(r"/openings?/\d+", re.IGNORECASE),
    re.compile(r"/job/[^/]+", re.IGNORECASE),
    re.compile(r"greenhouse\.io/[^/]+/jobs/\d+", re.IGNORECASE),
    re.compile(r"lever\.co/[^/]+/\d+", re.IGNORECASE),
    re.compile(r"myworkdayjobs\.com/[^/]+/job/\d+", re.IGNORECASE),
    re.compile(r"icims\.com/jobs/\d+", re.IGNORECASE),
]

# Fragments that disqualify a URL (e.g., login, privacy, legal, events).
NOISE_PATTERNS = [
    re.compile(r"login|signin|auth|privacy|terms|legal|cookie|events?|webinars?", re.IGNORECASE),
]


class CompanyPagesDiscovery(JobDiscoverySource):
    """Discover jobs from configured company career pages."""

    name = "company_pages"

    def __init__(self, pages: list[str] | None = None, timeout: int = 30):
        self.pages = pages or []
        self.timeout = timeout

    async def discover(self, profile: dict) -> list[JobApplication]:
        preferences = profile.get("preferences", {})
        pages = preferences.get("company_career_pages", self.pages)
        if not pages:
            logger.warning("No company career pages configured; skipping discovery")
            return []

        target_roles = preferences.get("target_roles", [])
        jobs: list[JobApplication] = []
        seen_urls: set[str] = set()

        for page_url in pages:
            try:
                page_jobs = self._discover_page(page_url)
            except Exception as exc:
                logger.warning(f"Failed to discover jobs from {page_url}: {exc}")
                continue

            for job in page_jobs:
                if job.url in seen_urls:
                    continue
                seen_urls.add(job.url)
                if target_roles and not self._matches_preferences(job, profile):
                    continue
                jobs.append(job)

        logger.info(f"Company pages discovery returned {len(jobs)} matching jobs")
        return jobs

    def _discover_page(self, page_url: str) -> list[JobApplication]:
        response = requests.get(page_url, timeout=self.timeout, headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        })
        response.raise_for_status()
        html = response.text
        return self._parse_jobs_from_html(html, page_url)

    def _parse_jobs_from_html(self, html: str, page_url: str) -> list[JobApplication]:
        jobs: list[JobApplication] = []
        # Extract all anchor tags with href and link text.
        for match in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.IGNORECASE | re.DOTALL):
            href = match.group(1).strip()
            text = re.sub(r"<[^>]+>", "", match.group(2)).strip()
            if not href or not text:
                continue

            absolute_url = urljoin(page_url, href)
            if not self._looks_like_job_url(absolute_url):
                continue
            if self._is_noise_url(absolute_url):
                continue

            company = self._extract_company(page_url)
            title = self._clean_title(text)
            if not title:
                continue

            jobs.append(JobApplication(
                title=title,
                company=company,
                url=absolute_url,
                location=None,
                description=None,
                source=self.name,
                platform=self._detect_platform(absolute_url),
            ))

        return jobs

    def _looks_like_job_url(self, url: str) -> bool:
        return any(pattern.search(url) for pattern in JOB_LINK_PATTERNS)

    def _is_noise_url(self, url: str) -> bool:
        return any(pattern.search(url) for pattern in NOISE_PATTERNS)

    def _extract_company(self, page_url: str) -> str:
        parsed = urlparse(page_url)
        hostname = parsed.hostname or ""
        parts = hostname.replace("www.", "").split(".")
        return parts[0].capitalize() if parts else "Unknown"

    def _clean_title(self, text: str) -> str:
        # Remove excessive whitespace and common non-title fragments.
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"\s*(Apply now|Apply|Learn more|Read more)\s*$", "", text, flags=re.IGNORECASE)
        return text[:200]

    def _detect_platform(self, url: str) -> str | None:
        if "greenhouse" in url:
            return "greenhouse"
        if "lever" in url or "jobs.lever.co" in url:
            return "lever"
        if "myworkdayjobs" in url:
            return "workday"
        if "icims" in url or "applicantpro" in url:
            return "icims"
        if "governmentjobs" in url:
            return "governmentjobs"
        return None
