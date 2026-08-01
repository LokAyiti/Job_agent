"""Tests for the email feedback ledger."""
from pathlib import Path

import pytest

from job_agent.agents.feedback_ledger import FeedbackLedger
from job_agent.models import ApplicationStatus, JobApplication


@pytest.fixture
def sample_job():
    return JobApplication(
        id="abc123",
        title="Data Analyst",
        company="Example Corp",
        url="https://example.com/jobs/1",
        resume_path=Path("resume/JD_Data_Analyst_20260801.pdf"),
    )


def test_feedback_ledger_records_callback(tmp_path, sample_job):
    ledger = FeedbackLedger(tmp_path / "feedback.json")
    entry = ledger.record(
        job=sample_job,
        outcome="callback",
        source_resume="resume/JD_Data_Analyst_20260801.pdf",
        fabrication_tolerance="moderate",
        notes="Recruiter scheduled phone screen",
    )
    assert entry["outcome"] == "callback"
    assert entry["job_id"] == "abc123"
    assert (tmp_path / "feedback.json").exists()


def test_feedback_ledger_records_from_status_update(tmp_path, sample_job):
    ledger = FeedbackLedger(tmp_path / "feedback.json")
    entry = ledger.record_from_status_update(
        job=sample_job,
        new_status=ApplicationStatus.RESPONDED,
        fabrication_tolerance="aggressive",
    )
    assert entry is not None
    assert entry["outcome"] == "callback"
    assert entry["fabrication_tolerance"] == "aggressive"


def test_feedback_ledger_ignores_unknown_status(tmp_path, sample_job):
    ledger = FeedbackLedger(tmp_path / "feedback.json")
    entry = ledger.record_from_status_update(
        job=sample_job,
        new_status=ApplicationStatus.SUBMITTED,
    )
    assert entry is None


def test_feedback_ledger_get_successful_claims(tmp_path):
    ledger = FeedbackLedger(tmp_path / "feedback.json")
    for i in range(3):
        job = JobApplication(
            id=f"job{i}",
            title="Data Analyst" if i < 2 else "Software Engineer",
            company=f"Company {i}",
            url="https://example.com",
        )
        ledger.record(job=job, outcome="callback" if i < 2 else "rejection")

    hints = ledger.get_successful_claims(job_title="Data Analyst", limit=5)
    assert len(hints) == 2
    assert all(h["outcome"] == "callback" for h in hints)


def test_feedback_ledger_historical_outcomes(tmp_path):
    ledger = FeedbackLedger(tmp_path / "feedback.json")
    for outcome in ["callback", "callback", "rejection", "no_response"]:
        job = JobApplication(
            id=f"{outcome}_1",
            title="Data Analyst",
            company="Corp",
            url="https://example.com",
        )
        ledger.record(job=job, outcome=outcome)

    counts = ledger.get_historical_outcomes("Data Analyst")
    assert counts["callback"] == 2
    assert counts["rejection"] == 1
    assert counts["no_response"] == 1


def test_feedback_ledger_invalid_outcome_raises(tmp_path, sample_job):
    ledger = FeedbackLedger(tmp_path / "feedback.json")
    with pytest.raises(ValueError):
        ledger.record(job=sample_job, outcome="unknown")
