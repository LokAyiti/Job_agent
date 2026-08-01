"""Human-like pacing and stealth utilities for Playwright."""
from __future__ import annotations

import asyncio
import random
from typing import Optional

from loguru import logger
from playwright.async_api import Page


class Humanizer:
    """Add realistic delays and typing behavior to reduce bot detection."""

    def __init__(
        self,
        min_delay: float = 0.15,
        max_delay: float = 0.55,
        typing_delay_min: float = 0.03,
        typing_delay_max: float = 0.12,
    ):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.typing_delay_min = typing_delay_min
        self.typing_delay_max = typing_delay_max

    async def wait(self, multiplier: float = 1.0) -> None:
        """Wait a randomized amount of time."""
        delay = random.uniform(self.min_delay, self.max_delay) * multiplier
        await asyncio.sleep(delay)

    async def wait_between_actions(self) -> None:
        await self.wait(1.0)

    async def wait_before_typing(self) -> None:
        await self.wait(0.5)

    async def type_like_human(self, page: Page, selector: str, text: str) -> None:
        """Type text into a field with variable per-keystroke delays."""
        await page.locator(selector).click()
        await self.wait_before_typing()
        for char in text:
            await page.locator(selector).type(char, delay=random.uniform(self.typing_delay_min, self.typing_delay_max))
        await self.wait_between_actions()

    async def move_mouse_randomly(self, page: Page) -> None:
        """Move the mouse to a random position within the viewport."""
        try:
            viewport = await page.evaluate("() => ({ width: window.innerWidth, height: window.innerHeight })")
            x = random.randint(50, max(51, viewport["width"] - 50))
            y = random.randint(50, max(51, viewport["height"] - 50))
            await page.mouse.move(x, y)
            await self.wait(0.3)
        except Exception as exc:
            logger.debug(f"Mouse move failed: {exc}")

    async def scroll_naturally(self, page: Page, pixels: Optional[int] = None) -> None:
        """Scroll the page in small, human-like increments."""
        if pixels is None:
            pixels = random.randint(200, 800)
        step = random.randint(50, 150)
        direction = 1 if pixels > 0 else -1
        remaining = abs(pixels)
        while remaining > 0:
            scroll = min(step, remaining)
            await page.mouse.wheel(0, scroll * direction)
            await self.wait(0.2)
            remaining -= scroll


class StealthInjector:
    """Inject anti-detection scripts into a Playwright page."""

    # Minimal script to hide the webdriver/automation flag and override common
    # navigator properties that bot detection services check. This is a starting
    # point; for stronger stealth consider playwright-stealth or patchright.
    STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    window.chrome = { runtime: {} };
    Object.defineProperty(window, 'chrome', { get: () => ({ runtime: {} }) });
    """

    async def inject(self, page: Page) -> None:
        try:
            await page.add_init_script(self.STEALTH_SCRIPT)
            logger.debug("Stealth scripts injected into page context")
        except Exception as exc:
            logger.warning(f"Stealth injection failed: {exc}")
