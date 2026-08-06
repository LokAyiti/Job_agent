"""Tests for the browser-side FormVerifier."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from job_agent.agents.form_verifier import FormVerifier


@pytest.fixture
def verifier():
    return FormVerifier()


@pytest.fixture
def mock_page():
    page = MagicMock()
    loc = MagicMock()
    loc.first = loc
    loc.count = AsyncMock(return_value=0)
    loc.all_inner_texts = AsyncMock(return_value=[])
    loc.is_disabled = AsyncMock(return_value=False)
    page.locator = MagicMock(return_value=loc)
    page.evaluate = AsyncMock(return_value=None)
    return page


@pytest.mark.asyncio
async def test_required_empty_field_needs_human(verifier, mock_page):
    mock_page.evaluate.return_value = {
        "tag": "input",
        "type": "text",
        "visible": True,
        "value": "",
        "checked": False,
        "disabled": False,
        "readonly": False,
    }
    form_schema = {
        "fields": {},
        "unmapped_fields": [
            {
                "label": "Why this role?",
                "field_type": "text",
                "required": True,
                "visible": True,
                "selector": "#why",
            }
        ],
    }
    result = await verifier.verify(mock_page, form_schema, [])
    assert len(result) == 1
    audit = result[0]
    assert audit["disposition"] == "needs_human"
    assert audit["reason"] == "required_field_empty_in_browser"
    assert audit["browser_verified"] is False


@pytest.mark.asyncio
async def test_filled_field_is_filled(verifier, mock_page):
    mock_page.evaluate.return_value = {
        "tag": "input",
        "type": "text",
        "visible": True,
        "value": "I love data.",
        "checked": False,
        "disabled": False,
        "readonly": False,
    }
    form_schema = {
        "fields": {},
        "unmapped_fields": [
            {
                "label": "Why this role?",
                "field_type": "text",
                "required": True,
                "visible": True,
                "selector": "#why",
            }
        ],
    }
    result = await verifier.verify(mock_page, form_schema, [])
    audit = result[0]
    assert audit["disposition"] == "filled"
    assert audit["browser_verified"] is True


@pytest.mark.asyncio
async def test_validation_errors_detected(verifier, mock_page):
    loc = mock_page.locator.return_value
    loc.all_inner_texts = AsyncMock(return_value=["This field is required", "Invalid email"])
    errors = await verifier.detect_validation_errors(mock_page)
    assert "This field is required" in errors
    assert "Invalid email" in errors


@pytest.mark.asyncio
async def test_submit_not_found_returns_reason(verifier, mock_page):
    loc = mock_page.locator.return_value
    loc.count = AsyncMock(return_value=0)
    form_schema = {
        "fields": {
            "submit": {
                "field_type": "submit",
                "label": "Submit",
                "required": False,
                "visible": True,
                "selector": "#submit",
                "value_source": "_action_",
            }
        },
        "unmapped_fields": [],
    }
    reason = await verifier.check_submit_control(mock_page, form_schema)
    assert reason == "submit_control_not_found"


@pytest.mark.asyncio
async def test_submit_disabled_returns_reason(verifier, mock_page):
    loc = mock_page.locator.return_value
    loc.count = AsyncMock(return_value=1)
    loc.is_disabled = AsyncMock(return_value=True)
    form_schema = {
        "fields": {
            "submit": {
                "field_type": "submit",
                "label": "Submit",
                "required": False,
                "visible": True,
                "selector": "#submit",
                "value_source": "_action_",
            }
        },
        "unmapped_fields": [],
    }
    reason = await verifier.check_submit_control(mock_page, form_schema)
    assert reason == "submit_control_disabled"
