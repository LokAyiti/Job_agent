"""Pluggable site adapter protocol."""
from abc import ABC, abstractmethod
from typing import Any

from playwright.async_api import Page

from job_agent.captcha import CaptchaSolver
from job_agent.models import Account, JobApplication


class FormChallenge(Exception):
    """Raised when the adapter detects CAPTCHA, login wall, or unsupported flow."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class SiteAdapter(ABC):
    """Abstract adapter for a career site/application form."""

    platform: str = ""

    @classmethod
    @abstractmethod
    def can_handle(cls, url: str) -> bool:
        """Return True if this adapter can process the given URL."""

    @abstractmethod
    def name(self) -> str:
        """Human-readable adapter name."""

    @abstractmethod
    def platform_name(self) -> str:
        """Canonical platform identifier (e.g., 'greenhouse', 'workday', 'icims')."""

    @abstractmethod
    async def is_login_required(self, page: Page) -> bool:
        """Return True if the current page is a login/signup gate."""

    @abstractmethod
    async def authenticate(
        self,
        page: Page,
        account: Account,
        create_account: bool = False,
    ) -> bool:
        """Log in or sign up on the platform. Returns True on success."""

    async def handle_captcha(self, page: Page, solver: CaptchaSolver) -> bool:
        """Attempt to solve any CAPTCHA on the page.

        Default implementation delegates to the shared CaptchaSolver. Adapters may
        override this for platform-specific injection requirements.
        """
        return await solver.solve_on_page(page)

    @abstractmethod
    async def parse_form(self, page: Page) -> dict[str, Any]:
        """Inspect the page and return a dict describing discovered form fields."""

    async def prepare_application(
        self,
        page: Page,
        job: JobApplication,
        account: Account | None,
    ) -> None:
        """Optional hook to navigate to the application page and authenticate.

        Called by the Submission Agent after the initial page load and before
        parse_form/fill_application. Adapters that show the login gate only on
        the apply page (e.g., GovernmentJobs) should override this.
        """

    @abstractmethod
    async def fill_application(
        self,
        page: Page,
        job: JobApplication,
        resume_path: str,
        profile: dict[str, str],
        dry_run: bool = False,
        form_schema: dict[str, Any] | None = None,
    ) -> None:
        """Fill the application form with profile info and upload the resume."""

    @abstractmethod
    async def submit(self, page: Page, dry_run: bool) -> bool:
        """Click the final submit button. If dry_run is True, do not click.

        Returns True if submission/dry-run reached the final step.
        """

    @abstractmethod
    async def detect_challenges(self, page: Page, dry_run: bool = False) -> None:
        """Raise FormChallenge if the page requires human intervention.

        Dry-run may bypass challenges to allow form verification without solving.
        """


class AdapterRegistry:
    def __init__(self):
        self._adapters: list[type[SiteAdapter]] = []

    def register(self, adapter_cls: type[SiteAdapter]) -> None:
        self._adapters.append(adapter_cls)

    def get_adapter(self, url: str) -> SiteAdapter:
        for adapter_cls in self._adapters:
            if adapter_cls.can_handle(url):
                return adapter_cls()
        raise ValueError(f"No site adapter registered for URL: {url}")

    def detect_platform(self, url: str) -> str:
        for adapter_cls in self._adapters:
            if adapter_cls.can_handle(url):
                return adapter_cls.platform or adapter_cls.__name__.lower().replace("adapter", "")
        return "unknown"
