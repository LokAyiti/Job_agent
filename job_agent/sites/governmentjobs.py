"""GovernmentJobs.com / NEOGOV adapter.

NEOGOV powers governmentjobs.com and many public-sector career portals. The
application flow is a multi-step wizard (Info, Work, Education, Additional,
Attachments, Questions, Review, Certify) that is usually pre-populated from the
candidate's account profile. This adapter logs in and reaches the first step of
the wizard. Because the supplemental questions and required attachments vary by
agency/position, the adapter stops at the form in dry-run mode and flags the job
as `needs_human` for real submissions unless a future implementation completes
the wizard.
"""
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from loguru import logger
from playwright.async_api import Page, TimeoutError as PWTimeoutError

from job_agent.captcha import CaptchaSolver, CaptchaUnsolvableError
from job_agent.models import Account, JobApplication
from job_agent.sites.base import FormChallenge, SiteAdapter


class GovernmentJobsAdapter(SiteAdapter):
    platform = "governmentjobs"

    _BASE_URL = "https://www.governmentjobs.com"

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return "governmentjobs.com" in url.lower()

    def name(self) -> str:
        return "governmentjobs"

    def platform_name(self) -> str:
        return self.platform

    async def is_login_required(self, page: Page) -> bool:
        """Return True only when a visible login/password form is present."""
        try:
            password_field = await page.wait_for_selector(
                "input#sign-in-password-field:visible",
                timeout=3000,
            )
            return password_field is not None
        except PWTimeoutError:
            return False

    async def authenticate(
        self,
        page: Page,
        account: Account,
        create_account: bool = False,
    ) -> bool:
        """Log into governmentjobs.com."""
        if create_account:
            logger.warning(
                "Account creation flag ignored for governmentjobs.com; using existing credentials"
            )

        logger.info(f"Signing into governmentjobs.com as {account.username}")
        await page.goto(
            f"{self._BASE_URL}/Applications/Submitted",
            wait_until="domcontentloaded",
        )
        await page.wait_for_timeout(3000)

        await self._accept_cookies(page)

        if not await self.is_login_required(page):
            logger.info("Already logged in to governmentjobs.com")
            return True

        await page.fill(
            "input#username-or-email-field:visible",
            account.username,
        )
        await page.fill(
            "input#sign-in-password-field:visible",
            account.password,
        )
        await page.locator('button:has-text("Sign In"):visible').first.click()
        await page.wait_for_timeout(5000)

        if await self._is_logged_in(page):
            logger.info("Login successful")
            return True

        # If still not logged in, attempt to solve a visible CAPTCHA.
        logger.warning("Login failed; attempting CAPTCHA solve")
        try:
            solver = CaptchaSolver(self.settings)
            await solver.solve_on_page(page)
            await page.wait_for_timeout(3000)
            if await self._is_logged_in(page):
                logger.info("Login successful after CAPTCHA")
                return True
        except CaptchaUnsolvableError as exc:
            logger.warning(f"CAPTCHA could not be solved: {exc}")

        return False

    async def parse_form(self, page: Page) -> dict[str, Any]:
        """Return a summary of the application wizard sections on the page."""
        sections = [
            "Info",
            "Work",
            "Education",
            "Additional",
            "Attachments",
            "Questions",
            "Review",
            "Certify",
        ]
        summary: dict[str, Any] = {}
        for section in sections:
            count = await page.locator(
                f"text='{section}' >> xpath=.."
            ).count()
            if count:
                summary[section.lower()] = count
        summary["visible_inputs"] = await page.locator("input:visible").count()
        summary["url"] = page.url
        return summary

    async def fill_application(
        self,
        page: Page,
        job: JobApplication,
        resume_path: str,
        profile: dict[str, str],
        dry_run: bool = False,
    ) -> None:
        """Reach the application wizard. In dry-run mode we do not fill anything."""
        apply_url = self._apply_url(job.url)
        logger.info(f"Navigating to application form: {apply_url}")
        await page.goto(apply_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)
        await self._accept_cookies(page)

        # If the site redirected us to the login page, login failed earlier.
        if not await self._is_logged_in(page):
            raise FormChallenge("Application page requires login; authentication failed earlier")

        summary = await self.parse_form(page)
        logger.info(f"Application wizard sections detected: {summary}")

        if not dry_run:
            # Full wizard automation is not yet implemented. The wizard is
            # pre-populated from the candidate's profile, but supplemental
            # questions and attachments vary by agency.
            raise FormChallenge(
                "GovernmentJobs application wizard requires per-job review and manual completion"
            )

    async def submit(self, page: Page, dry_run: bool) -> bool:
        """Dry-run: do not click the final submit. Real mode: not yet supported."""
        if dry_run:
            logger.info("Dry-run: stopping before final submission")
            return True
        raise FormChallenge(
            "Real submission for governmentjobs.com is not yet automated; needs human review"
        )

    async def detect_challenges(self, page: Page, dry_run: bool = False) -> None:
        """Detect unsupported flows."""
        if "governmentjobs.com" not in page.url.lower():
            raise FormChallenge("Page is not on governmentjobs.com")

    async def handle_captcha(self, page: Page, solver: CaptchaSolver) -> bool:
        """Delegate to the shared CAPTCHA solver."""
        return await solver.solve_on_page(page)

    def _apply_url(self, job_url: str) -> str:
        """Return the NEOGOV apply URL for a job posting."""
        base = job_url.rstrip("/")
        if base.endswith("/apply"):
            return base + "/general"
        if base.endswith("/apply/general"):
            return base
        return f"{base}/apply/general"

    async def _accept_cookies(self, page: Page) -> None:
        try:
            await page.locator("button.osano-cm-accept").first.click(timeout=3000)
            await page.wait_for_timeout(500)
        except Exception:
            pass

    async def _is_logged_in(self, page: Page) -> bool:
        """Detect an authenticated session on a governmentjobs page."""
        if await page.locator("a.sign-out").count() > 0:
            return True
        if await page.locator("text='Applying as:'").count() > 0:
            return True
        if await page.locator("text='My Account'").count() > 0:
            return True
        return False
