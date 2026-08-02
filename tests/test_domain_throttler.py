"""Tests for the async domain throttler."""
import asyncio
import time

from job_agent.utils.domain_throttler import AsyncDomainThrottler


def _run(coro):
    return asyncio.run(coro)


def test_throttler_enforces_minimum_delay():
    throttler = AsyncDomainThrottler(min_delay=0.2, max_delay=0.2)
    domain = "example.com"

    start = time.time()
    _run(throttler.wait(domain))
    _run(throttler.wait(domain))
    elapsed = time.time() - start

    assert elapsed >= 0.18, "Second request should be delayed"


def test_throttler_does_not_delay_first_request():
    throttler = AsyncDomainThrottler(min_delay=2.0, max_delay=3.0)
    start = time.time()
    _run(throttler.wait("example.com"))
    elapsed = time.time() - start
    assert elapsed < 0.05


def test_different_domains_are_independent():
    throttler = AsyncDomainThrottler(min_delay=0.5, max_delay=0.5)
    start = time.time()
    _run(throttler.wait("a.com"))
    _run(throttler.wait("b.com"))
    elapsed = time.time() - start
    assert elapsed < 0.1
