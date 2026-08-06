"""Failure-path and edge-case tests for the QuestionAnsweringAgent + pipeline."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests

from job_agent.agents.question_answering_agent import QuestionAnsweringAgent
from job_agent.config import Settings
from job_agent.models import JobApplication


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def agent(tmp_path):
    settings = Settings(
        answer_cache_file=tmp_path / "answer_cache.json",
        _env_file=None,
    )
    return QuestionAnsweringAgent(settings)


@pytest.fixture
def profile():
    return {
        "personal_info": {
            "name": "Lokesh Ayiti",
            "email": "test@example.com",
            "phone": "555-0000",
            "linkedin": "https://linkedin.com/in/lokesh",
            "work_authorization": "US Citizen",
            "location": "United States",
        },
        "preferences": {"target_roles": ["Data Analyst", "Data Engineer"]},
        "skills": ["SQL", "Python", "Power BI"],
        "experience_highlights": ["Built dashboards", "Designed ETL pipelines"],
    }


@pytest.fixture
def job():
    return JobApplication(
        title="Data Analyst",
        company="Example Corp",
        url="https://example.com/job",
        description="Looking for SQL and Python skills. Need dashboards.",
    )


def test_missing_api_key_and_broken_bridge_returns_empty(agent, profile, job):
    """When both the LLM bridge and direct OpenRouter call are unavailable,
    generate_answer must return an empty string without crashing the pipeline."""
    field = {
        "label": "What makes you a great fit?",
        "field_type": "textarea",
        "required": True,
        "visible": True,
    }
    # Simulate a failed bridge subprocess and a failed direct network call with no API key.
    with (
        patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="")),
        patch.dict("os.environ", {}, clear=True),
        patch.object(requests, "post", side_effect=requests.exceptions.RequestException("down")),
    ):
        answer = agent.generate_answer(field, job, profile)
    assert answer == ""


def test_broken_required_select_with_no_options_does_not_crash(agent, profile, job):
    """A required select dropdown with zero options should be skipped gracefully."""
    field = {
        "label": "Broken required field",
        "field_type": "select",
        "required": True,
        "visible": True,
        "options_sample": [],
    }
    answer = agent.answer_for_field(field, job, profile)
    # With no options and no LLM answer, the agent cannot match anything.
    assert answer is None


def test_checkbox_group_multiple_selections(agent, profile, job):
    """For checkbox groups that allow multiple selections, the agent should
    return all matching option texts."""
    field = {
        "label": "Which skills do you have? (Select all that apply)",
        "field_type": "checkbox",
        "required": True,
        "visible": True,
        "options_sample": [
            {"value": "py", "text": "Python"},
            {"value": "sql", "text": "SQL"},
            {"value": "js", "text": "JavaScript"},
        ],
        "multiple": True,
    }
    with patch.object(agent, "generate_answer", return_value="Python, SQL"):
        answer = agent.answer_for_field(field, job, profile)
    assert answer == ["Python", "SQL"]


def test_checkbox_single_required_selection(agent, profile, job):
    """For a single-required checkbox, the agent should return one matching option."""
    field = {
        "label": "Are you willing to relocate?",
        "field_type": "checkbox",
        "required": True,
        "visible": True,
        "options_sample": [
            {"value": "yes", "text": "Yes"},
            {"value": "no", "text": "No"},
        ],
    }
    with patch.object(agent, "generate_answer", return_value="No"):
        answer = agent.answer_for_field(field, job, profile)
    assert answer == ["No"]


def test_fill_checkbox_group_selects_multiple_options(agent, profile, job):
    """fill_unmapped_fields should select multiple checkboxes when the answer is a list."""
    page = MagicMock()
    loc = MagicMock()
    loc.count = AsyncMock(return_value=1)
    loc.is_visible = AsyncMock(return_value=True)
    loc.check = AsyncMock()
    loc.first = loc
    page.locator = MagicMock(return_value=loc)

    form_schema = {
        "unmapped_fields": [
            {
                "label": "Skills",
                "field_type": "checkbox",
                "required": True,
                "visible": True,
                "id": "skills",
                "selector": "#skills",
                "options_sample": [
                    {"value": "py", "text": "Python"},
                    {"value": "sql", "text": "SQL"},
                ],
                "multiple": True,
            }
        ]
    }

    with patch.object(agent, "generate_answer", return_value="Python, SQL"):
        filled = _run(agent.fill_unmapped_fields(page, form_schema, job, profile, dry_run=True))

    assert filled.answers.get("Skills") == ["Python", "SQL"]


@patch("job_agent.agents.question_answering_agent.logger")
def test_protected_question_without_decline_is_logged_and_skipped(mock_logger, agent, profile, job):
    """A protected question with no decline option must be skipped and logged."""
    field = {
        "label": "How would you describe your gender identity?",
        "field_type": "select",
        "required": True,
        "visible": True,
        "options_sample": [
            {"value": "m", "text": "Male"},
            {"value": "f", "text": "Female"},
        ],
    }
    answer = agent.answer_for_field(field, job, profile)
    assert answer is None
    assert any("Skipping protected question" in str(call) for call in mock_logger.warning.call_args_list)


def test_required_protected_no_decline_needs_human(agent, profile, job):
    """A required protected question with no decline option must flag needs_human."""
    page = MagicMock()
    form_schema = {
        "unmapped_fields": [
            {
                "label": "How would you describe your gender identity?",
                "field_type": "select",
                "required": True,
                "visible": True,
                "selector": "#gender",
                "options_sample": [
                    {"value": "m", "text": "Male"},
                    {"value": "f", "text": "Female"},
                ],
            }
        ]
    }
    result = _run(agent.fill_unmapped_fields(page, form_schema, job, profile, dry_run=True))
    assert result.needs_human
    assert result.required_protected_no_decline
    audit = result.audit[0]
    assert audit.disposition == "needs_human"
    assert audit.answer_source == "needs_human"
    assert audit.reason == "protected_required_question_no_decline_option"


def test_required_protected_with_decline_selects_decline(agent, profile, job):
    """A required protected question with a decline option should select it."""
    page = MagicMock()
    form_schema = {
        "unmapped_fields": [
            {
                "label": "What is your gender?",
                "field_type": "select",
                "required": True,
                "visible": True,
                "selector": "#gender",
                "options_sample": [
                    {"value": "m", "text": "Male"},
                    {"value": "f", "text": "Female"},
                    {"value": "d", "text": "Decline to self-identify"},
                ],
            }
        ]
    }
    result = _run(agent.fill_unmapped_fields(page, form_schema, job, profile, dry_run=True))
    assert not result.needs_human
    audit = result.audit[0]
    assert audit.disposition == "filled"
    assert audit.answer_source == "decline_option"
    assert audit.value == "Decline to self-identify"
    assert result.answers.get("What is your gender?") == "Decline to self-identify"


def test_required_text_question_with_failed_llm_needs_human(agent, profile, job):
    """A required text question with no cache and a failed LLM must flag needs_human."""
    page = MagicMock()
    form_schema = {
        "unmapped_fields": [
            {
                "label": "Why this role?",
                "field_type": "textarea",
                "required": True,
                "visible": True,
                "selector": "#why",
            }
        ]
    }
    with patch.object(agent, "generate_answer", return_value=""):
        result = _run(agent.fill_unmapped_fields(page, form_schema, job, profile, dry_run=True))
    assert result.needs_human
    audit = result.audit[0]
    assert audit.disposition == "needs_human"
    assert audit.answer_source == "needs_human"
    assert audit.reason == "llm_unavailable_and_cache_missing"


def test_unidentifiable_required_field_needs_human(agent, profile, job):
    """A required field with no label/name/id and a generic selector must flag needs_human."""
    page = MagicMock()
    form_schema = {
        "unmapped_fields": [
            {
                "label": "",
                "field_type": "text",
                "required": True,
                "visible": True,
                "name": "",
                "id": "",
                "selector": "input",
            }
        ]
    }
    result = _run(agent.fill_unmapped_fields(page, form_schema, job, profile, dry_run=True))
    assert result.needs_human
    assert result.unidentifiable_required
    audit = result.audit[0]
    assert audit.disposition == "needs_human"
    assert audit.answer_source == "unidentifiable"
    assert audit.reason == "unidentifiable_required_field"


def test_required_numeric_question_needs_human(agent, profile, job):
    """A required numeric question must be escalated to a human."""
    page = MagicMock()
    form_schema = {
        "unmapped_fields": [
            {
                "label": "Years of experience",
                "field_type": "number",
                "required": True,
                "visible": True,
                "selector": "#years",
            }
        ]
    }
    result = _run(agent.fill_unmapped_fields(page, form_schema, job, profile, dry_run=True))
    assert result.needs_human
    assert result.required_numeric_date
    audit = result.audit[0]
    assert audit.disposition == "needs_human"
    assert audit.answer_source == "needs_human"
    assert audit.reason == "required_numeric_date_question_not_answered"

