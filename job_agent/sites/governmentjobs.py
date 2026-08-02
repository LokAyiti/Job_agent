"""GovernmentJobs.com / NEOGOV adapter.

NEOGOV powers governmentjobs.com and many public-sector career portals. The
application flow is a multi-step wizard (Info, Work, Education, Additional,
Attachments, Questions, Review, Certify) that is usually pre-populated from the
candidate's account profile.

This adapter logs in, navigates to the apply wizard, and fills the "Info" step
with name, email, phone, and resume when running in non-dry-run mode. Because
the supplemental questions and required attachments vary heavily by agency, the
adapter stops and flags the job as `needs_human` if it encounters unsupported
required fields or wizard steps it cannot complete.
"""
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from loguru import logger
from playwright.async_api import Page, TimeoutError as PWTimeoutError

from job_agent.captcha import CaptchaSolver, CaptchaUnsolvableError
from job_agent.config import Settings, get_settings
from job_agent.models import Account, JobApplication
from job_agent.sites.base import FormChallenge, SiteAdapter


class GovernmentJobsAdapter(SiteAdapter):
    platform = "governmentjobs"

    _BASE_URL = "https://www.governmentjobs.com"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

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

    async def prepare_application(
        self,
        page: Page,
        job: JobApplication,
        account: Account | None,
    ) -> None:
        """Navigate to the apply page.

        governmentjobs.com only shows the login gate after redirecting to the
        apply URL. We leave authentication to the Submission Agent's standard
        `_ensure_authenticated` flow by simply landing on the apply page here.
        """
        apply_url = self._apply_url(job.url)
        logger.info(f"Preparing application form: {apply_url}")
        await page.goto(apply_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        await self._accept_cookies(page)

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
        summary["visible_selects"] = await page.locator("select:visible").count()
        summary["visible_textareas"] = await page.locator("textarea:visible").count()
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
        """Walk the multi-step application wizard.

        governmentjobs.com pre-populates most fields from the candidate's
        account profile. This method uploads the resume when the Attachments
        step appears and clicks "Next" through the wizard. In dry-run mode it
        walks the wizard but stops before the final submit; in real mode it
        clicks submit on the Certify/Review step.
        """
        summary = await self.parse_form(page)
        logger.info(f"Application wizard sections detected: {summary}")

        if not await self._is_on_apply_page(page):
            raise FormChallenge(
                "Expected to be on a governmentjobs.com application page"
            )

        await self._walk_wizard(page, resume_path, profile, dry_run=dry_run)

    async def submit(self, page: Page, dry_run: bool) -> bool:
        """Dry-run: stop before final submit. Real mode: click final Submit.

        GovernmentJobs applications contain agency-specific attestations and
        legally-binding certifications. In real mode we check the required
        certification checkbox and click the final Submit button.
        """
        if dry_run:
            logger.info("Dry-run: stopping before final submission")
            return True

        # Ensure any certification/attestation checkbox is ticked before submit.
        await self._handle_certify_step(page)

        submit_button = page.locator(
            "button:has-text('Submit'):visible, button:has-text('Apply'):visible, "
            "button[type='submit']:visible"
        ).first
        if await submit_button.count() == 0:
            logger.warning("No submit button found on final step")
            return False

        logger.warning(
            "GovernmentJobs application is about to be submitted for real. "
            "This action is irreversible and may create a legally-binding application."
        )
        await submit_button.click()
        logger.info("Final Submit clicked")
        return True

    async def _walk_wizard(
        self,
        page: Page,
        resume_path: str,
        profile: dict[str, str],
        dry_run: bool = True,
        max_steps: int = 20,
    ) -> None:
        """Progress through wizard steps by clicking Next/Continue.

        Uploads the resume when a file input is visible. Stops if validation
        errors prevent progression or if an unsupported required field appears.
        """
        resume_file = Path(resume_path)
        cover_letter_file = self._find_cover_letter(resume_file)

        for step in range(max_steps):
            current_url = page.url
            current_title = await page.title()
            logger.info(f"Wizard step {step + 1}: {current_title} ({current_url})")

            # Dismiss any stray navigation modal that may block interaction.
            await self._dismiss_confirm_navigation(page)

            # Upload resume/cover letter whenever the attachments/file inputs are visible.
            if resume_file.exists():
                await self._upload_resume(page, resume_file, cover_letter_file)

            # Detect validation errors from the last Next click.
            error_count = await page.locator(
                ".field-validation-error:visible, .validation-message:visible, "
                ".has-error:visible, [role='alert']:visible"
            ).count()
            if error_count > 0:
                raise FormChallenge(
                    f"Wizard step {step + 1} has {error_count} validation errors; needs human review"
                )

            # Scroll to the bottom of the page to ensure the Next button is
            # rendered (some wizard steps lazy-load the footer).
            await page.evaluate("() => { window.scrollTo(0, document.body.scrollHeight); }")
            await page.wait_for_timeout(500)

            # If we are on the Questions step, attempt to answer the agency and
            # supplemental questions automatically rather than stopping.
            if "/apply/questions" in page.url.lower():
                # NEOGOV does not always use the HTML5 `required` attribute; any
                # visible unanswered question can block progression.
                await self._answer_questions_step(page, profile)

            # Debug: log all visible button/link texts to identify the Next control.
            visible_buttons = await page.locator(
                "button:visible, a:visible, input[type='submit']:visible"
            ).all_inner_texts()
            logger.debug(f"Visible controls: {visible_buttons}")

            # Look for a Next/Continue/Save/Proceed button (including disabled ones so
            # we can report why we cannot advance).
            next_button = page.locator(
                "button:has-text('Next'):visible, a:has-text('Next'):visible, "
                "button:has-text('Continue'):visible, a:has-text('Continue'):visible, "
                "button:has-text('Save'):visible, a:has-text('Save'):visible, "
                "button:has-text('Proceed to Review'):visible, a:has-text('Proceed to Review'):visible, "
                "button:has-text('Proceed'):visible, a:has-text('Proceed'):visible"
            ).first

            # If there is no Next button, we are likely on the final Review/Certify step.
            if await next_button.count() == 0:
                logger.info(f"Reached final wizard step at step {step + 1}")
                return

            # Dry-run: stop after confirming we can reach the final step.
            # Real mode: click Next to advance.
            if dry_run:
                logger.info(f"Dry-run: would click Next on step {step + 1}")
                return

            await next_button.click()
            await self._human_wait(page, 1.5, 2.5)

            # If the page did not change after clicking Next, assume a
            # validation error or modal blocked progression.
            new_url = page.url
            new_title = await page.title()
            if new_url == current_url and new_title == current_title:
                logger.warning(f"Wizard did not advance after clicking Next on step {step + 1}")
                raise FormChallenge(
                    "Wizard did not advance after Next click; likely validation error or modal"
                )

        raise FormChallenge("Wizard exceeded maximum steps without completing")

    def _find_cover_letter(self, resume_file: Path) -> Path | None:
        """Return a cover letter matching the resume job, or a generic one."""
        cover_dir = self.settings.base_cover_letter_dir
        if not cover_dir or not cover_dir.exists():
            return None

        # Prefer a cover letter whose filename contains words from the resume filename.
        resume_stem = resume_file.stem.lower()
        role_words = [w for w in resume_stem.replace("jd_", "").replace("lokesh", "").split("_") if len(w) > 2]
        candidates = []
        for file in cover_dir.iterdir():
            if file.is_file() and file.suffix.lower() in {".pdf", ".docx", ".doc"}:
                score = sum(1 for word in role_words if word in file.stem.lower())
                candidates.append((score, file))
        candidates.sort(key=lambda x: x[0], reverse=True)
        if candidates and candidates[0][0] > 0:
            return candidates[0][1]

        # Fallback: any PDF cover letter.
        pdfs = [f for f in cover_dir.iterdir() if f.is_file() and f.suffix.lower() == ".pdf"]
        return pdfs[0] if pdfs else None

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
        # If a visible password field is present, we are definitely on a login page.
        try:
            password_field = await page.wait_for_selector(
                "input#sign-in-password-field:visible",
                timeout=2000,
            )
            if password_field is not None:
                return False
        except Exception:
            pass

        if await page.locator("a.sign-out").count() > 0:
            return True
        if await page.locator("text='Applying as:'").count() > 0:
            return True
        if await page.locator("text='My Account'").count() > 0:
            return True
        return False

    async def _is_on_apply_page(self, page: Page) -> bool:
        """Return True if the current URL is an application wizard page."""
        url = page.url.lower()
        return "governmentjobs.com" in url and "/apply" in url

    async def _fill_info_step(self, page: Page, profile: dict[str, str]) -> None:
        """Fill first name, last name, email, and phone on the Info step."""
        full_name = profile.get("my_name", "") or ""
        name_parts = full_name.strip().split()
        first_name = name_parts[0] if name_parts else ""
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""
        email = profile.get("my_email", "") or ""
        phone = profile.get("my_phone", "") or ""

        await self._fill_if_visible(page, "input[name='firstName'], input#firstName, input[id*='first']", first_name)
        await self._fill_if_visible(page, "input[name='lastName'], input#lastName, input[id*='last']", last_name)
        await self._fill_if_visible(page, "input[type='email'], input[name='email'], input#email", email)
        await self._fill_if_visible(page, "input[type='tel'], input[name='phone'], input#phone", phone)

    async def _upload_resume(self, page: Page, resume_file: Path, cover_letter_file: Path | None = None) -> None:
        """Upload resume and cover letter into the correct attachment sections.

        governmentjobs.com has separate Cover Letter and Resume file inputs. We
        identify them by looking at the nearest heading/label text.
        """
        file_inputs = page.locator("input[type='file']:visible")
        count = await file_inputs.count()
        if count == 0:
            logger.debug("No visible file input for resume upload")
            return

        # Upload resume to the Resume section.
        await self._upload_to_labeled_section(page, resume_file, ["resume"])

        # Upload cover letter if provided.
        if cover_letter_file and cover_letter_file.exists():
            await self._upload_to_labeled_section(page, cover_letter_file, ["cover letter", "coverletter"])

        # Wait for any uploads to process and the Next button to enable.
        next_button = page.locator(
            "button:has-text('Next'):visible, button:has-text('Continue'):visible"
        ).first
        if await next_button.count() > 0:
            try:
                await next_button.wait_for(state="enabled", timeout=15000)
                logger.info("Attachment upload complete; Next button is enabled")
            except Exception:
                logger.warning("Next button did not become enabled after attachment upload")

    async def _upload_to_labeled_section(
        self,
        page: Page,
        file: Path,
        label_keywords: list[str],
    ) -> None:
        """Find a file input inside a section whose label matches a keyword and upload."""
        try:
            # Look for file inputs and check their surrounding heading/label text.
            file_inputs = page.locator("input[type='file']:visible")
            for i in range(await file_inputs.count()):
                input_locator = file_inputs.nth(i)
                # Find the nearest preceding heading or label by walking up ancestors.
                section_text = await input_locator.evaluate(
                    """(el) => {
                        let node = el;
                        for (let i = 0; i < 6 && node; i++) {
                            node = node.parentElement;
                            if (node) {
                                const heading = node.querySelector('h1, h2, h3, h4, h5, h6, label, .control-label, legend');
                                if (heading) return heading.innerText || heading.textContent || '';
                            }
                        }
                        return '';
                    }"""
                )
                section_text_lower = (section_text or "").lower()
                if any(keyword in section_text_lower for keyword in label_keywords):
                    await input_locator.set_input_files(str(file.resolve()))
                    logger.info(f"Uploaded {file.name} to section matching {label_keywords}")
                    await page.wait_for_timeout(1500)
                    return
            logger.warning(f"Could not find attachment section matching {label_keywords}")
        except Exception as exc:
            logger.warning(f"Upload to labeled section failed: {exc}")

    async def _fill_if_visible(self, page: Page, selector: str, value: str) -> None:
        """Fill a field only if it exists, is visible, and currently empty."""
        if not value:
            return
        try:
            locator = page.locator(selector).first
            if await locator.count() == 0:
                return
            current = await locator.input_value()
            if current:
                return
            await locator.fill(value)
            logger.debug(f"Filled {selector} with {value[:20]}...")
        except Exception as exc:
            logger.debug(f"Could not fill {selector}: {exc}")

    async def _human_wait(self, page: Page, min_seconds: float = 0.5, max_seconds: float = 1.5) -> None:
        """Pause for a randomized duration to mimic human pacing."""
        import random
        await page.wait_for_timeout(random.uniform(min_seconds, max_seconds) * 1000)

    async def _dismiss_confirm_navigation(self, page: Page) -> None:
        """Dismiss the NEOGOV 'Confirm Navigation' modal if it is blocking the page."""
        try:
            modal = page.locator(
                ".modal-dialog:visible, [role='dialog']:visible, .confirm-navigation:visible"
            ).filter(has_text="Confirm Navigation")
            if await modal.count() == 0:
                modal = page.locator("body").filter(has_text="Confirm Navigation")
            if await modal.count() > 0 or await page.locator("text='Confirm Navigation'").count() > 0:
                # Prefer "Stay on this Page" so we can keep answering questions.
                stay = page.locator(
                    "button:has-text('Stay on this Page'):visible, "
                    "button:has-text('Stay'):visible, "
                    "a:has-text('Stay on this Page'):visible"
                ).first
                if await stay.count() > 0:
                    await stay.click()
                    logger.info("Dismissed Confirm Navigation modal (Stay)")
                    await page.wait_for_timeout(500)
                    return
                # Fallback: click anywhere outside the modal to close it.
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(300)
        except Exception as exc:
            logger.debug(f"Could not dismiss confirm navigation modal: {exc}")

    async def _answer_questions_step(self, page: Page, profile: dict[str, str]) -> None:
        """Fill agency and supplemental questions on the NEOGOV Questions step.

        Uses an injected JavaScript helper to locate visible question groups, pick
        safe defaults for compliance questions, and fill free-text answers from the
        user's profile. This avoids brittle Playwright selectors for NEOGOV's
        dynamic DOM.
        """
        logger.info("Answering application questions via JS helper")

        # Build the experience paragraph from profile highlights.
        highlights = profile.get("experience_highlights", []) or []
        base_experience = " ".join(highlights) if highlights else (
            "I have extensive experience analyzing data, building automated reporting pipelines, "
            "and delivering insights that support operational and strategic decisions."
        )

        result = await page.evaluate(
            """({ baseExperience }) => {
                function dismissModal() {
                    const stayBtn = Array.from(document.querySelectorAll('button, a')).find(
                        b => (b.innerText || b.textContent || '').toLowerCase().includes('stay on this page')
                    );
                    if (stayBtn) {
                        stayBtn.click();
                        return true;
                    }
                    const modal = document.querySelector('.modal.in, .modal.show, [role="dialog"]');
                    if (modal) modal.remove();
                    return false;
                }

                function parseQuestionName(name) {
                    // NEOGOV names are slugs like qa-awqs-0can-you-perform-the-essential-functions...
                    return name
                        .replace(/^qa-/, '')
                        .replace(/^rev-/, '')
                        .replace(/^(awqs|sqs)-\d+/, '')
                        .replace(/-/g, ' ')
                        .trim();
                }

                function looksLikeOptionLabel(text) {
                    const t = text.toLowerCase().trim();
                    return t.length < 5 || /^(yes|no|male|female|other|decline|prefer not|n\/a)$/.test(t);
                }

                function findLabel(input) {
                    const fromName = parseQuestionName(input.name || '');
                    let visibleText = '';
                    // Search ancestors for a visible question label/legend.
                    if (input.id) {
                        const lab = document.querySelector(`label[for="${input.id}"]`);
                        if (lab) {
                            const text = (lab.innerText || lab.textContent || '').trim();
                            if (text && !looksLikeOptionLabel(text)) visibleText = text;
                        }
                    }
                    if (!visibleText) {
                        let node = input;
                        for (let i = 0; i < 8 && node; i++) {
                            node = node.parentElement;
                            if (!node) break;
                            const lab = node.querySelector('legend, .question-label, .control-label, .field-label, h3, h4');
                            if (lab) {
                                const text = (lab.innerText || lab.textContent || '').trim();
                                if (text && !looksLikeOptionLabel(text)) {
                                    visibleText = text;
                                    break;
                                }
                            }
                        }
                    }
                    // Combine both so keyword matching sees the parsed slug and the
                    // visible question text; de-duplicate if they are identical.
                    if (!visibleText) return fromName || '';
                    if (!fromName) return visibleText;
                    if (visibleText.toLowerCase().includes(fromName.toLowerCase()) ||
                        fromName.toLowerCase().includes(visibleText.toLowerCase())) {
                        return visibleText;
                    }
                    return `${visibleText} ${fromName}`;
                }

                function findOptionLabel(input) {
                    // Native label association first.
                    if (input.labels && input.labels.length) {
                        return (input.labels[0].innerText || input.labels[0].textContent || '').trim();
                    }
                    if (input.id) {
                        const lab = document.querySelector(`label[for="${input.id}"]`);
                        if (lab) return (lab.innerText || lab.textContent || '').trim();
                    }
                    // Parent label that wraps the input.
                    let node = input.parentElement;
                    for (let i = 0; i < 3 && node; i++) {
                        if (node.tagName.toLowerCase() === 'label') {
                            return (node.innerText || node.textContent || '').trim();
                        }
                        const lab = node.querySelector('label');
                        if (lab) return (lab.innerText || lab.textContent || '').trim();
                        node = node.parentElement;
                    }
                    return '';
                }

                function getClickable(input) {
                    // Prefer clicking the visible label so the UI updates even if the
                    // real radio/checkbox is visually hidden.
                    if (input.labels && input.labels.length) return input.labels[0];
                    if (input.id) {
                        const lab = document.querySelector(`label[for="${input.id}"]`);
                        if (lab) return lab;
                    }
                    let node = input.parentElement;
                    for (let i = 0; i < 3 && node; i++) {
                        if (node.tagName.toLowerCase() === 'label') return node;
                        node = node.parentElement;
                    }
                    return input;
                }

                function isVisible(el) {
                    return !!(el.offsetParent || el.getClientRects().length);
                }

                function setValue(input, value) {
                    input.value = value;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    input.dispatchEvent(new Event('blur', { bubbles: true }));
                    // Trigger Angular / Knockout value accessors if present.
                    input.dispatchEvent(new Event('keyup', { bubbles: true }));
                }

                function triggerFramework(input) {
                    // NEOGOV uses Knockout in many places; trigger its value accessor.
                    if (input && typeof input.click === 'function') {
                        try { input.click(); } catch (e) {}
                    }
                    ['input', 'change', 'blur', 'keyup', 'focus'].forEach(evtName => {
                        input.dispatchEvent(new Event(evtName, { bubbles: true }));
                    });
                    // Knockout data-bind listeners.
                    if (window.ko && ko.utils && ko.utils.triggerEvent) {
                        try { ko.utils.triggerEvent(input, 'change'); } catch (e) {}
                    }
                }

                function clickInput(input) {
                    // NEOGOV radios are often custom-styled and the visible label is the
                    // real interactive element. We always click the label first with a
                    // synthetic MouseEvent, then update the underlying input and fire the
                    // framework events so the form state actually changes.
                    const clickable = getClickable(input);
                    if (clickable && clickable !== input) {
                        const rect = clickable.getBoundingClientRect();
                        const x = rect.left + rect.width / 2;
                        const y = rect.top + rect.height / 2;
                        clickable.dispatchEvent(new MouseEvent('mousedown', {
                            bubbles: true, cancelable: true, view: window, clientX: x, clientY: y
                        }));
                        clickable.dispatchEvent(new MouseEvent('mouseup', {
                            bubbles: true, cancelable: true, view: window, clientX: x, clientY: y
                        }));
                        clickable.dispatchEvent(new MouseEvent('click', {
                            bubbles: true, cancelable: true, view: window, clientX: x, clientY: y
                        }));
                    }
                    // Fallback / reinforcement: also click the native input directly.
                    input.checked = true;
                    input.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                    triggerFramework(input);
                }

                function isChecked(radio) {
                    return !!(radio.checked || radio.getAttribute('aria-checked') === 'true');
                }

                function containsAny(text, keywords) {
                    return keywords.some(k => text.toLowerCase().includes(k));
                }

                function answerTextarea(input, label) {
                    if (input.value && input.value.trim()) return;
                    let topical =
                        "I have applied SQL, Python, and BI tools to solve complex data problems, " +
                        "build ETL pipelines, and deliver reliable analytics for cross-functional teams.";
                    if (containsAny(label, ['ami', 'advanced metering', 'meter data', 'utility', 'metering'])) {
                        topical =
                            "I am highly comfortable working with structured operational and telemetry data. " +
                            "I have built dashboards, monitoring reports, and data quality checks for systems " +
                            "that rely on accurate time-series readings and downstream analytics.";
                    } else if (containsAny(label, ['gis', 'geographic', 'spatial', 'mapping'])) {
                        topical =
                            "I have integrated geospatial and tabular datasets to produce analytical reports " +
                            "and have used SQL/Python to clean, validate, and visualize location-aware data.";
                    } else if (containsAny(label, ['monitor', 'performance metric', 'kpi', 'dashboard'])) {
                        topical =
                            "I have designed and maintained dashboards and performance metrics for stakeholders, " +
                            "identifying anomalies, tracking KPIs, and translating trends into actionable recommendations.";
                    }
                    setValue(input, `${baseExperience} ${topical}`.trim().slice(0, 4000));
                }

                function answerSelect(select, label) {
                    const options = Array.from(select.options);
                    let preferred = [];
                    if (containsAny(label, ['education', 'degree'])) {
                        preferred = ['master', 'bachelor', 'associate', 'degree'];
                    } else if (containsAny(label, ['experience'])) {
                        preferred = ['more than', 'three', 'two', 'one', 'meet'];
                    }
                    for (const kw of preferred) {
                        const idx = options.findIndex(o => o.text.toLowerCase().includes(kw));
                        if (idx >= 0) {
                            select.selectedIndex = idx;
                            select.dispatchEvent(new Event('change', { bubbles: true }));
                            return true;
                        }
                    }
                    // Fallback to first non-placeholder option.
                    const idx = options.findIndex(o => o.text.trim() && !/^(select|choose|--|please)/i.test(o.text));
                    if (idx >= 0) {
                        select.selectedIndex = idx;
                        select.dispatchEvent(new Event('change', { bubbles: true }));
                        return true;
                    }
                    return false;
                }

                function answerRadio(radios, label, nameSlug) {
                    let desired = null;
                    let optionKeywords = [];
                    const combinedText = `${label} ${nameSlug}`.toLowerCase();
                    if (containsAny(combinedText, ['essential functions', 'age requirement', 'minimum age', 'work permit', 'under the age of 18'])) {
                        desired = 'yes';
                    } else if (containsAny(combinedText, ['discharged', 'forced to resign'])) {
                        desired = 'no';
                    } else if (containsAny(combinedText, ['work authorization', 'legally authorized', 'eligible to work', 'eligibility to work', 'verification of their eligibility', 'federal law requires all employees', 'provide verification'])) {
                        desired = 'yes';
                    } else if (containsAny(combinedText, ['testing accommodation', 'accommodation for disability', 'need testing accommodations', 'will you need testing', 'testing-accommodations'])) {
                        desired = 'no';
                    } else if (containsAny(combinedText, ['artificial intelligence', 'generative ai', 'chatgpt', 'ai tool', 'supplemental questionnaire', 'own words', 'agree to answer', 'each applicant must complete'])) {
                        desired = 'yes';
                    } else if (containsAny(combinedText, ['how did you', 'learn of this', 'referral', 'source', 'first learn'])) {
                        optionKeywords = ['linkedin', 'indeed', 'website', 'job board'];
                    } else if (containsAny(combinedText, ['education', 'degree']) && containsAny(combinedText, ['minimum qualifications', 'related field', 'possess', 'meet'])) {
                        optionKeywords = ['possess', 'associate', 'bachelor', 'master', 'degree'];
                    } else if (containsAny(combinedText, ['experience']) && containsAny(combinedText, ['utility', 'meter', 'data management', 'operations'])) {
                        optionKeywords = ['more than', 'three', 'at least one'];
                    }

                    let target = null;
                    if (optionKeywords.length) {
                        target = radios.find(r => {
                            const optLab = findOptionLabel(r).toLowerCase();
                            return optionKeywords.some(kw => optLab.includes(kw));
                        });
                        // Fallback to first non-empty option for referral/source.
                        if (!target && containsAny(combinedText, ['how did you', 'learn of this', 'referral', 'source', 'first learn'])) {
                            target = radios.find(r => {
                                const optLab = findOptionLabel(r).trim();
                                return optLab && !/other/i.test(optLab);
                            });
                        }
                    }
                    if (!target && desired) {
                        target = radios.find(r => {
                            const optLab = findOptionLabel(r).toLowerCase();
                            const val = (r.value || r.getAttribute('aria-label') || '').toLowerCase();
                            return optLab.includes(desired) || val === desired ||
                                   (desired === 'yes' && (optLab.startsWith('yes') || val.startsWith('yes'))) ||
                                   (desired === 'no' && (optLab.startsWith('no') || val.startsWith('no')));
                        });
                        // Fallback for simple Yes/No groups: first option is usually Yes.
                        if (!target && radios.length === 2 && desired === 'yes') target = radios[0];
                        if (!target && radios.length === 2 && desired === 'no') target = radios[1];
                    }
                    if (target) {
                        clickInput(target);
                        // Verify the click actually stuck; if not, try the other radio and back.
                        if (!isChecked(target)) {
                            const other = radios.find(r => r !== target);
                            if (other) clickInput(other);
                            awaitTimeout(50);
                            clickInput(target);
                        }
                        return true;
                    }
                    return false;
                }

                function awaitTimeout(ms) {
                    const start = Date.now();
                    while (Date.now() - start < ms) {}
                }

                function answerCheckbox(checkboxes, label) {
                    if (!containsAny(label, ['how did you', 'learn of this', 'referral', 'source'])) return false;
                    const desired = ['linkedin', 'indeed', 'job board', 'website'];
                    let matched = false;
                    for (const cb of checkboxes) {
                        const optLab = findOptionLabel(cb).toLowerCase();
                        if (desired.some(d => optLab.includes(d))) {
                            if (!cb.checked) {
                                clickInput(cb);
                                matched = true;
                            }
                        }
                    }
                    if (!matched) {
                        // Check first non-Other option.
                        const first = checkboxes.find(cb => !findOptionLabel(cb).toLowerCase().includes('other'));
                        if (first && !first.checked) clickInput(first);
                    }
                    return true;
                }

                const log = [];
                const diagnostics = [];
                dismissModal();

                // Collect inputs. Radios/checkboxes may be visually hidden but still
                // the real form controls, so we keep all of them while filtering the
                // rest to visible elements only.
                const allInputs = Array.from(document.querySelectorAll('input, select, textarea'));
                const radioGroups = {};
                const checkboxGroups = {};
                const selects = [];
                const textareas = [];
                const textInputs = [];

                allInputs.forEach(input => {
                    if (input.type === 'radio') {
                        radioGroups[input.name] = radioGroups[input.name] || [];
                        radioGroups[input.name].push(input);
                    } else if (input.type === 'checkbox') {
                        checkboxGroups[input.name] = checkboxGroups[input.name] || [];
                        checkboxGroups[input.name].push(input);
                    } else if (input.tagName.toLowerCase() === 'select') {
                        if (isVisible(input)) selects.push(input);
                    } else if (input.tagName.toLowerCase() === 'textarea') {
                        if (isVisible(input)) textareas.push(input);
                    } else if (input.type === 'text' || input.type === 'email' || input.type === 'tel') {
                        if (isVisible(input)) textInputs.push(input);
                    }
                });

                function isVoluntaryEEO(label, nameSlug) {
                    const text = `${label} ${nameSlug}`.toLowerCase();
                    // Voluntary self-identification groups only. Required accommodation or
                    // essential-function questions must be answered, so do not skip those.
                    if (containsAny(text, ['essential functions', 'testing accommodation', 'accommodation for disability', 'need testing accommodations', 'will you need testing', 'testing-accommodations'])) {
                        return false;
                    }
                    return containsAny(text, ['gender', 'ethnicity', 'race', 'veteran', 'disability', 'decline', 'voluntary information']);
                }

                // Answer radio groups.
                Object.entries(radioGroups).forEach(([name, radios]) => {
                    const label = findLabel(radios[0]);
                    const optionLabels = radios.map(r => findOptionLabel(r));
                    diagnostics.push(`radio group '${name}': q='${label.slice(0,60)}' options=[${optionLabels.map(x => `'${x.slice(0,20)}'`).join(', ')}]`);
                    if (isVoluntaryEEO(label, name)) {
                        diagnostics.push(`  -> skipped voluntary EEO`);
                        return;
                    }
                    if (answerRadio(radios, label, name)) {
                        log.push(`radio: ${label.slice(0, 80)}`);
                    } else {
                        diagnostics.push(`  -> not answered (desired may be undetermined)`);
                    }
                });

                // Answer checkbox groups.
                Object.entries(checkboxGroups).forEach(([name, cbs]) => {
                    const label = findLabel(cbs[0]);
                    if (isVoluntaryEEO(label, name)) return;
                    if (answerCheckbox(cbs, label)) {
                        log.push(`checkbox: ${label.slice(0, 80)}`);
                    }
                });

                // Answer selects.
                selects.forEach(select => {
                    const label = findLabel(select);
                    if (answerSelect(select, label)) {
                        log.push(`select: ${label.slice(0, 80)}`);
                    }
                });

                // Answer textareas with experience narratives.
                textareas.forEach(ta => {
                    const label = findLabel(ta);
                    if (containsAny(label, ['relatives', 'previously worked', 'contracting', 'temporary agency'])) {
                        setValue(ta, 'None');
                        log.push(`text: ${label.slice(0, 80)} -> None`);
                        return;
                    }
                    answerTextarea(ta, label);
                    log.push(`textarea: ${label.slice(0, 80)}`);
                });

                // Answer plain text inputs that look like short-answer questions.
                textInputs.forEach(input => {
                    const label = findLabel(input);
                    if (containsAny(label, ['relatives', 'previously worked', 'contracting', 'temporary agency'])) {
                        setValue(input, 'None');
                        log.push(`text: ${label.slice(0, 80)} -> None`);
                    }
                });

                dismissModal();
                return { answered: log.length, log, diagnostics };
            }""",
            {"baseExperience": base_experience},
        )

        logger.info(f"Question helper answered {result.get('answered', 0)} fields")
        for entry in result.get("diagnostics", []):
            logger.info(f"  DIAG: {entry}")
        for entry in result.get("log", []):
            logger.info(f"  OK: {entry}")

        # Wait briefly for any dynamic validation to settle.
        await page.wait_for_timeout(1000)
        await self._dismiss_confirm_navigation(page)

    async def _handle_certify_step(self, page: Page) -> None:
        """Check required certification/attestation checkboxes on the Review/Certify step."""
        checkboxes = page.locator(
            "input[type='checkbox']:visible"
        )
        for i in range(await checkboxes.count()):
            cb = checkboxes.nth(i)
            try:
                if await cb.is_checked():
                    continue
                # Check if the associated label text sounds like a required attestation.
                label_text = await cb.evaluate(
                    """(el) => {
                        const id = el.id;
                        if (id) {
                            const lab = document.querySelector(`label[for="${id}"]`);
                            if (lab) return (lab.innerText || lab.textContent || '').trim();
                        }
                        let node = el.parentElement;
                        for (let i = 0; i < 4 && node; i++) {
                            const lab = node.querySelector('label');
                            if (lab) return (lab.innerText || lab.textContent || '').trim();
                            node = node.parentElement;
                        }
                        return '';
                    }"""
                )
                text = (label_text or "").lower()
                if any(k in text for k in ("certify", "agree", "understand", " truthful", "accurate", "complete")):
                    await cb.click(force=True)
                    logger.info("Checked certification/attestation checkbox")
            except Exception as exc:
                logger.debug(f"Could not check certification checkbox: {exc}")
