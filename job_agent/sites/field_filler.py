"""Robust field filling helpers that try multiple locator strategies.

ATS UIs change IDs, names, and ARIA attributes frequently. These helpers fall
back through several strategies per field so a single selector change does not
cause a false failure.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import Page


class RobustFieldFiller:
    """Fill form fields using multiple locator strategies."""

    def __init__(self, page: Page):
        self.page = page

    async def fill(
        self,
        value: str,
        *,
        field_id: str | None = None,
        name: str | None = None,
        label: str | None = None,
        aria_label: str | None = None,
        placeholder: str | None = None,
        selectors: list[str] | None = None,
        timeout: int = 5000,
    ) -> bool:
        """Try to fill an input/textarea with one of several locator strategies.

        Returns ``True`` if a field was filled.
        """
        if not value:
            return False

        strategies: list[tuple[str, Any]] = []

        # Explicit Playwright CSS selectors (adapter-specific fallbacks).
        if selectors:
            for selector in selectors:
                strategies.append(("selector", self.page.locator(selector).first))

        if field_id:
            strategies.append(("id", self.page.locator(f"#{self._css_escape(field_id)}").first))

        if name:
            strategies.append(
                (
                    "name",
                    self.page.locator(
                        f'input[name="{self._css_escape(name)}"], '
                        f'select[name="{self._css_escape(name)}"], '
                        f'textarea[name="{self._css_escape(name)}"]'
                    ).first,
                )
            )

        if aria_label:
            strategies.append(("aria-label", self.page.get_by_label(aria_label, exact=False).first))

        if label:
            strategies.append(("label", self.page.get_by_label(label, exact=False).first))

        if placeholder:
            strategies.append(
                ("placeholder", self.page.get_by_placeholder(placeholder, exact=False).first)
            )

        for strategy_name, locator in strategies:
            try:
                if await locator.count() == 0:
                    continue
                if not await locator.is_visible(timeout=2000):
                    continue
                tag = await locator.evaluate("el => el.tagName.toLowerCase()")
                if tag == "select":
                    await locator.select_option(label=value, timeout=timeout)
                else:
                    await locator.fill(value, timeout=timeout)
                logger.debug(f"Filled field via {strategy_name}")
                return True
            except Exception as exc:
                logger.debug(f"Could not fill field via {strategy_name}: {exc}")
                continue

        return False

    async def upload(
        self,
        file_path: Path,
        *,
        field_id: str | None = None,
        name: str | None = None,
        label: str | None = None,
        aria_label: str | None = None,
        selectors: list[str] | None = None,
        timeout: int = 5000,
    ) -> bool:
        """Try to upload a file to a file input using multiple locator strategies."""
        if not file_path.exists():
            logger.warning(f"Upload file not found: {file_path}")
            return False

        strategies: list[tuple[str, Any]] = []
        if selectors:
            for selector in selectors:
                strategies.append(("selector", self.page.locator(selector).first))
        if field_id:
            strategies.append(("id", self.page.locator(f"#{self._css_escape(field_id)}").first))
        if name:
            strategies.append(
                ("name", self.page.locator(f'input[type="file"][name="{self._css_escape(name)}"]').first)
            )
        if aria_label:
            strategies.append(("aria-label", self.page.get_by_label(aria_label, exact=False).locator("input[type=\"file\"]").first))
        if label:
            strategies.append(("label", self.page.get_by_label(label, exact=False).locator("input[type=\"file\"]").first))

        # Generic last-resort file input.
        strategies.append(("any file input", self.page.locator('input[type="file"]').first))

        for strategy_name, locator in strategies:
            try:
                if await locator.count() == 0:
                    continue
                await locator.set_input_files(str(file_path.resolve()), timeout=timeout)
                logger.debug(f"Uploaded file via {strategy_name}")
                return True
            except Exception as exc:
                logger.debug(f"Could not upload file via {strategy_name}: {exc}")
                continue

        return False

    @staticmethod
    def _css_escape(value: str) -> str:
        """Minimal escaping for CSS identifiers/strings."""
        return value.replace("\\", "\\\\").replace('"', '\\"')
