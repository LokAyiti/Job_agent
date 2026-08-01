"""Tests for site adapters and the registry."""
import pytest

from job_agent.sites.base import AdapterRegistry, FormChallenge
from job_agent.sites.governmentjobs import GovernmentJobsAdapter
from job_agent.sites.greenhouse import GreenhouseAdapter
from job_agent.sites.icims import iCIMSAdapter
from job_agent.sites.registry import build_default_registry
from job_agent.sites.workday import WorkdayAdapter


class FakeLocator:
    def __init__(self, count_value: int, body_text: str = ""):
        self._count_value = count_value
        self._body_text = body_text

    async def count(self) -> int:
        return self._count_value

    async def inner_text(self) -> str:
        return self._body_text


class FakePage:
    def __init__(self, url: str, counts: dict[str, int] | None = None, body_text: str = ""):
        self.url = url
        self._counts = counts or {}
        self._body_text = body_text

    def locator(self, selector: str):
        return FakeLocator(
            self._counts.get(selector, 0),
            self._body_text if selector == "body" else "",
        )

    async def wait_for_selector(self, selector: str, **kwargs):
        # Normalize visible selector to the same key used in counts.
        key = selector.replace(":visible", "")
        if self._counts.get(key, 0) > 0:
            return object()
        raise TimeoutError()

    async def inner_text(self) -> str:
        return self._body_text


@pytest.fixture
def registry():
    return build_default_registry()


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://boards.greenhouse.io/gradial/jobs/4338065009", "greenhouse"),
        ("https://company.wd101.myworkdayjobs.com/en-US/job/123", "workday"),
        ("https://company.myworkdayjobs.com/en-US/job/123", "workday"),
        ("https://company.icims.com/jobs/123?mode=job", "icims"),
        ("https://company.applicantpro.com/jobs/123", "icims"),
        ("https://www.governmentjobs.com/jobs/5259259-0/meter-data-analyst", "governmentjobs"),
    ],
)
def test_registry_detects_platform(registry, url, expected):
    assert registry.detect_platform(url) == expected


def test_registry_get_adapter(registry):
    adapter = registry.get_adapter("https://boards.greenhouse.io/example/jobs/123")
    assert isinstance(adapter, GreenhouseAdapter)

    adapter = registry.get_adapter("https://company.wd101.myworkdayjobs.com/job/123")
    assert isinstance(adapter, WorkdayAdapter)

    adapter = registry.get_adapter("https://company.icims.com/jobs/123")
    assert isinstance(adapter, iCIMSAdapter)

    adapter = registry.get_adapter("https://www.governmentjobs.com/jobs/5259259-0/meter-data-analyst")
    assert isinstance(adapter, GovernmentJobsAdapter)


@pytest.mark.asyncio
async def test_governmentjobs_adapter_interface():
    adapter = GovernmentJobsAdapter()
    assert adapter.name() == "governmentjobs"
    assert adapter.platform_name() == "governmentjobs"

    # Login required when a password field is visible.
    login_page = FakePage(
        "https://www.governmentjobs.com/Applications/Submitted",
        counts={"input#sign-in-password-field": 1},
    )
    assert await adapter.is_login_required(login_page) is True

    # Already logged in when the application wizard is shown.
    apply_page = FakePage(
        "https://www.governmentjobs.com/jobs/5259259-0/meter-data-analyst/apply",
        counts={"text='Applying as:'": 1},
    )
    assert await adapter._is_logged_in(apply_page) is True

    # Not logged in when neither sign-out nor applying-as is present.
    landing_page = FakePage("https://www.governmentjobs.com/jobs/123")
    assert await adapter._is_logged_in(landing_page) is False


def test_registry_unknown_url_raises():
    registry = AdapterRegistry()
    with pytest.raises(ValueError, match="No site adapter registered"):
        registry.get_adapter("https://unknown-site.example.com/jobs/123")


@pytest.mark.asyncio
async def test_greenhouse_adapter_interface():
    adapter = GreenhouseAdapter()
    assert adapter.name() == "greenhouse"
    assert adapter.platform_name() == "greenhouse"

    page = FakePage("https://boards.greenhouse.io/example/jobs/123")
    assert await adapter.is_login_required(page) is False


@pytest.mark.asyncio
async def test_greenhouse_detect_challenges_flags_non_application_page():
    adapter = GreenhouseAdapter()
    page = FakePage("https://boards.greenhouse.io/example/")
    with pytest.raises(FormChallenge, match="does not appear to be a Greenhouse application form"):
        await adapter.detect_challenges(page)


@pytest.mark.asyncio
async def test_greenhouse_detect_challenges_allows_application_page():
    adapter = GreenhouseAdapter()
    page = FakePage(
        "https://boards.greenhouse.io/example/jobs/123",
        counts={"#first_name": 1},
        body_text="Apply to this job",
    )
    # Should not raise.
    assert await adapter.detect_challenges(page) is None


@pytest.mark.asyncio
async def test_greenhouse_detect_challenges_flags_unsupported_flow():
    adapter = GreenhouseAdapter()
    page = FakePage(
        "https://boards.greenhouse.io/example/jobs/123",
        counts={"#first_name": 1},
        body_text="Apply with LinkedIn",
    )
    with pytest.raises(FormChallenge, match="Unsupported Greenhouse flow"):
        await adapter.detect_challenges(page)


@pytest.mark.asyncio
async def test_workday_adapter_interface():
    adapter = WorkdayAdapter()
    assert adapter.name() == "workday"
    assert adapter.platform_name() == "workday"

    page = FakePage(
        "https://company.wd101.myworkdayjobs.com/login",
        counts={'input[type="password"]': 1},
    )
    assert await adapter.is_login_required(page) is True


@pytest.mark.asyncio
async def test_icims_adapter_interface():
    adapter = iCIMSAdapter()
    assert adapter.name() == "icims"
    assert adapter.platform_name() == "icims"

    page = FakePage(
        "https://company.icims.com/login",
        counts={'input[type="password"]': 1},
    )
    assert await adapter.is_login_required(page) is True


def test_empty_adapter_registry_detect_platform_is_unknown():
    registry = AdapterRegistry()
    assert registry.detect_platform("https://anything.com") == "unknown"
