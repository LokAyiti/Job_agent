"""Discovery source protocol for job boards."""
from abc import ABC, abstractmethod
from typing import Iterable

from job_agent.models import JobApplication


class JobDiscoverySource(ABC):
    """Pluggable source that discovers job postings and returns JobApplications."""

    name: str = "unknown"

    @abstractmethod
    async def discover(self, profile: dict) -> list[JobApplication]:
        """Return a list of jobs discovered from this source."""

    def _matches_preferences(self, job: JobApplication, profile: dict) -> bool:
        """Basic filter: title must match one of the target roles."""
        preferences = profile.get("preferences", {})
        target_roles = preferences.get("target_roles", [])
        if not target_roles:
            return True
        title_lower = job.title.lower()
        return any(role.lower() in title_lower for role in target_roles)
