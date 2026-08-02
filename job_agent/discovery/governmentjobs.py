"""GovernmentJobs.com discovery source powered by Scrapling."""
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup
from loguru import logger

from job_agent.discovery.base import JobDiscoverySource
from job_agent.models import JobApplication
from job_agent.scrapling_client import ScraplingClient, get_scrapling_client


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


class GovernmentJobsDiscovery(JobDiscoverySource):
    """Discover jobs from governmentjobs.com via the Scrapling client.

    The source fetches search result pages for a configurable set of states,
    parses the NEOGOV job listing HTML, and returns matching jobs. When
    ``SCRAPLING_USE_SERVICE=true`` the requests are routed through the
    Scrapling Docker service so Cloudflare-protected pages are handled there.
    """

    name = "governmentjobs"
    BASE_URL = "https://www.governmentjobs.com"

    def __init__(
        self,
        client: ScraplingClient | None = None,
        max_states: int | None = 5,
        max_pages_per_state: int = 1,
    ):
        self.client = client or get_scrapling_client()
        self.max_states = max_states
        self.max_pages_per_state = max_pages_per_state

    async def discover(self, profile: dict) -> list[JobApplication]:
        preferences = profile.get("preferences", {})
        target_roles = preferences.get("target_roles", ["Data Analyst"])
        # The scraper uses the first target role as its title filter.
        title_filter = target_roles[0].lower() if target_roles else ""

        states = US_STATES[: self.max_states] if self.max_states is not None else US_STATES
        jobs: list[JobApplication] = []
        seen_urls: set[str] = set()

        for state in states:
            page = 1
            while page <= self.max_pages_per_state:
                url = self._build_search_url(title_filter, state, page)
                try:
                    response = self.client.fetch(url, stealth=False)
                    listings = self._parse_search_page_html(response.text)
                    logger.debug(
                        f"governmentjobs [{state}] page {page}: {len(listings)} listings"
                    )
                except Exception as exc:
                    logger.warning(f"governmentjobs fetch failed for {state} page {page}: {exc}")
                    break

                added = 0
                for listing in listings:
                    if listing["application_url"] in seen_urls:
                        continue
                    seen_urls.add(listing["application_url"])
                    job = JobApplication(
                        title=listing["title"],
                        company=listing["company"],
                        url=listing["application_url"],
                        location=listing["location"],
                        source=self.name,
                        platform="governmentjobs",
                    )
                    if self._matches_preferences(job, profile):
                        jobs.append(job)
                        added += 1

                # Stop paginating if this page had no relevant results.
                if added == 0 and len(listings) == 0:
                    break
                page += 1

        logger.info(f"GovernmentJobs discovery returned {len(jobs)} matching jobs")
        return jobs

    def _build_search_url(self, keyword: str, location: str, page: int) -> str:
        params = {
            "keyword": keyword,
            "location": location,
        }
        if page > 1:
            params["page"] = page
        return f"{self.BASE_URL}/jobs?{urlencode(params)}"

    def _parse_search_page_html(self, html: str) -> list[dict[str, Any]]:
        """Parse job listings from a NEOGOV search result HTML page."""
        soup = BeautifulSoup(html, "lxml")
        listings: list[dict[str, Any]] = []
        container = soup.find("ul", class_="job-listing-container")
        if not container:
            logger.debug("No job-listing-container found in search page")
            return listings

        for item in container.find_all("li", class_="job-item"):
            try:
                title_a = item.find("a", class_="job-details-link")
                if not title_a:
                    continue
                title = title_a.get_text(strip=True)
                href = title_a.get("href", "")
                application_url = urljoin(self.BASE_URL, href)

                company = ""
                company_el = item.find("div", class_="job-organization")
                if company_el:
                    company = company_el.get_text(strip=True)

                location = ""
                primary_info = item.find_all("div", class_="primaryInfo")
                for info in primary_info:
                    if "job-organization" not in info.get("class", []):
                        text = info.get_text(strip=True)
                        if re.search(r",\s*[A-Za-z]{2}\b|\bRemote\b", text) or text in US_STATES:
                            location = text
                        break

                listings.append(
                    {
                        "title": title,
                        "company": company,
                        "location": location,
                        "application_url": application_url,
                    }
                )
            except Exception as exc:
                logger.warning(f"Failed to parse a job item: {exc}")
                continue

        return listings
