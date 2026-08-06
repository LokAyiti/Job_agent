"""Tests for the custom question answering agent."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


def test_cache_hit_returns_cached_answer(agent, profile, job):
    field = {
        "label": "Why do you want this role?",
        "field_type": "textarea",
        "required": True,
        "visible": True,
    }
    cache_key = agent._cache_key(field)
    agent._set_cached_answer(cache_key, "I love data analysis.")

    with patch.object(agent, "_call_llm") as mock_llm:
        answer = agent.answer_for_field(field, job, profile)
    assert answer == "I love data analysis."
    mock_llm.assert_not_called()


def test_generate_answer_caches_and_returns_llm_output(agent, profile, job):
    field = {
        "label": "What excites you about data?",
        "field_type": "textarea",
        "required": True,
        "visible": True,
    }

    with patch.object(agent, "_call_llm", return_value="Turning data into decisions."):
        answer = agent.answer_for_field(field, job, profile)
    assert answer == "Turning data into decisions."
    assert agent._get_cached_answer(agent._cache_key(field)) == "Turning data into decisions."


def test_work_authorization_question_uses_profile(agent, profile, job):
    field = {
        "label": "Are you legally authorized to work in the US?",
        "field_type": "select",
        "required": True,
        "visible": True,
        "options_sample": [{"value": "yes", "text": "Yes"}, {"value": "no", "text": "No"}],
    }
    answer = agent.answer_for_field(field, job, profile)
    assert answer == "Yes"


def test_protected_question_with_decline_option(agent, profile, job):
    field = {
        "label": "What is your gender?",
        "field_type": "select",
        "required": True,
        "visible": True,
        "options_sample": [
            {"value": "m", "text": "Male"},
            {"value": "f", "text": "Female"},
            {"value": "d", "text": "Decline to self-identify"},
        ],
    }
    answer = agent.answer_for_field(field, job, profile)
    assert answer == "Decline to self-identify"


def test_protected_question_without_decline_option_is_skipped(agent, profile, job):
    field = {
        "label": "What is your race?",
        "field_type": "select",
        "required": True,
        "visible": True,
        "options_sample": [{"value": "a", "text": "Asian"}, {"value": "b", "text": "Black"}],
    }
    answer = agent.answer_for_field(field, job, profile)
    assert answer is None


def test_numeric_or_date_question_is_skipped(agent, profile, job):
    field = {"label": "Years of experience", "field_type": "number", "required": True, "visible": True}
    answer = agent.answer_for_field(field, job, profile)
    assert answer is None


def test_select_option_matching(agent, profile, job):
    field = {
        "label": "Are you willing to relocate?",
        "field_type": "select",
        "required": True,
        "visible": True,
        "options_sample": [
            {"value": "yes", "text": "Yes"},
            {"value": "no", "text": "No"},
        ],
    }

    with patch.object(agent, "_call_llm", return_value="No"):
        answer = agent.answer_for_field(field, job, profile)
    assert answer == "No"


def test_fill_unmapped_fields_fills_visible_required_fields(agent, profile, job):
    page = MagicMock()
    loc = MagicMock()
    loc.count = AsyncMock(return_value=1)
    loc.is_visible = AsyncMock(return_value=True)
    loc.fill = AsyncMock()
    loc.evaluate = AsyncMock(return_value="input")
    loc.first = loc
    page.locator = MagicMock(return_value=loc)
    page.get_by_label = MagicMock(return_value=loc)
    page.get_by_placeholder = MagicMock(return_value=loc)

    form_schema = {
        "unmapped_fields": [
            {
                "label": "Why this role?",
                "field_type": "textarea",
                "required": True,
                "visible": True,
                "id": "why",
            }
        ]
    }

    with patch.object(agent, "_call_llm", return_value="Because I love data."):
        filled = _run(agent.fill_unmapped_fields(page, form_schema, job, profile, dry_run=True))

    assert filled.answers.get("Why this role?") == "Because I love data."
    loc.fill.assert_awaited()


def test_answer_cache_key_is_stable_for_same_question(agent):
    field = {"label": " Why  this role? ", "field_type": "textarea"}
    assert agent._cache_key(field) == agent._cache_key(field)
