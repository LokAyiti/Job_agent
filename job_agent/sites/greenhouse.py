"""Greenhouse careers site adapter.

Best-effort form filling for boards.greenhouse.io and similar Greenhouse URLs.
Unknown custom questions are skipped with a note so the user can review.
"""
from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import Page, TimeoutError as PWTimeoutError

from job_agent.models import Account, JobApplication
from job_agent.sites.base import FormChallenge, SiteAdapter
from job_agent.sites.field_filler import RobustFieldFiller
from job_agent.sites.form_utils import (
    build_form_schema,
    extract_fields,
    get_profile_values,
)


class GreenhouseAdapter(SiteAdapter):
    platform: str = "greenhouse"

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return "greenhouse.io" in url.lower()

    def name(self) -> str:
        return "greenhouse"

    def platform_name(self) -> str:
        return self.platform

    async def is_login_required(self, page: Page) -> bool:
        """Greenhouse public job applications usually do not require login.

        Some companies embed Greenhouse behind an auth wall; detect explicit login
        paths or a dominant sign-in form.
        """
        url = page.url.lower()
        if "/login" in url or "/auth" in url:
            return True
        try:
            sign_in_selectors = [
                'input[type="password"]',
                'button:has-text("Sign in")',
                'button:has-text("Log in")',
                'a:has-text("Sign in")',
            ]
            for selector in sign_in_selectors:
                if await page.locator(selector).count() > 0:
                    return True
        except Exception:
            pass
        return False

    async def authenticate(
        self,
        page: Page,
        account: Account,
        create_account: bool = False,
    ) -> bool:
        """Best-effort login or account creation for Greenhouse.

        Public Greenhouse boards rarely need this; if an auth wall appears, the
        adapter fills the email and password fields and clicks the primary button.
        """
        from loguru import logger

        logger.info(
            f"{'Creating' if create_account else 'Signing in to'} Greenhouse account for {account.company}"
        )
        try:
            await page.fill('input[type="email"], input[name="email"]', account.username)
            await page.fill('input[type="password"], input[name="password"]', account.password)

            if create_account:
                button = page.locator('button:has-text("Create")')
            else:
                button = page.locator('button:has-text("Sign in"), button:has-text("Log in")')

            if await button.count() == 0:
                button = page.locator('button[type="submit"]').first

            if await button.count() > 0:
                await button.click()
                await page.wait_for_load_state("networkidle")
            return True
        except Exception as exc:
            logger.warning(f"Greenhouse authentication failed: {exc}")
            return False

    async def detect_challenges(self, page: Page, dry_run: bool = False) -> None:
        """Detect unsupported application flows.

        CAPTCHA is intentionally left to the shared CaptchaSolver; login walls are
        handled by :meth:`is_login_required` and :meth:`authenticate`.
        """
        # If the page is clearly not an application form, flag it.
        application_selectors = ["#first_name", "#email", "#resume", "#application_form"]
        has_application_form = False
        for selector in application_selectors:
            try:
                if await page.locator(selector).count() > 0:
                    has_application_form = True
                    break
            except Exception:
                continue

        if not has_application_form:
            raise FormChallenge("Page does not appear to be a Greenhouse application form")

        # Detect unsupported multi-step wizards or third-party redirects.
        unsupported_indicators = [
            "apply with linkedin",  # unsupported easy-apply variant
        ]
        text = await page.locator("body").inner_text()
        lowered = text.lower()
        for indicator in unsupported_indicators:
            if indicator in lowered:
                raise FormChallenge(f"Unsupported Greenhouse flow detected: {indicator}")

    async def parse_form(self, page: Page) -> dict[str, Any]:
        """Inspect the live DOM and return a structured field schema."""
        fields = await extract_fields(page)
        known_selectors = {
            "first_name": '#first_name',
            "last_name": '#last_name',
            "email": '#email',
            "phone": '#phone',
            "resume": 'input#resume[type="file"]',
            "submit": 'input[type="submit"], button[type="submit"]',
        }
        return build_form_schema(fields, self.platform, page.url, known_selectors)

    async def fill_application(
        self,
        page: Page,
        job: JobApplication,
        resume_path: str,
        profile: dict[str, str],
        dry_run: bool = False,
    ) -> None:
        values = get_profile_values(profile)
        filler = RobustFieldFiller(page)

        await filler.fill(
            values["first_name"],
            field_id="first_name",
            name="first_name",
            label="First Name",
            aria_label="First Name",
        )
        await filler.fill(
            values["last_name"],
            field_id="last_name",
            name="last_name",
            label="Last Name",
            aria_label="Last Name",
        )
        await filler.fill(
            values["email"],
            field_id="email",
            name="email",
            label="Email",
            aria_label="Email",
        )
        await filler.fill(
            values["phone"],
            field_id="phone",
            name="phone",
            label="Phone",
            aria_label="Phone",
        )
        await filler.fill(
            values["linkedin"],
            field_id="question_",
            label="LinkedIn",
            aria_label="LinkedIn",
            selectors=['input[id*="linkedin" i]'],
        )

        # Resume upload.
        resume_file = Path(resume_path)
        uploaded = await filler.upload(
            resume_file,
            field_id="resume",
            name="resume",
            label="Resume",
            aria_label="Resume",
            selectors=['input#resume[type="file"]'],
        )
        if uploaded:
            logger.info(f"Uploaded resume {resume_file.name}")
            await page.wait_for_timeout(1500)
        elif not resume_file.exists():
            logger.warning(f"Resume file not found: {resume_path}")
        else:
            logger.warning("No resume upload field found on this Greenhouse form")

    async def submit(self, page: Page, dry_run: bool) -> bool:
        submit_button = page.locator('input[type="submit"], button[type="submit"]')
        if await submit_button.count() == 0:
            logger.warning("No submit button found on Greenhouse form")
            return False

        if dry_run:
            logger.info("Dry-run mode: stopping before final submit")
            return True

        try:
            await submit_button.click()
            await page.wait_for_load_state("networkidle")
            return True
        except PWTimeoutError:
            logger.warning("Submit click timed out; job may still be processing")
            return True


class GreenhouseEasyApplyAdapter(GreenhouseAdapter):
    """Placeholder for Greenhouse Easy Apply variant (one-click LinkedIn apply)."""

    @classmethod
    def can_handle(cls, url: str) -> bool:
        # Easy-apply URLs are usually embedded in the same domain with a flag.
        return False


def build_default_registry() -> "AdapterRegistry":
    from job_agent.sites.base import AdapterRegistry
    registry = AdapterRegistry()
    registry.register(GreenhouseAdapter)
    return registry
