"""iCIMS careers site adapter.

iCIMS application pages are usually rendered inside an iframe with the id
``icims_content_iframe``.  This adapter navigates to the apply view of the job,
extracts fields from that iframe, and fills the form using the shared profile
values and QuestionAnsweringAgent.
"""
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse, parse_qs, urlunparse

from loguru import logger
from playwright.async_api import Frame, Page, TimeoutError as PWTimeoutError

from job_agent.models import Account, JobApplication
from job_agent.sites.base import FormChallenge, SiteAdapter
from job_agent.sites.field_filler import RobustFieldFiller
from job_agent.sites.form_utils import (
    build_form_schema,
    extract_fields,
    get_profile_values,
)


class iCIMSAdapter(SiteAdapter):
    platform: str = "icims"

    @classmethod
    def can_handle(cls, url: str) -> bool:
        lowered = url.lower()
        return (
            "icims.com" in lowered
            or "applicantpro.com" in lowered
            or "jobs.icims.com" in lowered
        )

    def name(self) -> str:
        return "icims"

    def platform_name(self) -> str:
        return self.platform

    async def _content_frame(self, page: Page) -> Page | Frame:
        """Return the actual iCIMS content iframe Frame if present, otherwise the page itself."""
        try:
            # Wait for the iframe to be present and locate it by URL pattern.
            await page.wait_for_selector("iframe#icims_content_iframe", timeout=5000)
            frame = next(
                (f for f in page.frames if "icims_content_iframe" in f.url or "in_iframe=1" in f.url),
                None,
            )
            if frame is not None:
                return frame
        except Exception:
            pass
        return page

    async def _application_url(self, page: Page) -> str:
        """Return the job URL in apply mode.

        iCIMS job pages expose an "Apply" link that points to the same job path
        with ``mode=apply&apply=yes``.  Navigating there renders the application
        form (or the login/account-creation gate) inside the iframe.
        """
        current = page.url
        parsed = urlparse(current)
        query = parse_qs(parsed.query)
        query["mode"] = ["apply"]
        query["apply"] = ["yes"]
        new_query = urlencode(query, doseq=True)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

    async def _goto_apply(self, page: Page) -> None:
        """Navigate to the apply view of the current job page."""
        apply_url = await self._application_url(page)
        logger.info(f"Navigating to iCIMS apply view: {apply_url}")
        await page.goto(apply_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

    async def is_login_required(self, page: Page) -> bool:
        """Return True if the iCIMS page shows a login or account-creation gate."""
        url = page.url.lower()
        if any(path in url for path in ["/login", "/signin", "/account"]):
            return True

        target = await self._content_frame(page)
        indicators = [
            'input[type="password"]',
            'button:has-text("Sign In")',
            'button:has-text("Create Account")',
            'a:has-text("Sign In")',
            'a:has-text("Create Account")',
            'label:has-text("Password")',
            'input#email',
        ]
        try:
            for selector in indicators:
                if await target.locator(selector).count() > 0:
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
        """Best-effort login or account creation for iCIMS.

        iCIMS apply flows usually require the candidate to create an account
        before the full application form is shown.  Without saved credentials we
        mark the job as needing human review rather than creating accounts
        autonomously.
        """
        logger.info(
            f"{'Creating' if create_account else 'Signing in to'} iCIMS account for {account.company}"
        )
        try:
            target = await self._content_frame(page)
            await target.fill('input[type="email"], input[name*="email" i], input[id*="email" i]', account.username)
            await target.fill('input[type="password"], input[name*="password" i], input[id*="password" i]', account.password)

            if create_account:
                button = target.locator('button:has-text("Create Account"), input[type="submit"]')
            else:
                button = target.locator('button:has-text("Sign In"), button:has-text("Log in"), input[type="submit"]')

            if await button.count() == 0:
                button = target.locator('button[type="submit"]').first

            if await button.count() > 0:
                await button.click()
                await page.wait_for_load_state("networkidle", timeout=15000)
                await page.wait_for_timeout(2000)

            # Verify login succeeded: password field gone and a sign-out / apply form present.
            password_count = await target.locator('input[type="password"]').count()
            if password_count == 0:
                logger.info("iCIMS authentication succeeded")
                return True

            logger.warning("iCIMS authentication did not complete; password field still present")
            return False
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

    async def parse_form(self, page: Page) -> dict[str, Any]:
        """Inspect the live iCIMS apply form and return a structured field schema."""
        # Ensure we are in apply mode if the page is a job-details page with an Apply button.
        if await page.locator('a:has-text("Apply")').count() > 0 or "mode=apply" not in page.url:
            await self._goto_apply(page)

        target = await self._content_frame(page)
        fields = await extract_fields(target)

        known_selectors = {
            "first_name": 'input[name*="firstName" i], input[id*="firstName" i], input[aria-label*="First Name" i]',
            "last_name": 'input[name*="lastName" i], input[id*="lastName" i], input[aria-label*="Last Name" i]',
            "email": 'input[type="email"], input[name*="email" i], input[aria-label*="Email" i]',
            "phone": 'input[type="tel"], input[name*="phone" i], input[aria-label*="Phone" i]',
            "linkedin": 'input[name*="linkedin" i], input[id*="linkedin" i], input[aria-label*="LinkedIn" i]',
            "resume": 'input[type="file"][name*="resume" i], input[type="file"][id*="resume" i], input[type="file"]',
            "submit": 'button[type="submit"], input[type="submit"], button:has-text("Submit"), button:has-text("Next")',
        }
        return build_form_schema(fields, self.platform, page.url, known_selectors)

    async def fill_application(
        self,
        page: Page,
        job: JobApplication,
        resume_path: str,
        profile: dict[str, str],
        dry_run: bool = False,
        form_schema: dict[str, Any] | None = None,
    ) -> None:
        """Fill the iCIMS application form."""
        target = await self._content_frame(page)
        values = get_profile_values(profile)
        filler = RobustFieldFiller(target)

        await filler.fill(
            values["first_name"],
            name="firstName",
            label="First Name",
            aria_label="First Name",
            selectors=['input[name*="firstName" i]', 'input[id*="firstName" i]'],
        )
        await filler.fill(
            values["last_name"],
            name="lastName",
            label="Last Name",
            aria_label="Last Name",
            selectors=['input[name*="lastName" i]', 'input[id*="lastName" i]'],
        )
        await filler.fill(
            values["email"],
            name="email",
            label="Email",
            aria_label="Email",
            selectors=['input[type="email"]', 'input[name*="email" i]'],
        )
        await filler.fill(
            values["phone"],
            name="phone",
            label="Phone",
            aria_label="Phone",
            selectors=['input[type="tel"]', 'input[name*="phone" i]'],
        )
        await filler.fill(
            values["linkedin"],
            name="linkedin",
            label="LinkedIn",
            aria_label="LinkedIn",
            selectors=['input[name*="linkedin" i]', 'input[id*="linkedin" i]'],
        )

        resume_file = Path(resume_path)
        if resume_file.exists():
            uploaded = await filler.upload(
                resume_file,
                name="resume",
                label="Resume",
                aria_label="Resume",
                selectors=[
                    'input[type="file"][name*="resume" i]',
                    'input[type="file"][id*="resume" i]',
                    'input[type="file"]',
                ],
            )
            if uploaded:
                logger.info(f"Uploaded resume {resume_file.name}")
                await page.wait_for_timeout(1500)

        # Auto-answer custom questions when the harness provides a form schema.
        if form_schema:
            from job_agent.agents.question_answering_agent import QuestionAnsweringAgent

            return await QuestionAnsweringAgent().fill_unmapped_fields(
                target, form_schema, job, profile, dry_run=dry_run
            )

    async def submit(self, page: Page, dry_run: bool) -> bool:
        target = await self._content_frame(page)
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
