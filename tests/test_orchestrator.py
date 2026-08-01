"""Tests for the orchestrator's core logic."""
from pathlib import Path

import pytest

from job_agent.agents.orchestrator import Orchestrator
from job_agent.models import ApplicationStatus, JobApplication
from job_agent.persistence.excel_logger import ExcelLogger
from job_agent.persistence.sqlite_queue import SQLiteQueue


def _make_resume_pdf(path: Path, text: str) -> None:
    """Create a minimal valid PDF for testing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "%PDF-1.4\n"
        "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        "2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>\nendobj\n"
        "4 0 obj\n<< /Length 44 >>\nstream\nBT /F1 12 Tf 100 700 Td ({} ) Tj ET\nendstream\nendobj\n"
        "xref\n0 5\n0000000000 65535 f\n0000000009 00000 n\n0000000058 00000 n\n0000000115 00000 n\n0000000214 00000 n\n"
        "trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n310\n%%EOF\n"
    ).format(text)
    path.write_text(content, encoding="latin-1")


def test_resume_matching_and_validation(temp_settings):
    orchestrator = Orchestrator(temp_settings)
    resume_path = temp_settings.resume_dir / "JD_Senior Software Engineer_TestUser_20240101.pdf"
    _make_resume_pdf(resume_path, "Senior Software Engineer")

    job = JobApplication(title="Senior Software Engineer", company="X", url="https://example.com/jobs/1")
    resume = orchestrator._find_best_resume(job)
    assert resume is not None
    assert resume.path == resume_path


@pytest.mark.asyncio
async def test_duplicate_job_is_skipped(temp_settings, sample_job):
    orchestrator = Orchestrator(temp_settings)
    # Pre-populate the queue as if already applied.
    sample_job.status = ApplicationStatus.SUBMITTED
    orchestrator.queue.add_or_update(sample_job)

    duplicate = JobApplication(
        title="Senior Software Engineer",
        company="Example Corp",
        url="https://boards.greenhouse.io/example/jobs/99999",
        location="Remote",
    )
    result = await orchestrator.process_job(duplicate)
    assert result.status == ApplicationStatus.DUPLICATE


@pytest.mark.asyncio
async def test_missing_resume_fails_gracefully(temp_settings):
    orchestrator = Orchestrator(temp_settings)
    job = JobApplication(title="Staff Engineer", company="X", url="https://example.com/jobs/2")
    result = await orchestrator.process_job(job)
    assert result.status == ApplicationStatus.FAILED
    assert "No valid resume" in result.error_message


@pytest.mark.asyncio
async def test_persist_round_trip(temp_settings, sample_job):
    orchestrator = Orchestrator(temp_settings)
    await orchestrator.process_job(sample_job)

    assert orchestrator.queue.get(sample_job.id) is not None
    assert orchestrator.excel.get_application_by_id(sample_job.id) is not None


@pytest.mark.asyncio
async def test_pipeline_end_to_end_with_mocked_submission(temp_settings):
    from unittest.mock import AsyncMock, patch

    from job_agent.agents.submission_agent import ApplicationSubmissionAgent, SubmissionResult

    orchestrator = Orchestrator(temp_settings)
    job = JobApplication(
        title="Applied AI Engineer",
        company="Gradial",
        url="https://boards.greenhouse.io/gradial/jobs/4338065009",
        location="Remote",
    )
    resume_path = temp_settings.resume_dir / "JD_Applied AI Engineer_Test_20240101.pdf"
    _make_resume_pdf(resume_path, "Applied AI Engineer")

    with patch.object(
        ApplicationSubmissionAgent,
        "apply_with_retry",
        new=AsyncMock(return_value=SubmissionResult(ApplicationStatus.QUEUED, "Dry-run succeeded")),
    ):
        results = await orchestrator.run([job])

    assert len(results) == 1
    assert results[0].status == ApplicationStatus.QUEUED

    # Verify persistence.
    queued = orchestrator.queue.list_by_status(ApplicationStatus.QUEUED)
    assert any(q.id == job.id for q in queued)
    logged = orchestrator.excel.get_application_by_id(job.id)
    assert logged is not None
    assert logged.status == ApplicationStatus.QUEUED


@pytest.mark.asyncio
async def test_orchestrator_run_with_no_jobs(temp_settings):
    orchestrator = Orchestrator(temp_settings)
    results = await orchestrator.run()
    assert results == []
