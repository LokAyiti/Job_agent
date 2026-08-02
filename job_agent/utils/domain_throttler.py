"""Async domain request throttling.

Enforces a minimum delay between requests to the same domain to avoid being
rate-limited or flagged by ATS/ careers sites.
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any

from loguru import logger


class AsyncDomainThrottler:
    """Enforce a random minimum delay between requests to the same domain."""

    def __init__(self, min_delay: float = 2.0, max_delay: float = 3.0):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._last_request: dict[str, float] = {}

    async def wait(self, domain: str, context: Any = None) -> None:
        """Sleep if necessary to respect the per-domain delay budget.

        ``context`` is accepted for future extension (e.g. logging tags) but is
        currently unused.
        """
        _ = context
        now = time.time()
        last = self._last_request.get(domain)
        if last is not None:
            elapsed = now - last
            target = random.uniform(self.min_delay, self.max_delay)
            if elapsed < target:
                sleep_for = target - elapsed
                logger.debug(f"Throttling {domain}: sleeping {sleep_for:.2f}s")
                await asyncio.sleep(sleep_for)
                now = time.time()
        self._last_request[domain] = now
