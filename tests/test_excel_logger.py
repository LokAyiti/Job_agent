"""Tests for the Excel persistence layer."""
from datetime import datetime

from job_agent.models import ApplicationStatus, JobApplication
from job_agent.persistence.excel_logger import ExcelLogger


def test_create_and_upsert(temp_settings, sample_job):
    logger = ExcelLogger(temp_settings.log_file)
    logger.upsert(sample_job)

    apps = logger.list_applications()
    assert len(apps) == 1
    assert apps[0].title == sample_job.title
    assert apps[0].company == sample_job.company


def test_upsert_updates_existing_row(temp_settings, sample_job):
    logger = ExcelLogger(temp_settings.log_file)
    logger.upsert(sample_job)

    sample_job.status = ApplicationStatus.SUBMITTED
    sample_job.date_applied = datetime.now()
    logger.upsert(sample_job)

    apps = logger.list_applications()
    assert len(apps) == 1
    assert apps[0].status == ApplicationStatus.SUBMITTED


def test_duplicate_detection(temp_settings, sample_job):
    logger = ExcelLogger(temp_settings.log_file)
    sample_job.status = ApplicationStatus.SUBMITTED
    logger.upsert(sample_job)

    duplicate = JobApplication(
        title="Senior Software Engineer",
        company="Example Corp",
        url="https://boards.greenhouse.io/example/jobs/99999",
        location="Remote",
    )
    assert logger.is_duplicate(duplicate)

    different = JobApplication(
        title="Data Scientist",
        company="Other Corp",
        url="https://boards.greenhouse.io/other/jobs/1",
    )
    assert not logger.is_duplicate(different)
