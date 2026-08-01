"""Client for the Scrapling service.

When `scrapling_use_service` is true, all discovery and stealth traffic is sent to
the Scrapling service (usually running in Docker). When false, the client falls back
to plain `requests` so the rest of the app can still be tested and run without the
Docker service.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

import requests
from loguru import logger

from job_agent.config import Settings


@dataclass
class ScraplingResponse:
    """Normalized response returned by the Scrapling client."""

    url: str
    status: int
    text: str
    html: str
    headers: dict = field(default_factory=dict)
    cookies: dict = field(default_factory=dict)
    _raw_response: Optional[Any] = field(default=None, repr=False)

    def json(self) -> Any:
        """Parse the response body as JSON, preferring the real response's json()."""
        raw = self._raw_response
        if raw is not None and hasattr(raw, "json"):
            try:
                return raw.json()
            except Exception:
                pass
        try:
            return json.loads(self.text)
        except Exception as exc:
            raise ValueError(f"Response is not valid JSON: {exc}") from exc

    def raise_for_status(self):
        # Prefer the real response object's raise_for_status if available.
        raw = self._raw_response
        if raw is not None and hasattr(raw, "raise_for_status"):
            try:
                raw.raise_for_status()
                return
            except Exception:
                raise

        try:
            status = int(self.status)
        except Exception:
            return
        if status >= 400:
            raise requests.HTTPError(f"HTTP {status} for {self.url}")


class ScraplingServiceError(Exception):
    """Raised when the Scrapling service returns an error or is unreachable."""


class ScraplingClient:
    """Thin wrapper around the Scrapling HTTP service (with a local fallback)."""

    DEFAULT_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or Settings(_env_file=None)
        self.base_url = self.settings.scrapling_service_url.rstrip("/")
        self.use_service = self.settings.scrapling_use_service
        self.timeout = 120

    def fetch(
        self,
        url: str,
        stealth: bool = False,
        solve_cloudflare: bool = False,
        impersonate: str = "chrome",
    ) -> ScraplingResponse:
        """Fetch a URL. Use stealth=True for anti-bot protected pages."""
        if self.use_service:
            endpoint = "/stealth-fetch" if (stealth or solve_cloudflare) else "/fetch"
            body: dict[str, Any] = {"url": url}
            if endpoint == "/stealth-fetch":
                body["headless"] = True
                body["solve_cloudflare"] = solve_cloudflare
                body["network_idle"] = True
            else:
                body["impersonate"] = impersonate
            return self._post(endpoint, body, timeout=self.timeout)

        return self._fallback_get(url, stealth=stealth)

    def run_spider(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Run the generic job-discovery spider and return the scraped items."""
        if not self.use_service:
            logger.warning("Scrapling service is disabled; spider run returns empty list")
            return []
        result = self._post("/spider/run", payload, timeout=300)
        try:
            data = result.json()
        except Exception as exc:
            logger.warning(f"Spider response was not JSON: {exc}")
            return []
        return data.get("items", []) if isinstance(data, dict) else []

    def select(
        self,
        html: str,
        selectors: dict[str, str],
        url: Optional[str] = None,
    ) -> dict[str, Any]:
        """Run adaptive CSS/XPath selectors against an HTML string."""
        if not self.use_service:
            logger.warning("Scrapling service is disabled; select() returns empty results")
            return {}
        result = self._post("/select", {"html": html, "selectors": selectors, "url": url}, timeout=60)
        try:
            data = result.json()
        except Exception as exc:
            logger.warning(f"Selector response was not JSON: {exc}")
            return {}
        return data.get("results", {}) if isinstance(data, dict) else {}

    def stealth_submit_snapshot(self, url: str, proxy: Optional[dict[str, Any]] = None) -> ScraplingResponse:
        """Return a stealth-rendered snapshot of a submission/ATS page."""
        if self.use_service:
            return self._post("/submit/cloudflare", {"url": url, "proxy": proxy}, timeout=self.timeout)
        return self._fallback_get(url, stealth=True)

    def send_extension_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send a Chrome-extension DOM snapshot to the service."""
        if not self.use_service:
            # Fallback: save locally if the main app calls this without the service.
            logger.warning("Scrapling service is disabled; extension snapshot stored locally")
            return self._local_snapshot(payload)
        result = self._post("/extension/snapshot", payload, timeout=60)
        try:
            return result.json()
        except Exception as exc:
            logger.warning(f"Snapshot response was not JSON: {exc}")
            return {}

    def _post(self, endpoint: str, body: dict[str, Any], timeout: int) -> ScraplingResponse:
        url = f"{self.base_url}{endpoint}"
        try:
            resp = requests.post(url, json=body, timeout=timeout)
        except requests.RequestException as exc:
            raise ScraplingServiceError(f"Scrapling service unreachable at {url}: {exc}") from exc

        try:
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise ScraplingServiceError(
                f"Scrapling service error {resp.status_code}: {resp.text[:500]}"
            ) from exc

        cookies = self._extract_cookies(resp)
        return ScraplingResponse(
            url=resp.url,
            status=resp.status_code,
            text=resp.text,
            html=resp.text,
            headers=dict(resp.headers),
            cookies=cookies,
            _raw_response=resp,
        )

    def _fallback_get(self, url: str, stealth: bool = False) -> ScraplingResponse:
        """Plain requests fallback used when the Scrapling service is disabled."""
        headers = {"User-Agent": self.DEFAULT_UA}
        if stealth:
            headers["Accept-Language"] = "en-US,en;q=0.5"
            headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            headers["DNT"] = "1"
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            cookies = self._extract_cookies(resp)
            return ScraplingResponse(
                url=resp.url,
                status=resp.status_code,
                text=resp.text,
                html=resp.text,
                headers=dict(resp.headers),
                cookies=cookies,
                _raw_response=resp,
            )
        except Exception as exc:
            raise ScraplingServiceError(f"Fallback fetch failed for {url}: {exc}") from exc

    @staticmethod
    def _extract_cookies(resp) -> dict:
        try:
            return resp.cookies.get_dict()
        except AttributeError:
            try:
                return dict(resp.cookies)
            except Exception:
                return {}

    def _local_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Store an extension snapshot locally when the service is unavailable."""
        from datetime import datetime, timezone
        from pathlib import Path

        drafts_dir = self.settings.adapter_drafts_dir
        drafts_dir.mkdir(parents=True, exist_ok=True)
        domain = urlparse(payload.get("url", "")).netloc or "unknown"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = drafts_dir / f"{domain}_{timestamp}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return {"snapshot_path": str(path), "platform": domain}


# Singleton client for convenience.
_client: Optional[ScraplingClient] = None


def get_scrapling_client(settings: Optional[Settings] = None) -> ScraplingClient:
    global _client
    if _client is None or settings is not None:
        _client = ScraplingClient(settings)
    return _client
