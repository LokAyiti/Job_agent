"""Greenhouse careers site adapter.

Best-effort form filling for boards.greenhouse.io and similar Greenhouse URLs.
Unknown custom questions are skipped with a note so the user can review.
"""
import re
from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import Locator, Page, TimeoutError as PWTimeoutError

from job_agent.models import Account, JobApplication
from job_agent.sites.base import FormChallenge, SiteAdapter


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
        fields = {}
        try:
            fields["first_name"] = await page.locator('#first_name').count()
            fields["last_name"] = await page.locator('#last_name').count()
            fields["email"] = await page.locator('#email').count()
            fields["phone"] = await page.locator('#phone').count()
            fields["resume"] = await page.locator('input#resume[type="file"]').count()
            fields["submit"] = await page.locator('button[type="submit"], input[type="submit"]').count()
        except Exception as exc:
            logger.warning(f"Greenhouse form parse error: {exc}")
        return fields

    async def fill_application(
        self,
        page: Page,
        job: JobApplication,
        resume_path: str,
        profile: dict[str, str],
        dry_run: bool = False,
    ) -> None:
        full_name = profile.get("my_name", "") or ""
        name_parts = full_name.strip().split()
        first_name = name_parts[0] if name_parts else ""
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

        await self._fill_text(page, '#first_name', first_name)
        await self._fill_text(page, '#last_name', last_name)
        await self._fill_text(page, '#email', profile.get("my_email", "") or "")
        await self._fill_text(page, '#phone', profile.get("my_phone", "") or "")
        await self._fill_by_label(page, "linkedin", profile.get("my_linkedin", "") or "")

        # Resume upload.
        resume_file = Path(resume_path)
        if resume_file.exists() and await page.locator('input#resume[type="file"]').count() > 0:
            upload_input = page.locator('input#resume[type="file"]')
            try:
                # Greenhouse hides the file input; Playwright can still set it directly.
                await upload_input.set_input_files(str(resume_file.resolve()))
                logger.info(f"Uploaded resume {resume_file.name}")
                await page.wait_for_timeout(1500)
            except Exception as exc:
                logger.warning(f"Resume upload failed: {exc}")
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

    async def _fill_text(self, page: Page, selector: str, value: str) -> None:
        if not value:
            return
        locator = page.locator(selector)
        if await locator.count() == 0:
            return
        await locator.fill(value)

    async def _fill_by_label(self, page: Page, keyword: str, value: str) -> None:
        if not value:
            return
        try:
            by_label = page.get_by_label(keyword, exact=False)
            if await by_label.count() > 0:
                await by_label.first.fill(value)
                return
        except Exception:
            pass

        # Fallback: find label containing keyword, then use its for attribute.
        try:
            label = page.locator(f'label:has-text("{keyword}")').first
            if await label.count() == 0:
                return
            target_id = await label.get_attribute("for")
            if target_id:
                target = page.locator(f"#{target_id}")
                if await target.count() > 0:
                    await target.fill(value)
        except Exception as exc:
            logger.debug(f"Could not fill {keyword} field: {exc}")


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
