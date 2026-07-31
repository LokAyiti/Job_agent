"""iCIMS careers site adapter.

iCIMS application pages are usually iframe-based or wizard-based. This adapter
covers the common candidate profile flow: sign in/create account, fill name/email/
phone, upload resume, and submit. Complex questionnaires are flagged for human
review.
"""
from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import Frame, Page, TimeoutError as PWTimeoutError

from job_agent.models import Account, JobApplication
from job_agent.sites.base import FormChallenge, SiteAdapter


class iCIMSAdapter(SiteAdapter):
    platform: str = "icims"

    @classmethod
    def can_handle(cls, url: str) -> bool:
        lowered = url.lower()
        return "icims.com" in lowered or "applicantpro.com" in lowered or "jobs.icims.com" in lowered

    def name(self) -> str:
        return "icims"

    def platform_name(self) -> str:
        return self.platform

    async def is_login_required(self, page: Page) -> bool:
        """iCIMS often requires creating an account or signing in before applying."""
        url = page.url.lower()
        if any(path in url for path in ["/login", "/signin", "/account"]):
            return True
        try:
            indicators = [
                'input[type="password"]',
                'button:has-text("Sign In")',
                'button:has-text("Create Account")',
                'a:has-text("Sign In")',
                'a:has-text("Create Account")',
                'label:has-text("Password")',
            ]
            for selector in indicators:
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
        """Log in or create an iCIMS candidate account."""
        logger.info(
            f"{'Creating' if create_account else 'Signing in to'} iCIMS account for {account.company}"
        )
        try:
            if create_account:
                create_link = page.locator('a:has-text("Create Account"), button:has-text("Create Account")')
                if await create_link.count() > 0:
                    await create_link.first.click()
                    await page.wait_for_timeout(1500)

            await self._fill_field(page, 'input[type="email"], input[name*="email" i], input[id*="email" i]', account.username)
            await self._fill_field(page, 'input[type="password"], input[name*="password" i], input[id*="password" i]', account.password)

            if create_account:
                await self._fill_field(
                    page,
                    'input[name*="confirmPassword" i], input[id*="confirmPassword" i], input[placeholder*="Confirm Password" i]',
                    account.password,
                )
                submit = page.locator('button:has-text("Create Account"), input[type="submit"]').first
            else:
                submit = page.locator('button:has-text("Sign In"), input[type="submit"], button[type="submit"]').first

            if await submit.count() > 0:
                await submit.click()
                await page.wait_for_load_state("networkidle", timeout=15000)
            return True
        except Exception as exc:
            logger.warning(f"iCIMS authentication failed: {exc}")
            return False

    async def detect_challenges(self, page: Page, dry_run: bool = False) -> None:
        """Detect iCIMS flows that are too complex to automate safely."""
        text = await page.locator("body").inner_text()
        lowered = text.lower()

        unsupported_phrases = [
            "assessment",
            "pre-employment screening",
            "video interview",
            "complete on a mobile device",
            "chrome extension required",
        ]
        for phrase in unsupported_phrases:
            if phrase in lowered:
                raise FormChallenge(f"Unsupported iCIMS flow detected: {phrase}")

    async def _application_frame(self, page: Page) -> Page | Frame:
        """Return the application iframe if iCIMS uses one, otherwise the page."""
        try:
            iframe = page.frame_locator('iframe[title*="application" i], iframe[id*="icims" i], iframe[src*="icims.com" i]')
            if await iframe.locator("body").count() > 0:
                return iframe
        except Exception:
            pass
        return page

    async def parse_form(self, page: Page) -> dict[str, Any]:
        target = await self._application_frame(page)
        fields = {}
        try:
            fields["first_name"] = await target.locator('input[name*="firstName" i], input[id*="firstName" i], input[aria-label*="First Name" i]').count()
            fields["last_name"] = await target.locator('input[name*="lastName" i], input[id*="lastName" i], input[aria-label*="Last Name" i]').count()
            fields["email"] = await target.locator('input[type="email"], input[name*="email" i]').count()
            fields["phone"] = await target.locator('input[type="tel"], input[name*="phone" i]').count()
            fields["resume"] = await target.locator('input[type="file"][name*="resume" i], input[type="file"][id*="resume" i], input[type="file"]').count()
            fields["submit"] = await target.locator('button[type="submit"], input[type="submit"], button:has-text("Submit")').count()
        except Exception as exc:
            logger.warning(f"iCIMS form parse error: {exc}")
        return fields

    async def fill_application(
        self,
        page: Page,
        job: JobApplication,
        resume_path: str,
        profile: dict[str, str],
        dry_run: bool = False,
    ) -> None:
        """Fill the iCIMS application form."""
        target = await self._application_frame(page)
        full_name = profile.get("my_name", "") or ""
        name_parts = full_name.strip().split()
        first_name = name_parts[0] if name_parts else ""
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

        await self._fill_field(
            target,
            'input[name*="firstName" i], input[id*="firstName" i], input[aria-label*="First Name" i]',
            first_name,
        )
        await self._fill_field(
            target,
            'input[name*="lastName" i], input[id*="lastName" i], input[aria-label*="Last Name" i]',
            last_name,
        )
        await self._fill_field(
            target,
            'input[type="email"], input[name*="email" i]',
            profile.get("my_email", "") or "",
        )
        await self._fill_field(
            target,
            'input[type="tel"], input[name*="phone" i], input[aria-label*="Phone" i]',
            profile.get("my_phone", "") or "",
        )
        await self._fill_field(
            target,
            'input[name*="linkedin" i], input[id*="linkedin" i], input[aria-label*="LinkedIn" i]',
            profile.get("my_linkedin", "") or "",
        )

        resume_file = Path(resume_path)
        if resume_file.exists():
            upload_selectors = [
                'input[type="file"][name*="resume" i]',
                'input[type="file"][id*="resume" i]',
                'input[type="file"][accept*=".pdf" i]',
                'input[type="file"]',
            ]
            for selector in upload_selectors:
                if await target.locator(selector).count() > 0:
                    try:
                        await target.locator(selector).set_input_files(str(resume_file.resolve()))
                        logger.info(f"Uploaded resume {resume_file.name}")
                        await page.wait_for_timeout(1500)
                        break
                    except Exception as exc:
                        logger.warning(f"Resume upload failed for selector {selector}: {exc}")

    async def submit(self, page: Page, dry_run: bool) -> bool:
        target = await self._application_frame(page)
        submit_button = target.locator(
            'button[type="submit"], input[type="submit"], button:has-text("Submit"), button:has-text("Next")'
        ).first
        if await submit_button.count() == 0:
            logger.warning("No submit button found on iCIMS form")
            return False

        if dry_run:
            logger.info("Dry-run mode: stopping before final submit")
            return True

        try:
            await submit_button.click()
            await page.wait_for_load_state("networkidle", timeout=15000)
            return True
        except PWTimeoutError:
            logger.warning("Submit click timed out; job may still be processing")
            return True

    async def _fill_field(self, target: Page | Frame, selector: str, value: str) -> None:
        if not value:
            return
        try:
            locator = target.locator(selector).first
            if await locator.count() == 0:
                return
            await locator.fill(value)
        except Exception as exc:
            logger.debug(f"Could not fill iCIMS field with selector {selector}: {exc}")
