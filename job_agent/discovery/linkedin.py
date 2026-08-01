"""LinkedIn job discovery via public guest API.

LinkedIn aggressively blocks automated scraping and its Terms of Service
restrict automated collection. This source uses the public jobs-guest API with
modest request volume, rate-limit-aware pacing, and rotating user agents. It is
disabled by default and must be enabled with
`preferences.enable_linkedin_discovery: true` in profile.json.

For production-scale extraction, prefer a paid API or manual job feed.
"""
from __future__ import annotations

import asyncio
import urllib.parse
from typing import Any

import httpx
from fake_useragent import UserAgent
from loguru import logger
from lxml import html

from job_agent.discovery.base import JobDiscoverySource
from job_agent.models import JobApplication
from job_agent.utils.circuit_breaker import domain_breaker_registry


class LinkedInDiscovery(JobDiscoverySource):
    """LinkedIn discovery source — disabled by default."""

    name = "linkedin"

    # Public LinkedIn jobs-guest endpoint. Returns HTML cards of job postings.
    SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

    def __init__(self, max_results: int = 25, request_timeout: float = 30.0):
        self.max_results = max(max_results, 1)
        self.request_timeout = request_timeout
        self._ua = UserAgent(browsers=["Chrome", "Edge", "Firefox"], platforms=["desktop"])

    async def discover(self, profile: dict) -> list[JobApplication]:
        preferences = profile.get("preferences", {})
        enabled = bool(preferences.get("enable_linkedin_discovery", False))
        if not enabled:
            logger.info("LinkedIn discovery is disabled by default; skipping")
            return []

        breaker = domain_breaker_registry.get("linkedin.com")
        if breaker.state.value == "open":
            logger.warning("Circuit breaker open for linkededin.com; skipping LinkedIn discovery")
            return []

        target_roles = preferences.get("target_roles", ["Data Analyst"])
        target_locations = preferences.get("target_locations", ["United States"])
        # Use the first role and first location as the primary search.
        keywords = target_roles[0] if target_roles else "Data Analyst"
        location = target_locations[0] if target_locations else "United States"

        jobs: list[JobApplication] = []
        try:
            jobs = await self._fetch_listings(keywords, location)
            breaker.record_success()
        except Exception as exc:
            logger.warning(f"LinkedIn discovery failed: {exc}")
            breaker.record_failure()

        # Filter against the full target role list and location preferences.
        filtered = [job for job in jobs if self._matches_preferences(job, profile)]
        logger.info(f"LinkedIn discovery returned {len(filtered)} matching jobs ({len(jobs)} raw)")
        return filtered

    async def _fetch_listings(self, keywords: str, location: str) -> list[JobApplication]:
        jobs: list[JobApplication] = []
        # LinkedIn's seeMore endpoint paginates by a single start parameter.
        # We fetch a few small pages to stay polite.
        page_size = 10
        pages = (self.max_results + page_size - 1) // page_size

        async with httpx.AsyncClient(timeout=self.request_timeout, follow_redirects=True) as client:
            for page in range(pages):
                start = page * page_size
                params = {
                    "keywords": keywords,
                    "location": location,
                    "start": start,
                    "count": page_size,
                }
                headers = {
                    "User-Agent": self._ua.random,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                    "Accept-Encoding": "gzip, deflate, br",
                    "DNT": "1",
                    "Connection": "keep-alive",
                }

                try:
                    response = await client.get(
                        self.SEARCH_URL,
                        params=params,
                        headers=headers,
                    )
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    logger.warning(f"LinkedIn page {page} HTTP error: {exc.response.status_code}")
                    break

                page_jobs = self._parse_html(response.text)
                if not page_jobs:
                    break
                jobs.extend(page_jobs)

                # Polite pacing between paginated requests.
                if page < pages - 1:
                    await asyncio.sleep(2.0)

                if len(jobs) >= self.max_results:
                    break

        return jobs[: self.max_results]

    def _parse_html(self, text: str) -> list[JobApplication]:
        jobs: list[JobApplication] = []
        try:
            tree = html.fromstring(text)
        except Exception as exc:
            logger.debug(f"Could not parse LinkedIn HTML: {exc}")
            return jobs

        # LinkedIn job cards are typically <div class="base-card"> elements.
        cards = tree.xpath('//div[contains(@class, "base-card")]')
        for card in cards:
            try:
                title_el = card.xpath('.//h3[contains(@class, "base-search-card__title")]')
                title = title_el[0].text_content().strip() if title_el else ""

                company_el = card.xpath('.//h4[contains(@class, "base-search-card__subtitle")]')
                company = company_el[0].text_content().strip() if company_el else ""

                location_el = card.xpath('.//span[contains(@class, "job-search-card__location")]')
                location = location_el[0].text_content().strip() if location_el else ""

                link_el = card.xpath('.//a[contains(@class, "base-card__full-link")]/@href')
                url = link_el[0].strip() if link_el else ""

                if not title or not url:
                    continue

                # Normalize URL to the public job view.
                url = urllib.parse.urljoin("https://www.linkedin.com/jobs/", url)

                jobs.append(
                    JobApplication(
                        title=title,
                        company=company,
                        url=url,
                        location=location,
                        source=self.name,
                        platform="linkedin",
                    )
                )
            except Exception as exc:
                logger.debug(f"Skipping malformed LinkedIn card: {exc}")
                continue

        return jobs
