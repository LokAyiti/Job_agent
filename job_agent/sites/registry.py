"""Central registry for all site adapters."""
from job_agent.sites.base import AdapterRegistry
from job_agent.sites.greenhouse import GreenhouseAdapter
from job_agent.sites.icims import iCIMSAdapter
from job_agent.sites.workday import WorkdayAdapter


def build_default_registry() -> AdapterRegistry:
    """Return a registry with all built-in platform adapters."""
    registry = AdapterRegistry()
    registry.register(GreenhouseAdapter)
    registry.register(WorkdayAdapter)
    registry.register(iCIMSAdapter)
    return registry
