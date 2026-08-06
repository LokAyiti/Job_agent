"""Robust field filling helpers that try multiple locator strategies.

ATS UIs change IDs, names, and ARIA attributes frequently. These helpers fall
back through several strategies per field so a single selector change does not
cause a false failure.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import Page, TimeoutError as PWTimeoutError


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
        """Try to fill an input/textarea/select with one of several locator strategies.

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
                meta = await locator.evaluate(
                    """el => ({
                        tag: el.tagName.toLowerCase(),
                        role: el.getAttribute('role') || '',
                        isReactSelect: !!(el.closest('.select__container') || el.closest('.select'))
                    })"""
                )
                if isinstance(meta, dict):
                    tag = meta["tag"]
                    is_react_select = meta["isReactSelect"] or meta["role"] == "combobox"
                else:
                    # Tests may mock evaluate to return the raw tag string.
                    tag = str(meta)
                    is_react_select = False
                if tag == "select":
                    await locator.select_option(label=value, timeout=timeout)
                elif is_react_select:
                    if await self._fill_react_select(locator, value):
                        logger.debug(f"Filled React-select via {strategy_name}")
                        return True
                    continue
                else:
                    await locator.fill(value, timeout=timeout)
                logger.debug(f"Filled field via {strategy_name}")
                return True
            except Exception as exc:
                logger.debug(f"Could not fill field via {strategy_name}: {exc}")
                continue

        return False

    async def _fill_react_select(self, locator: Any, value: str) -> bool:
        """Open a React-select dropdown and click the option matching ``value``.

        ``value`` may be a long sentence. For obvious Yes/No questions we collapse
        it to the single word so the option can be matched.
        """
        try:
            await locator.click(timeout=3000)
            await self.page.wait_for_timeout(600)

            candidate = self._react_select_candidate(value)

            # React-select exposes options as [role="option"] elements once the
            # menu is open. Use Playwright's accessible locator so punctuation in
            # the candidate text does not break a CSS selector.
            option_locators = [
                self.page.get_by_role("option", name=candidate, exact=False).first,
                self.page.locator('[role="option"]').filter(has_text=candidate).first,
            ]
            if " " not in candidate.strip():
                option_locators.insert(
                    0,
                    self.page.get_by_role("option", name=candidate, exact=True).first,
                )

            for option_locator in option_locators:
                try:
                    if await option_locator.count() > 0 and await option_locator.is_visible(timeout=2000):
                        await option_locator.click(timeout=3000)
                        await self.page.wait_for_timeout(400)
                        return True
                except PWTimeoutError:
                    continue

            # Fallback: look for an affirmative option. This handles required
            # acknowledgement dropdowns (e.g. "AI Policy" -> "I agree").
            try:
                all_options = await self.page.locator('[role="option"]').all_inner_texts()
                lowered = [t.strip().lower() for t in all_options if t.strip()]
                affirmative_keywords = ["agree", "yes", "i have read", "i understand", "true", "acknowledge"]
                chosen = None
                for i, text in enumerate(lowered):
                    if any(kw in text for kw in affirmative_keywords):
                        chosen = i
                        break
                if chosen is not None:
                    await self.page.locator('[role="option"]').nth(chosen).click(timeout=3000)
                    await self.page.wait_for_timeout(400)
                    return True
            except Exception as exc:
                logger.debug(f"React-select affirmative fallback failed: {exc}")

            # No matching option found; close the menu and report failure.
            await self.page.keyboard.press("Escape")
            return False
        except Exception as exc:
            logger.debug(f"React-select fill failed: {exc}")
            return False

    @staticmethod
    def _react_select_candidate(value: str) -> str:
        """Collapse common answer sentences into a selectable option."""
        lowered = value.lower()
        # Explicit Yes/No sentences should map to the option, not free text.
        if "yes" in lowered and "no" not in lowered:
            return "Yes"
        if "no" in lowered and "yes" not in lowered:
            return "No"
        # Work-authorization shorthand.
        if any(k in lowered for k in ("us citizen", "permanent resident", "green card", "authorized")):
            return "Yes"
        if any(k in lowered for k in ("need sponsorship", "require visa", "not authorized")):
            return "No"
        return value.strip()

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
