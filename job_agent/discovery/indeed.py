"""Indeed job discovery via RSS feed.

Indeed uses strong anti-bot protections and its API terms limit automated
access. This source uses the public RSS feed with rotating user agents and
polite request pacing. It is disabled by default and must be enabled with
`preferences.enable_indeed_discovery: true` in profile.json.

For production-scale extraction, prefer the Indeed Publisher API or a manual
job feed.
"""
from __future__ import annotations

import asyncio
import urllib.parse
from typing import Any

import httpx
from fake_useragent import UserAgent
from loguru import logger

from job_agent.discovery.base import JobDiscoverySource
from job_agent.models import JobApplication
from job_agent.utils.circuit_breaker import domain_breaker_registry


def _strip_html_tags(value: str) -> str:
    """Remove simple HTML tags from a string after decoding HTML entities."""
    import html as _html
    import re

    decoded = _html.unescape(value)
    return re.sub(r"<[^>]+>", "", decoded).strip()


class IndeedDiscovery(JobDiscoverySource):
    """Indeed discovery source — disabled by default."""

    name = "indeed"

    # Public Indeed RSS endpoint.
    RSS_URL = "https://www.indeed.com/rss"

    def __init__(self, max_results: int = 25, request_timeout: float = 30.0):
        self.max_results = max(max_results, 1)
        self.request_timeout = request_timeout
        self._ua = UserAgent(browsers=["Chrome", "Edge", "Firefox"], platforms=["desktop"])

    async def discover(self, profile: dict) -> list[JobApplication]:
        preferences = profile.get("preferences", {})
        enabled = bool(preferences.get("enable_indeed_discovery", False))
        if not enabled:
            logger.info("Indeed discovery is disabled by default; skipping")
            return []

        breaker = domain_breaker_registry.get("indeed.com")
        if breaker.state.value == "open":
            logger.warning("Circuit breaker open for indeed.com; skipping Indeed discovery")
            return []

        target_roles = preferences.get("target_roles", ["Data Analyst"])
        target_locations = preferences.get("target_locations", ["United States"])
        keywords = target_roles[0] if target_roles else "Data Analyst"
        location = target_locations[0] if target_locations else "United States"

        jobs: list[JobApplication] = []
        try:
            jobs = await self._fetch_rss(keywords, location)
            breaker.record_success()
        except Exception as exc:
            logger.warning(f"Indeed discovery failed: {exc}")
            breaker.record_failure()

        filtered = [job for job in jobs if self._matches_preferences(job, profile)]
        logger.info(f"Indeed discovery returned {len(filtered)} matching jobs ({len(jobs)} raw)")
        return filtered

    async def _fetch_rss(self, keywords: str, location: str) -> list[JobApplication]:
        params = {
            "q": keywords,
            "l": location,
            "limit": min(self.max_results, 25),
            "sort": "date",
        }
        headers = {
            "User-Agent": self._ua.random,
            "Accept": "application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

        async with httpx.AsyncClient(timeout=self.request_timeout, follow_redirects=True) as client:
            response = await client.get(
                self.RSS_URL,
                params=params,
                headers=headers,
            )
            response.raise_for_status()
            await asyncio.sleep(1.0)
            return self._parse_rss(response.text)

    def _parse_rss(self, text: str) -> list[JobApplication]:
        jobs: list[JobApplication] = []
        try:
            from xml.etree import ElementTree as ET

            root = ET.fromstring(text.encode("utf-8"))
        except Exception as exc:
            logger.debug(f"Could not parse Indeed RSS: {exc}")
            return jobs

        # Standard RSS channel/item structure.
        channel = root.find("channel")
        if channel is None:
            return jobs

        for item in channel.findall("item"):
            try:
                title_el = item.find("title")
                title = _strip_html_tags(title_el.text or "") if title_el is not None else ""

                link_el = item.find("link")
                url = link_el.text.strip() if link_el is not None and link_el.text else ""

                # Indeed RSS description often contains company and location info
                # in a format like: "Company Name<br>Location".
                desc_el = item.find("description")
                raw_description = desc_el.text or "" if desc_el is not None else ""
                company, location = self._extract_company_location(raw_description)
                description = _strip_html_tags(raw_description)

                if not title or not url:
                    continue

                jobs.append(
                    JobApplication(
                        title=title,
                        company=company,
                        url=url,
                        location=location,
                        source=self.name,
                        platform="indeed",
                    )
                )
            except Exception as exc:
                logger.debug(f"Skipping malformed Indeed RSS item: {exc}")
                continue

        return jobs[: self.max_results]

    def _extract_company_location(self, description: str) -> tuple[str, str]:
        """Try to pull company and location out of the RSS description."""
        import html as _html
        import re

        company = ""
        location = ""
        if not description:
            return company, location

        # Decode entities, normalize <br> tags to newlines, then strip remaining HTML.
        decoded = _html.unescape(description)
        with_newlines = re.sub(r"<br\s*/?>", "\n", decoded, flags=re.IGNORECASE)
        cleaned = _strip_html_tags(with_newlines)

        parts = [p.strip() for p in cleaned.split("\n") if p.strip()]
        if len(parts) >= 2:
            company = parts[0]
            location = parts[1]
        elif parts:
            company = parts[0]
        return company, location
