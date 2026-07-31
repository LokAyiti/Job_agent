"""Tests for the SQLite-backed job queue."""
from job_agent.models import ApplicationStatus, JobApplication
from job_agent.persistence.sqlite_queue import SQLiteQueue


def test_add_and_get(temp_settings, sample_job):
    queue = SQLiteQueue(temp_settings.sqlite_db)
    queue.add_or_update(sample_job)

    fetched = queue.get(sample_job.id)
    assert fetched is not None
    assert fetched.title == sample_job.title


def test_update_status_and_retry_count(temp_settings, sample_job):
    queue = SQLiteQueue(temp_settings.sqlite_db)
    queue.add_or_update(sample_job)

    queue.update_status(sample_job.id, ApplicationStatus.IN_PROGRESS, increment_retry=True)
    queue.update_status(sample_job.id, ApplicationStatus.FAILED, error_message="boom", increment_retry=True)

    fetched = queue.get(sample_job.id)
    assert fetched.status == ApplicationStatus.FAILED
    assert fetched.error_message == "boom"
    assert fetched.retry_count == 2


def test_duplicate_key(temp_settings, sample_job):
    queue = SQLiteQueue(temp_settings.sqlite_db)
    sample_job.status = ApplicationStatus.SUBMITTED
    queue.add_or_update(sample_job)

    duplicate = JobApplication(
        title="Senior Software Engineer",
        company="Example Corp",
        url="https://boards.greenhouse.io/example/jobs/99999",
        location="Remote",
    )
    assert queue.is_duplicate(duplicate)

    different = JobApplication(
        title="Data Scientist",
        company="Other Corp",
        url="https://boards.greenhouse.io/other/jobs/1",
    )
    assert not queue.is_duplicate(different)
