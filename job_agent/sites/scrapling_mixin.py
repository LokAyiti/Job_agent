"""Mixin for routing submission page rendering through the Scrapling service.

This is used when a known or unknown ATS platform presents anti-bot challenges
(e.g., Cloudflare Turnstile) so Playwright alone cannot fetch the page.
"""
from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from job_agent.config import Settings
from job_agent.scrapling_client import ScraplingClient, ScraplingServiceError, get_scrapling_client


class ScraplingSubmissionMixin:
    """Fetch a stealth-rendered snapshot of a submission/ATS page via Scrapling."""

    def __init__(self, settings: Optional[Settings] = None, client: Optional[ScraplingClient] = None):
        self.settings = settings or (client.settings if client else Settings(_env_file=None))
        self.client = client or get_scrapling_client(self.settings)

    def get_stealth_snapshot(self, url: str) -> Optional[dict[str, Any]]:
        """Return a snapshot (HTML, URL, title, form fields) of a protected page.

        Returns None if the Scrapling service is disabled or unreachable.
        """
        if not self.client.use_service:
            logger.debug("Scrapling service disabled; skipping stealth snapshot")
            return None

        try:
            response = self.client.stealth_submit_snapshot(url)
            data = response.json()
        except ScraplingServiceError:
            logger.warning(f"Scrapling service could not render {url}")
            return None
        except Exception as exc:
            logger.warning(f"Failed to parse stealth snapshot for {url}: {exc}")
            return None

        if not isinstance(data, dict):
            return None

        return {
            "url": data.get("url", url),
            "html": data.get("html", ""),
            "title": data.get("title", ""),
            "status": data.get("status", 0),
            "form_fields": data.get("form_fields", []),
            "text": data.get("text", ""),
        }

    def is_cloudflare_blocked(self, html: str) -> bool:
        """Detect a Cloudflare challenge page in an HTML snapshot."""
        if not html:
            return False
        lowered = html.lower()
        indicators = [
            "just a moment",
            "checking your browser",
            "verify you are human",
            "cloudflare",
            "challenge-running",
            "cf-browser-verification",
        ]
        return any(indicator in lowered for indicator in indicators)


def get_stealth_snapshot(url: str, settings: Optional[Settings] = None) -> Optional[dict[str, Any]]:
    """Convenience shortcut for one-off stealth snapshots."""
    mixin = ScraplingSubmissionMixin(settings=settings)
    return mixin.get_stealth_snapshot(url)
