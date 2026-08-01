"""GovernmentJobs.com scraper using Playwright."""

import logging
import re
import time
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext

from config.settings import Settings
from models.job_models import JobListing
from utils.captcha_solver import CaptchaSolver

logger = logging.getLogger(__name__)


# United States does not work as a single location on this site; we iterate states.
US_STATES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut",
    "Delaware", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa",
    "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan",
    "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire",
    "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio",
    "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
    "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington", "West Virginia",
    "Wisconsin", "Wyoming",
]


class GovernmentJobsScraper:
    """Scrape Data Analyst job listings from governmentjobs.com."""

    BASE_URL = "https://www.governmentjobs.com"

    def __init__(
        self,
        headless: bool = True,
        max_pages_per_state: int = 2,
        max_states: int | None = None,
        title_filter: str = "data analyst",
    ) -> None:
        self.headless = headless
        self.max_pages_per_state = max_pages_per_state
        self.max_states = max_states
        self.title_filter = title_filter.lower()
        self.context: BrowserContext | None = None
        self.browser: Browser | None = None

    def _build_search_url(self, location: str) -> str:
        params = {
            "keyword": Settings.JOB_TITLE,
            "location": location,
        }
        return f"{self.BASE_URL}/jobs?{urlencode(params)}"

    def start(self) -> None:
        """Launch browser and create a new context."""
        logger.info("Starting Playwright browser")
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        self.context = self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )
        self.context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

    def stop(self) -> None:
        """Close browser and Playwright."""
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    def _detect_captcha(self, page: Page) -> bool:
        """Return True if the page appears to contain a CAPTCHA challenge."""
        captcha_indicators = [
            "recaptcha",
            "g-recaptcha",
            "hcaptcha",
            "captcha",
            "verify you are human",
        ]
        content = page.content().lower()
        return any(indicator in content for indicator in captcha_indicators)

    def _login(self, page: Page) -> None:
        """Log into the site using credentials from .env."""
        if not Settings.LOGIN_EMAIL or not Settings.LOGIN_PASSWORD:
            logger.warning("Login credentials not configured; skipping login")
            return

        logger.info("Navigating to login page")
        page.goto(f"{self.BASE_URL}/Applications/Submitted", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")

        # Dismiss the cookie consent banner if it appears.
        try:
            page.locator("button.osano-cm-accept").first.click(timeout=3000)
            page.wait_for_timeout(500)
        except Exception:
            pass

        # Fill the visible governmentjobs.com sign-in form.
        page.fill('input#username-or-email-field:visible', Settings.LOGIN_EMAIL)
        page.fill('input#sign-in-password-field:visible', Settings.LOGIN_PASSWORD)
        page.locator('button:has-text("Sign In"):visible').first.click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3000)

        if self._is_logged_in(page):
            logger.info("Login successful")
            return

        # If still not logged in, a visible CAPTCHA may be blocking submission.
        logger.warning("Login failed; checking for CAPTCHA challenge")
        if not Settings.CAPTCHA_API_KEY:
            raise RuntimeError("Login failed and 2Captcha API key is not configured")
        try:
            solver = CaptchaSolver(Settings.CAPTCHA_API_KEY, timeout=120)
            solver.solve_on_page(page)
            page.wait_for_timeout(3000)
            if not self._is_logged_in(page):
                raise RuntimeError("Login still failed after CAPTCHA attempt")
            logger.info("Login successful after CAPTCHA")
        except Exception as exc:
            logger.warning("Failed to solve CAPTCHA on login page: %s", exc)
            raise RuntimeError(f"Login failed or CAPTCHA unsolvable: {exc}") from exc

    def _is_logged_in(self, page: Page) -> bool:
        """Return True if the page shows a logged-in session."""
        return page.locator("a.sign-out").count() > 0 or page.locator('input#sign-in-password-field:visible').count() == 0

    def _parse_search_page_html(self, html: str) -> list[JobListing]:
        """Parse job listings from the search result HTML."""
        soup = BeautifulSoup(html, "lxml")
        listings: list[JobListing] = []
        container = soup.find("ul", class_="job-listing-container")
        if not container:
            logger.warning("No job-listing-container found in search page")
            return listings

        for item in container.find_all("li", class_="job-item"):
            try:
                title_a = item.find("a", class_="job-details-link")
                if not title_a:
                    continue
                title = title_a.get_text(strip=True)
                href = title_a.get("href", "")
                application_url = urljoin(self.BASE_URL, href)

                # Filter by title keyword
                if self.title_filter and self.title_filter not in title.lower():
                    continue

                job_id = item.get("data-job-id", "")

                company = ""
                company_el = item.find("div", class_="job-organization")
                if company_el:
                    company = company_el.get_text(strip=True)

                location = ""
                primary_info = item.find_all("div", class_="primaryInfo")
                for info in primary_info:
                    if "job-organization" not in info.get("class", []):
                        text = info.get_text(strip=True)
                        # Heuristic: location usually contains a state abbreviation
                        if re.search(r",\s*[A-Za-z]{2}\b|\bRemote\b", text) or text in US_STATES:
                            location = text
                        break

                listings.append(
                    JobListing(
                        job_id=job_id,
                        title=title,
                        company=company,
                        location=location,
                        application_url=application_url,
                    )
                )
            except Exception as exc:
                logger.warning("Failed to parse a job item: %s", exc)
                continue

        return listings

    def _extract_job_cards(self, page: Page) -> list[JobListing]:
        """Extract job listings from the current search page."""
        html = page.content()
        return self._parse_search_page_html(html)

    def _extract_job_details(self, page: Page, listing: JobListing) -> JobListing:
        """Open the job detail page and extract full description and requirements."""
        if not listing.application_url:
            return listing

        try:
            page.goto(listing.application_url, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                logger.warning("Network idle timeout for job details %s", listing.application_url)
            time.sleep(2)

            html = page.content()
            soup = BeautifulSoup(html, "lxml")

            # The main job details content is inside the tab-content container
            details = soup.find("div", class_="tab-content")
            if not details:
                details = soup.find("div", class_="entity-details-tab")
            if not details:
                details = soup

            text = details.get_text(separator="\n", strip=True)

            # Extract requirements: from "Qualifications Required" up to next major section
            requirements = ""
            qual_match = re.search(
                r"(?i)Qualifications Required\s*[\n:]*(.*?)(?=\n?(Physical Qualifications Required|Qualifications Preferred|Documents Required|Job Description|Employer Login|$))",
                text,
                re.DOTALL,
            )
            if qual_match:
                requirements = qual_match.group(1).strip()

            # Description is everything up to "Qualifications Required"
            description = text
            if requirements:
                description = text.split("Qualifications Required", 1)[0].strip()
            elif "Qualifications Required" in text:
                description = text.split("Qualifications Required", 1)[0].strip()

            # The main content starts after the "Benefits" tab label
            description = re.sub(r"(?is)^.*?Benefits\s*", "", description, count=1).strip()

            listing.description = description
            listing.requirements = requirements

            # Pull employer if missing
            if not listing.company:
                employer_match = re.search(r"Employer\s*\n\s*([^\n]+)", text)
                if employer_match:
                    listing.company = employer_match.group(1).strip()

        except Exception as exc:
            logger.warning(
                "Failed to extract details for %s: %s", listing.application_url, exc
            )

        return listing

    def _has_next_page(self, page: Page) -> bool:
        """Return True if a next page link is present and enabled."""
        next_btn = page.query_selector("a[aria-label='Next']")
        if not next_btn:
            return False
        aria = next_btn.get_attribute("aria-disabled") or ""
        classes = next_btn.get_attribute("class") or ""
        return "disabled" not in classes.lower() and aria.lower() != "true"

    def _click_next_page(self, page: Page) -> None:
        """Click the next page button and wait for the listing container to update."""
        next_btn = page.query_selector("a[aria-label='Next']")
        if not next_btn:
            raise RuntimeError("Next page button not found")

        # Capture current first job ID to detect update
        html = page.content()
        soup = BeautifulSoup(html, "lxml")
        first = soup.find("li", class_="job-item")
        first_id = first.get("data-job-id") if first else ""

        next_btn.click()
        # Wait for listing container to refresh
        for _ in range(30):
            time.sleep(0.5)
            html = page.content()
            soup = BeautifulSoup(html, "lxml")
            new_first = soup.find("li", class_="job-item")
            if new_first and new_first.get("data-job-id") != first_id:
                break
        else:
            logger.warning("Listing container may not have updated after clicking next")

    def scrape(self, login: bool = False) -> list[JobListing]:
        """Scrape job listings across multiple states and pages."""
        self.start()
        listings: list[JobListing] = []
        seen_ids: set[str] = set()

        try:
            page = self.context.new_page()

            if login:
                self._login(page)

            states = US_STATES[: self.max_states] if self.max_states else US_STATES
            logger.info("Searching across %s states", len(states))

            for state in states:
                logger.info("Searching state: %s", state)
                search_url = self._build_search_url(state)
                page.goto(search_url, wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle", timeout=30000)
                except Exception:
                    logger.warning("Network idle timeout for %s; continuing after brief wait", state)
                time.sleep(2)

                # Check for no results
                if page.query_selector(".job-no-result-content, .job-search-no-results-container"):
                    logger.info("No results for %s", state)
                    continue

                for page_num in range(1, self.max_pages_per_state + 1):
                    logger.info("Processing %s page %s", state, page_num)
                    page_listings = self._extract_job_cards(page)
                    logger.info("Found %s matching job cards on %s page %s", len(page_listings), state, page_num)

                    for listing in page_listings:
                        if listing.job_id in seen_ids:
                            continue
                        seen_ids.add(listing.job_id)

                        detailed = self._extract_job_details(page, listing)
                        listings.append(detailed)
                        time.sleep(1)  # polite delay between detail pages

                    if page_num < self.max_pages_per_state and self._has_next_page(page):
                        self._click_next_page(page)
                    else:
                        break

                # Brief delay between states to avoid rate limiting
                time.sleep(2)
        finally:
            self.stop()

        logger.info("Total unique listings scraped: %s", len(listings))
        return listings


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    scraper = GovernmentJobsScraper(
        headless=False,
        max_pages_per_state=1,
        max_states=1,
        title_filter="analyst",
    )
    jobs = scraper.scrape(login=False)
    for job in jobs:
        print(job.model_dump_json(indent=2))
