"""Shared utilities for agents: retries, delays, screenshots."""
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import Page
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from job_agent.config import Settings


class BaseAgent:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _screenshot_path(self, prefix: str) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{prefix}_{timestamp}.png"
        path = self.settings.screenshot_dir / filename
        self.settings.screenshot_dir.mkdir(parents=True, exist_ok=True)
        return path

    async def _save_screenshot(self, page: Page | None, prefix: str) -> Path | None:
        if page is None:
            return None
        try:
            path = self._screenshot_path(prefix)
            await page.screenshot(path=str(path), full_page=True)
            logger.info(f"Screenshot saved: {path}")
            return path
        except Exception as exc:
            logger.warning(f"Could not save screenshot: {exc}")
            return None

    def _profile_dict(self) -> dict[str, str]:
        return {
            "my_name": self.settings.my_name,
            "my_email": self.settings.my_email,
            "my_phone": self.settings.my_phone,
            "my_linkedin": self.settings.my_linkedin,
        }


def with_retries(max_attempts: int, retry_delay: int):
    """Decorator factory for retrying transient operations."""
    return retry(
        reraise=True,
        stop=stop_after_attempt(max(1, max_attempts)),
        wait=wait_exponential(multiplier=retry_delay, min=retry_delay, max=retry_delay * 10),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    )
