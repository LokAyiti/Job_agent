"""Indeed job discovery scaffolding.

Indeed uses strong anti-bot protections and its API terms limit automated
access. By default this source is disabled. Enable it only via
`preferences.enable_indeed_discovery: true` in profile.json, and expect to use
a Chrome extension, residential proxies, or the Indeed API for real extraction.
"""
from loguru import logger

from job_agent.discovery.base import JobDiscoverySource
from job_agent.models import JobApplication


class IndeedDiscovery(JobDiscoverySource):
    """Indeed discovery source — disabled by default."""

    name = "indeed"

    async def discover(self, profile: dict) -> list[JobApplication]:
        preferences = profile.get("preferences", {})
        enabled = bool(preferences.get("enable_indeed_discovery", False))
        if not enabled:
            logger.info("Indeed discovery is disabled by default; skipping")
            return []

        logger.warning(
            "Indeed discovery is enabled but not implemented in this phase. "
            "Use a Chrome extension, paid API, or manual job list to feed Indeed jobs."
        )
        return []
