"""Application Submission Agent — fills and submits job applications via Playwright."""
from datetime import datetime
from pathlib import Path

from loguru import logger
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from job_agent.agents.base_agent import BaseAgent
from job_agent.captcha import CaptchaSolver, CaptchaUnsolvableError
from job_agent.config import Settings
from job_agent.models import Account, ApplicationStatus, JobApplication
from job_agent.persistence.credentials import CredentialStore
from job_agent.sites.base import FormChallenge, SiteAdapter
from job_agent.sites.registry import build_default_registry


class SubmissionResult:
    def __init__(
        self,
        status: ApplicationStatus,
        message: str | None = None,
        screenshot_path: Path | None = None,
    ):
        self.status = status
        self.message = message
        self.screenshot_path = screenshot_path


class ApplicationSubmissionAgent(BaseAgent):
    def __init__(
        self,
        settings: Settings,
        registry=None,
        credential_store: CredentialStore | None = None,
    ):
        super().__init__(settings)
        self.registry = registry or build_default_registry()
        self.credential_store = credential_store or CredentialStore(self.settings.sqlite_db)
        self.solver = CaptchaSolver(self.settings)
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    async def __aenter__(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.settings.browser_headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    def _new_context(self) -> BrowserContext:
        if self._browser is None:
            raise RuntimeError("Browser not started. Use async context manager.")
        return self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
            locale="en-US",
            timezone_id="America/New_York",
        )

    async def _ensure_authenticated(
        self,
        page: Page,
        adapter: SiteAdapter,
        job: JobApplication,
    ) -> Account | None:
        """Log in or sign up if the platform requires an account."""
        if not await adapter.is_login_required(page):
            return None

        platform = adapter.platform_name()
        company = job.company
        account = self.credential_store.get(platform, company)

        if account is not None:
            logger.info(f"Existing account found for {company} on {platform}")
            success = await adapter.authenticate(page, account, create_account=False)
            if not success:
                raise FormChallenge(f"Login failed for {platform}/{company}")
            return account

        if not self.settings.has_login_credentials:
            raise FormChallenge(
                f"Account required for {platform}/{company} but LOGIN_EMAIL and LOGIN_PASSWORD are not set"
            )

        logger.info(f"Creating new account for {company} on {platform}")
        account = Account(
            platform=platform,
            company=company,
            username=self.settings.login_email,
            password=self.settings.login_password,
        )
        success = await adapter.authenticate(page, account, create_account=True)
        if not success:
            raise FormChallenge(f"Account creation failed for {platform}/{company}")

        self.credential_store.save(
            platform=platform,
            company=company,
            username=account.username,
            password=account.password,
        )
        logger.info(f"Saved new credentials for {platform}/{company}")
        return account

    async def _solve_captcha_if_present(self, page: Page, adapter: SiteAdapter) -> None:
        """Solve any CAPTCHA on the page before submission."""
        try:
            await adapter.handle_captcha(page, self.solver)
        except CaptchaUnsolvableError as exc:
            logger.warning(f"CAPTCHA could not be solved automatically: {exc}")
            if self.settings.human_in_the_loop:
                await self._pause_for_human(page, "CAPTCHA needs manual solving")
            else:
                raise FormChallenge(f"CAPTCHA unsolvable and human-in-the-loop disabled: {exc}")

    async def _pause_for_human(self, page: Page, reason: str) -> None:
        """Pause execution and wait for the user to manually solve a challenge."""
        screenshot = await self._save_screenshot(page, "human_in_the_loop")
        logger.warning(f"{reason}. Pausing for manual intervention.")
        # If headless, switching to headed would help, but for now we just wait.
        if self.settings.browser_headless:
            logger.warning(
                "Running in headless mode. Set BROWSER_HEADLESS=false to see the browser for manual intervention."
            )
        input("Press ENTER in the terminal after you have solved the challenge...")

    async def apply(self, job: JobApplication, resume_path: Path | None = None) -> SubmissionResult:
        try:
            adapter = self.registry.get_adapter(job.url)
        except ValueError as exc:
            logger.error(f"No adapter for {job.url}: {exc}")
            return SubmissionResult(
                ApplicationStatus.NEEDS_HUMAN,
                f"No site adapter for URL: {job.url}",
            )

        logger.info(f"Using adapter '{adapter.name()}' for {job.url}")
        context = None
        page = None
        try:
            context = await self._new_context()
            page = await context.new_page()

            await page.goto(job.url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)

            await self._ensure_authenticated(page, adapter, job)
            # Give the page a moment to settle after authentication redirects.
            await page.wait_for_timeout(2000)

            form_summary = await adapter.parse_form(page)
            logger.info(f"Form fields detected: {form_summary}")

            resume = resume_path or job.resume_path
            if resume is None or not resume.exists():
                logger.warning(f"No valid resume for job {job.id}")
                return SubmissionResult(
                    ApplicationStatus.FAILED,
                    "Resume missing or invalid",
                )

            await adapter.fill_application(
                page,
                job,
                str(resume.resolve()),
                self._profile_dict(),
                dry_run=not self.settings.enable_auto_submit,
            )

            if self.settings.enable_auto_submit:
                await self._solve_captcha_if_present(page, adapter)
            else:
                logger.info("Dry-run mode: skipping CAPTCHA solving")

            success = await adapter.submit(page, dry_run=not self.settings.enable_auto_submit)
            if not success:
                return SubmissionResult(
                    ApplicationStatus.FAILED,
                    "Submit step did not complete",
                )

            status = ApplicationStatus.SUBMITTED if self.settings.enable_auto_submit else ApplicationStatus.QUEUED
            message = (
                "Application submitted successfully"
                if self.settings.enable_auto_submit
                else "Dry-run: form filled but not submitted"
            )
            job.date_applied = datetime.now()
            screenshot = None
            if status == ApplicationStatus.QUEUED:
                screenshot = await self._save_screenshot(page, f"dryrun_{job.id}")
            return SubmissionResult(status, message, screenshot)

        except FormChallenge as exc:
            logger.warning(f"Human challenge on {job.url}: {exc.reason}")
            screenshot = await self._save_screenshot(page, f"challenge_{job.id}")
            return SubmissionResult(
                ApplicationStatus.NEEDS_HUMAN,
                f"Challenge detected: {exc.reason}",
                screenshot,
            )
        except CaptchaUnsolvableError as exc:
            logger.warning(f"CAPTCHA could not be solved for {job.url}: {exc}")
            screenshot = await self._save_screenshot(page, f"captcha_failed_{job.id}")
            return SubmissionResult(
                ApplicationStatus.NEEDS_HUMAN,
                f"CAPTCHA unsolvable: {exc}",
                screenshot,
            )
        except Exception as exc:
            logger.exception(f"Submission failed for {job.url}: {exc}")
            screenshot = await self._save_screenshot(page, f"error_{job.id}")
            return SubmissionResult(
                ApplicationStatus.FAILED,
                f"Submission error: {exc}",
                screenshot,
            )
        finally:
            if page:
                await page.close()
            if context:
                await context.close()

    async def apply_with_retry(self, job: JobApplication, resume_path: Path | None = None) -> SubmissionResult:
        last_result = None
        for attempt in range(1, self.settings.max_retries + 1):
            last_result = await self.apply(job, resume_path)
            if last_result.status in {ApplicationStatus.SUBMITTED, ApplicationStatus.QUEUED}:
                return last_result
            if last_result.status == ApplicationStatus.NEEDS_HUMAN:
                return last_result
            logger.warning(f"Retry {attempt}/{self.settings.max_retries} for job {job.id}")
        return last_result or SubmissionResult(ApplicationStatus.FAILED, "Exhausted retries")
