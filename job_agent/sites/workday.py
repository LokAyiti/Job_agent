"""Workday careers site adapter.

Workday portals are heavily JavaScript-driven and usually require a candidate
account before the application form is rendered. This adapter detects login
state, attempts authentication with saved credentials, and fills the public
application fields that Workday exposes. When the form cannot be reached
(bad credentials, CAPTCHA, unsupported flow), it raises :class:`FormChallenge`
so a human can take over.
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
        """Return True if the current page requires a Workday account."""
        url = page.url.lower()
        if any(path in url for path in ["/login", "/signin", "/authenticate"]):
            return True

        sign_in_indicators = [
            'button[data-automation-id="utilityButtonSignIn"]',
            'button:has-text("Sign In")',
            'button:has-text("Create Account")',
            'input[type="password"]',
            'label:has-text("Password")',
            'h1:has-text("Sign In")',
            'h2:has-text("Sign In")',
        ]
        try:
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
        """Log in or create a Workday candidate account.

        Returns True only if the login form disappears after submission,
        indicating a successful authentication.
        """
        logger.info(
            f"{'Creating' if create_account else 'Signing in to'} Workday account for {account.company}"
        )
        try:
            # If the sign-in button is present, open the login modal first.
            sign_in_button = page.locator(
                'button[data-automation-id="utilityButtonSignIn"], button:has-text("Sign In")'
            ).first
            if await sign_in_button.count() > 0 and await sign_in_button.is_visible():
                await sign_in_button.click()
                await page.wait_for_timeout(1500)

            if create_account:
                create_tab = page.locator('button:has-text("Create Account"), a:has-text("Create Account")')
                if await create_tab.count() > 0:
                    await create_tab.first.click()
                    await page.wait_for_timeout(1500)

                await self._fill_field(page, 'input[type="email"]', account.username)
                await self._fill_field(
                    page,
                    'input[type="password"], input[aria-label*="Password" i]',
                    account.password,
                )
                await self._fill_field(
                    page,
                    'input[aria-label*="Confirm Password" i], input[placeholder*="Confirm Password" i]',
                    account.password,
                )
                submit = page.locator('button:has-text("Create Account"), button[type="submit"]')
            else:
                await self._fill_field(
                    page,
                    'input[type="email"], input[data-automation-id="email"]',
                    account.username,
                )
                await self._fill_field(
                    page,
                    'input[type="password"], input[data-automation-id="password"]',
                    account.password,
                )
                submit = page.locator(
                    'button:has-text("Sign In"), button[data-automation-id="signInSubmitButton"], button[type="submit"]'
                )

            if await submit.count() == 0:
                submit = page.locator('button[type="submit"]').first

            if await submit.count() > 0:
                await submit.first.click()
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except PWTimeoutError:
                    pass
                await page.wait_for_timeout(2000)

            # Verify login succeeded: the password field and sign-in button should be gone.
            password_count = await page.locator('input[type="password"]').count()
            signin_count = await page.locator(
                'button[data-automation-id="utilityButtonSignIn"]'
            ).count()
            if password_count == 0 and signin_count == 0:
                logger.info("Workday authentication succeeded")
                return True

            logger.warning("Workday authentication did not complete; login elements still present")
            return False
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

    async def _click_apply_if_needed(self, page: Page) -> bool:
        """Click an Apply button on the job details page and wait for the form.

        Returns True if an Apply button was found and clicked.
        """
        apply_selectors = [
            'button[data-automation-id="applyManually"]',
            'button[data-automation-id="applyWithResume"]',
            'a[data-automation-id="applyManually"]',
            'a[data-automation-id="applyWithResume"]',
            'button:has-text("Apply")',
            'a:has-text("Apply")',
        ]

        # Workday renders the apply button via JS; wait for it to appear.
        for selector in apply_selectors:
            try:
                locator = page.locator(selector).first
                await locator.wait_for(state="visible", timeout=10000)
                if await locator.count() > 0 and await locator.is_visible():
                    await locator.click()
                    await page.wait_for_timeout(3000)
                    return True
            except Exception:
                continue
        return False

    async def _wait_for_form_or_login(self, page: Page) -> bool:
        """Wait for either application form fields or a login gate to appear.

        Returns True if form fields are present, False if a login gate appeared.
        """
        try:
            await page.wait_for_selector(
                'input[data-automation-id="firstName"], input[data-automation-id="email"], '
                'input[type="email"], input[type="password"], '
                'button[data-automation-id="utilityButtonSignIn"], button:has-text("Sign In")',
                timeout=15000,
            )
        except PWTimeoutError:
            logger.warning("Neither application form nor login gate appeared within timeout")
            return False

        # If a login gate is present, report it.
        if await page.locator('input[type="password"]').count() > 0 or await page.locator(
            'button[data-automation-id="utilityButtonSignIn"], button:has-text("Sign In")'
        ).count() > 0:
            logger.warning("Workday login gate detected after clicking Apply")
            return False

        return True

    async def parse_form(self, page: Page) -> dict[str, Any]:
        """Inspect the live DOM and return a structured field schema."""
        await self._click_apply_if_needed(page)
        form_visible = await self._wait_for_form_or_login(page)
        if not form_visible:
            raise FormChallenge("Workday application form not reachable; likely requires login or unsupported extension flow")

        fields = await extract_fields(page)
        known_selectors = {
            "first_name": 'input[data-automation-id="firstName"], input[name*="firstName" i], input[aria-label*="First Name" i]',
            "last_name": 'input[data-automation-id="lastName"], input[name*="lastName" i], input[aria-label*="Last Name" i]',
            "email": 'input[data-automation-id="email"], input[type="email"], input[aria-label*="Email" i]',
            "phone": 'input[data-automation-id="phone"], input[type="tel"], input[aria-label*="Phone" i]',
            "linkedin": 'input[data-automation-id="linkedin"], input[aria-label*="LinkedIn" i]',
            "resume": 'input[data-automation-id="resume"][type="file"], input[type="file"][aria-label*="Resume" i]',
            "submit": 'button[data-automation-id="submit"], button:has-text("Submit")',
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
        """Fill the Workday application form."""
        values = get_profile_values(profile)
        filler = RobustFieldFiller(page)

        # Click Apply if we are still on the job details page, then wait for the form.
        await self._click_apply_if_needed(page)
        form_visible = await self._wait_for_form_or_login(page)
        if not form_visible:
            raise FormChallenge("Workday application form not reachable; likely requires login or unsupported extension flow")

        await filler.fill(
            values["first_name"],
            field_id="firstName",
            name="firstName",
            label="First Name",
            aria_label="First Name",
            selectors=['input[data-automation-id="firstName"]'],
        )
        await filler.fill(
            values["last_name"],
            field_id="lastName",
            name="lastName",
            label="Last Name",
            aria_label="Last Name",
            selectors=['input[data-automation-id="lastName"]'],
        )
        await filler.fill(
            values["email"],
            field_id="email",
            name="email",
            label="Email",
            aria_label="Email",
            selectors=['input[data-automation-id="email"]', 'input[type="email"]'],
        )
        await filler.fill(
            values["phone"],
            field_id="phone",
            name="phone",
            label="Phone",
            aria_label="Phone",
            selectors=['input[data-automation-id="phone"]', 'input[type="tel"]'],
        )
        await filler.fill(
            values["linkedin"],
            field_id="linkedin",
            name="linkedin",
            label="LinkedIn",
            aria_label="LinkedIn",
            selectors=['input[data-automation-id="linkedin"]'],
        )

        resume_file = Path(resume_path)
        if resume_file.exists():
            await filler.upload(
                resume_file,
                field_id="resume",
                name="resume",
                label="Resume",
                aria_label="Resume",
                selectors=[
                    'input[data-automation-id="resume"][type="file"]',
                    'input[type="file"][aria-label*="Resume" i]',
                ],
            )

        # Auto-answer custom questions when the harness provides a form schema.
        if form_schema:
            from job_agent.agents.question_answering_agent import QuestionAnsweringAgent

            return await QuestionAnsweringAgent().fill_unmapped_fields(
                page, form_schema, job, profile, dry_run=dry_run
            )

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
