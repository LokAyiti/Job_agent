"""Registry for job discovery sources."""
from job_agent.discovery.base import JobDiscoverySource
from job_agent.discovery.governmentjobs import GovernmentJobsDiscovery
from job_agent.discovery.greenhouse import GreenhouseDiscovery


class DiscoveryRegistry:
    """Collect and run configured job discovery sources."""

    def __init__(self):
        self._sources: dict[str, type[JobDiscoverySource]] = {
            "governmentjobs": GovernmentJobsDiscovery,
            "greenhouse": GreenhouseDiscovery,
        }

    def list_sources(self) -> list[str]:
        return list(self._sources.keys())

    def get(self, name: str, profile: dict) -> JobDiscoverySource:
        cls = self._sources.get(name)
        if cls is None:
            raise ValueError(f"Unknown discovery source: {name}")
        if name == "greenhouse":
            tokens = profile.get("preferences", {}).get("greenhouse_boards", [])
            return cls(board_tokens=tokens)
        return cls()

    def discover_all(self, profile: dict, sources: list[str] | None = None) -> list:
        """Run discovery across all requested sources."""
        import asyncio

        sources = sources or self.list_sources()
        instances = [self.get(name, profile) for name in sources]
        # Run sequentially to avoid overwhelming external services.
        results = []
        for source in instances:
            jobs = asyncio.get_event_loop().run_until_complete(source.discover(profile))
            results.extend(jobs)
        return results
