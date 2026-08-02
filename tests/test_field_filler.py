"""Tests for the robust field filler helper."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from job_agent.sites.field_filler import RobustFieldFiller


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def page():
    """Return a Playwright Page mock where locators are AsyncMock-based."""

    def make_locator(count=0, visible=True, tag="input"):
        loc = MagicMock()
        loc.count = AsyncMock(return_value=count)
        loc.is_visible = AsyncMock(return_value=visible)
        loc.fill = AsyncMock()
        loc.select_option = AsyncMock()
        loc.set_input_files = AsyncMock()
        loc.evaluate = AsyncMock(return_value=tag)
        # Playwright's .first returns a narrowed locator; simulate it returning self.
        loc.first = loc
        return loc

    page = MagicMock()
    page.make_locator = make_locator
    page.locator = MagicMock(side_effect=lambda s: make_locator(count=0))
    page.get_by_label = MagicMock(side_effect=lambda *a, **k: make_locator(count=0))
    page.get_by_placeholder = MagicMock(side_effect=lambda *a, **k: make_locator(count=0))
    return page


def test_fill_tries_strategies_until_success(page):
    success_locator = page.make_locator(count=1, visible=True, tag="input")

    def side_effect(selector: str):
        # Match the id-based selector created from field_id="firstName".
        if "firstName" in selector:
            return success_locator
        return page.make_locator(count=0)

    page.locator = MagicMock(side_effect=side_effect)

    filler = RobustFieldFiller(page)
    result = _run(filler.fill("Lokesh", field_id="firstName", label="First Name"))
    assert result is True
    success_locator.fill.assert_awaited_once_with("Lokesh", timeout=5000)


def test_fill_returns_false_when_no_field_found(page):
    filler = RobustFieldFiller(page)
    result = _run(filler.fill("Lokesh", field_id="missing"))
    assert result is False


def test_upload_tries_strategies_until_success(page, tmp_path):
    file_path = tmp_path / "resume.pdf"
    file_path.write_text("PDF")

    success_locator = page.make_locator(count=1, tag="input")

    def side_effect(selector: str):
        if "resume" in selector or "data-automation-id=\"resume\"" in selector:
            return success_locator
        return page.make_locator(count=0)

    page.locator = MagicMock(side_effect=side_effect)

    filler = RobustFieldFiller(page)
    result = _run(filler.upload(file_path, field_id="resume"))
    assert result is True
    success_locator.set_input_files.assert_awaited_once_with(str(file_path.resolve()), timeout=5000)
