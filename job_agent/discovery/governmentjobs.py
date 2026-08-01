"""GovernmentJobs.com discovery source."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from loguru import logger

from job_agent.discovery.base import JobDiscoverySource
from job_agent.models import JobApplication

# Track A scraper is in a different package layout; import it at runtime so
# relative top-level imports inside job_application_system resolve.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
import sys
sys.path.insert(0, str(_PROJECT_ROOT / "job_application_system"))
from agents.scraper import GovernmentJobsScraper


class GovernmentJobsDiscovery(JobDiscoverySource):
    """Discover jobs from governmentjobs.com via the existing Track A scraper."""

    name = "governmentjobs"

    def __init__(self, max_states: int | None = None, max_pages_per_state: int = 1):
        self.max_states = max_states
        self.max_pages_per_state = max_pages_per_state
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="govjobs_discover")

    async def discover(self, profile: dict) -> list[JobApplication]:
        preferences = profile.get("preferences", {})
        target_roles = preferences.get("target_roles", ["Data Analyst"])
        # The scraper uses the first target role as its title filter.
        title_filter = target_roles[0] if target_roles else ""

        loop = asyncio.get_running_loop()
        listings = await loop.run_in_executor(
            self._executor,
            self._scrape_sync,
            title_filter,
        )

        jobs = []
        for listing in listings:
            job = JobApplication(
                title=listing.title,
                company=listing.company,
                url=listing.application_url or "",
                location=listing.location,
                source=self.name,
                platform="governmentjobs",
            )
            if self._matches_preferences(job, profile):
                jobs.append(job)

        logger.info(f"GovernmentJobs discovery returned {len(jobs)} matching jobs")
        return jobs

    def _scrape_sync(self, title_filter: str) -> list[Any]:
        scraper = GovernmentJobsScraper(
            headless=True,
            max_states=self.max_states,
            max_pages_per_state=self.max_pages_per_state,
            title_filter=title_filter.lower(),
        )
        return scraper.scrape(login=False)

    def __del__(self):
        self._executor.shutdown(wait=False)
