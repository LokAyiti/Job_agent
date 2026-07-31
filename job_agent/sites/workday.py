"""Workday careers site adapter.

Workday portals are heavily JavaScript-driven and multi-step. This adapter covers
the common public application entry point: click "Apply", sign in/create account,
fill basic profile fields, and submit. Complex multi-step wizards are flagged for
human review.
"""
from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import Page, TimeoutError as PWTimeoutError

from job_agent.models import Account, JobApplication
from job_agent.sites.base import FormChallenge, SiteAdapter


class WorkdayAdapter(SiteAdapter):
    platform: str = "workday"

    @classmethod
    def can_handle(cls, url: str) -> bool:
        lowered = url.lower()
        return "myworkdayjobs.com" in lowered or "workday.com" in lowered

    def name(self) -> str:
        return "workday"

    def platform_name(self) -> str:
        return self.platform

    async def is_login_required(self, page: Page) -> bool:
        """Workday almost always requires an account before applying."""
        url = page.url.lower()
        if any(path in url for path in ["/login", "/signin", "/authenticate"]):
            return True
        try:
            sign_in_indicators = [
                'input[type="password"]',
                'button:has-text("Sign In")',
                'button:has-text("Create Account")',
                'h1:has-text("Sign In")',
                'h2:has-text("Sign In")',
                'label:has-text("Password")',
            ]
            for selector in sign_in_indicators:
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
        """Log in or create a Workday candidate account."""
        logger.info(
            f"{'Creating' if create_account else 'Signing in to'} Workday account for {account.company}"
        )
        try:
            if create_account:
                create_tab = page.locator('button:has-text("Create Account"), a:has-text("Create Account")')
                if await create_tab.count() > 0:
                    await create_tab.first.click()
                    await page.wait_for_timeout(1500)

                await self._fill_field(page, 'input[type="email"]', account.username)
                await self._fill_field(page, 'input[type="password"], input[aria-label*="Password" i]', account.password)
                await self._fill_field(
                    page,
                    'input[aria-label*="Confirm Password" i], input[placeholder*="Confirm Password" i]',
                    account.password,
                )
                submit = page.locator('button:has-text("Create Account"), button[type="submit"]')
            else:
                await self._fill_field(page, 'input[type="email"], input[data-automation-id="email"]', account.username)
                await self._fill_field(page, 'input[type="password"], input[data-automation-id="password"]', account.password)
                submit = page.locator(
                    'button:has-text("Sign In"), button[data-automation-id="signInSubmitButton"], button[type="submit"]'
                )

            if await submit.count() == 0:
                submit = page.locator('button[type="submit"]').first

            if await submit.count() > 0:
                await submit.first.click()
                await page.wait_for_load_state("networkidle", timeout=15000)
            return True
        except Exception as exc:
            logger.warning(f"Workday authentication failed: {exc}")
            return False

    async def detect_challenges(self, page: Page, dry_run: bool = False) -> None:
        """Detect unsupported Workday flows that need human review."""
        text = await page.locator("body").inner_text()
        lowered = text.lower()

        unsupported_phrases = [
            "use chrome extension",
            "multi-step application",
            "assessment",
            "pre-employment",
            "video interview",
        ]
        for phrase in unsupported_phrases:
            if phrase in lowered:
                raise FormChallenge(f"Unsupported Workday flow detected: {phrase}")

    async def parse_form(self, page: Page) -> dict[str, Any]:
        fields = {}
        try:
            fields["first_name"] = await page.locator('input[data-automation-id="firstName"], input[name*="firstName" i]').count()
            fields["last_name"] = await page.locator('input[data-automation-id="lastName"], input[name*="lastName" i]').count()
            fields["email"] = await page.locator('input[data-automation-id="email"], input[type="email"]').count()
            fields["phone"] = await page.locator('input[data-automation-id="phone"], input[type="tel"]').count()
            fields["resume"] = await page.locator('input[data-automation-id="resume"], input[type="file"]').count()
            fields["submit"] = await page.locator('button[data-automation-id="submit"], button:has-text("Submit")').count()
        except Exception as exc:
            logger.warning(f"Workday form parse error: {exc}")
        return fields

    async def fill_application(
        self,
        page: Page,
        job: JobApplication,
        resume_path: str,
        profile: dict[str, str],
        dry_run: bool = False,
    ) -> None:
        """Fill the Workday application form."""
        full_name = profile.get("my_name", "") or ""
        name_parts = full_name.strip().split()
        first_name = name_parts[0] if name_parts else ""
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

        # Click Apply if we are still on the job details page.
        apply_button = page.locator('button[data-automation-id="applyManually"], button:has-text("Apply")')
        if await apply_button.count() > 0:
            await apply_button.first.click()
            await page.wait_for_timeout(2000)

        await self._fill_field(
            page,
            'input[data-automation-id="firstName"], input[name*="firstName" i], input[aria-label*="First Name" i]',
            first_name,
        )
        await self._fill_field(
            page,
            'input[data-automation-id="lastName"], input[name*="lastName" i], input[aria-label*="Last Name" i]',
            last_name,
        )
        await self._fill_field(
            page,
            'input[data-automation-id="email"], input[type="email"], input[aria-label*="Email" i]',
            profile.get("my_email", "") or "",
        )
        await self._fill_field(
            page,
            'input[data-automation-id="phone"], input[type="tel"], input[aria-label*="Phone" i]',
            profile.get("my_phone", "") or "",
        )
        await self._fill_field(
            page,
            'input[data-automation-id="linkedin"], input[aria-label*="LinkedIn" i]',
            profile.get("my_linkedin", "") or "",
        )

        resume_file = Path(resume_path)
        if resume_file.exists():
            upload_selectors = [
                'input[data-automation-id="resume"][type="file"]',
                'input[type="file"][aria-label*="Resume" i]',
                'input[type="file"]',
            ]
            for selector in upload_selectors:
                if await page.locator(selector).count() > 0:
                    try:
                        await page.locator(selector).set_input_files(str(resume_file.resolve()))
                        logger.info(f"Uploaded resume {resume_file.name}")
                        await page.wait_for_timeout(1500)
                        break
                    except Exception as exc:
                        logger.warning(f"Resume upload failed for selector {selector}: {exc}")

    async def submit(self, page: Page, dry_run: bool) -> bool:
        submit_button = page.locator(
            'button[data-automation-id="submit"], button:has-text("Submit"), button:has-text("Next")'
        )
        if await submit_button.count() == 0:
            submit_button = page.locator('button[type="submit"]').first

        if await submit_button.count() == 0:
            logger.warning("No submit button found on Workday form")
            return False

        if dry_run:
            logger.info("Dry-run mode: stopping before final submit")
            return True

        try:
            await submit_button.first.click()
            await page.wait_for_load_state("networkidle", timeout=15000)
            return True
        except PWTimeoutError:
            logger.warning("Submit click timed out; job may still be processing")
            return True

    async def _fill_field(self, page: Page, selector: str, value: str) -> None:
        if not value:
            return
        try:
            locator = page.locator(selector).first
            if await locator.count() == 0:
                return
            await locator.fill(value)
        except Exception as exc:
            logger.debug(f"Could not fill field with selector {selector}: {exc}")
