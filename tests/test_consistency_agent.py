"""Tests for the ConsistencyAgent contradiction checks."""
import pytest

from job_agent.agents.consistency_agent import ConsistencyAgent


@pytest.fixture
def agent():
    return ConsistencyAgent()


@pytest.fixture
def profile():
    return {
        "personal_info": {"work_authorization": "US Citizen"},
        "skills": ["Python", "SQL"],
        "experience_highlights": ["Built dashboards for 5+ years", "Designed ETL pipelines"],
    }


@pytest.fixture
def job():
    return {"title": "Data Analyst", "company": "Example Corp"}


def test_us_worker_sponsorship_yes_is_flagged(agent, profile, job):
    audit = [
        {
            "label": "Do you require visa sponsorship?",
            "field_type": "select",
            "required": True,
            "visible": True,
            "answer_source": "llm",
            "value": "Yes",
            "disposition": "filled",
            "browser_verified": True,
        }
    ]
    issues = agent.check(profile, job, audit)
    assert any("require visa sponsorship" in issue for issue in issues)


def test_us_worker_authorized_no_is_flagged(agent, profile, job):
    audit = [
        {
            "label": "Are you legally authorized to work in the US?",
            "field_type": "select",
            "required": True,
            "visible": True,
            "answer_source": "profile",
            "value": "No",
            "disposition": "filled",
            "browser_verified": True,
        }
    ]
    issues = agent.check(profile, job, audit)
    assert any("legally authorized" in issue or "authorized to work" in issue for issue in issues)


def test_experience_years_exceed_profile_is_flagged(agent, profile, job):
    audit = [
        {
            "label": "How many years of data experience do you have?",
            "field_type": "number",
            "required": True,
            "visible": True,
            "answer_source": "llm",
            "value": "7",
            "disposition": "filled",
            "browser_verified": True,
        }
    ]
    issues = agent.check(profile, job, audit)
    assert any("7 years" in issue and "5 years" in issue for issue in issues)


def test_skills_mismatch_is_flagged(agent, profile, job):
    audit = [
        {
            "label": "Which skills do you have?",
            "field_type": "text",
            "required": True,
            "visible": True,
            "answer_source": "llm",
            "value": "Java",
            "disposition": "filled",
            "browser_verified": True,
        }
    ]
    issues = agent.check(profile, job, audit)
    assert any("Java" in issue for issue in issues)


def test_required_field_skipped_is_flagged(agent, profile, job):
    audit = [
        {
            "label": "Why this role?",
            "field_type": "textarea",
            "required": True,
            "visible": True,
            "answer_source": "needs_human",
            "value": None,
            "disposition": "needs_human",
            "browser_verified": False,
        }
    ]
    issues = agent.check(profile, job, audit)
    assert any("Why this role?" in issue for issue in issues)


def test_hidden_field_failure_is_ignored(agent, profile, job):
    audit = [
        {
            "label": "",
            "field_type": "hidden",
            "required": True,
            "visible": False,
            "answer_source": "needs_human",
            "value": None,
            "disposition": "needs_human",
            "browser_verified": False,
        }
    ]
    issues = agent.check(profile, job, audit)
    assert issues == []
