"""Shared pytest fixtures."""
import tempfile
from pathlib import Path

import pytest

from job_agent.config import Settings, get_settings, reload_settings
from job_agent.models import JobApplication


@pytest.fixture
def temp_settings(monkeypatch):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp_path = Path(tmp)
        monkeypatch.setattr(
            "job_agent.config._settings",
            Settings(
                resume_dir=tmp_path / "resume",
                log_file=tmp_path / "logs" / "applications.xlsx",
                sqlite_db=tmp_path / "logs" / "job_queue.db",
                screenshot_dir=tmp_path / "logs" / "screenshots",
                my_name="Test User",
                my_email="test@example.com",
                my_phone="555-0000",
                my_linkedin="https://linkedin.com/in/test",
                login_email="test-login@example.com",
                login_password="TestPass123!",
                enable_auto_submit=False,
            ),
        )
        settings = get_settings()
        settings.ensure_dirs()
        yield settings


@pytest.fixture
def sample_job():
    return JobApplication(
        title="Senior Software Engineer",
        company="Example Corp",
        url="https://boards.greenhouse.io/example/jobs/12345",
        location="Remote",
    )
