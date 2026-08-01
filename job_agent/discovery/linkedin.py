"""LinkedIn job discovery scaffolding.

LinkedIn aggressively blocks automated scraping and its Terms of Service
restrict automated collection. By default this source is disabled. Enable it
only via `preferences.enable_linkedin_discovery: true` in profile.json, and
expect to use a Chrome extension, residential proxies, or a paid API for any
real extraction.
"""
from loguru import logger

from job_agent.discovery.base import JobDiscoverySource
from job_agent.models import JobApplication


class LinkedInDiscovery(JobDiscoverySource):
    """LinkedIn discovery source — disabled by default."""

    name = "linkedin"

    async def discover(self, profile: dict) -> list[JobApplication]:
        preferences = profile.get("preferences", {})
        enabled = bool(preferences.get("enable_linkedin_discovery", False))
        if not enabled:
            logger.info("LinkedIn discovery is disabled by default; skipping")
            return []

        logger.warning(
            "LinkedIn discovery is enabled but not implemented in this phase. "
            "Use a Chrome extension, paid API, or manual job list to feed LinkedIn jobs."
        )
        return []
