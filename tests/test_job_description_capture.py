"""Tests for the job-description archive helper."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from job_agent.agents.job_description_capture import JobDescriptionCapture
from job_agent.config import Settings
from job_agent.models import JobApplication


@pytest.fixture
def capture(tmp_path):
    settings = Settings(
        job_descriptions_dir=tmp_path / "job_descriptions",
        _env_file=None,
    )
    return JobDescriptionCapture(settings)


def test_capture_uses_job_description_when_no_fetch(capture, tmp_path):
    job = JobApplication(
        title="Data Analyst",
        company="Test Corp",
        url="https://example.com/job",
        location="Remote",
        description="Looking for SQL and Python skills.",
        requirements="2+ years of experience.",
    )
    resume_pdf = tmp_path / "JD_Data_Analyst_Lokesh_20260801.pdf"
    resume_pdf.write_text("fake pdf")

    with patch.object(capture, "_fetch_url", return_value=None):
        md_path, html_path = capture.capture(job, resume_pdf)

    assert md_path is not None
    assert html_path is not None
    assert md_path.exists()
    assert html_path.exists()
    assert md_path.name == "JD_Data_Analyst_Lokesh_20260801_jd.md"
    assert html_path.name == "JD_Data_Analyst_Lokesh_20260801_jd.html"

    md_text = md_path.read_text(encoding="utf-8")
    assert "Data Analyst" in md_text
    assert "Test Corp" in md_text
    assert "Looking for SQL and Python skills." in md_text
    assert "2+ years of experience." in md_text
    assert str(resume_pdf) in md_text

    html_text = html_path.read_text(encoding="utf-8")
    assert "Data Analyst" in html_text
    assert "https://example.com/job" in html_text

    assert job.jd_path == md_path
    assert job.jd_html_path == html_path


def test_capture_fetches_html_when_description_missing(capture, tmp_path):
    job = JobApplication(
        title="Data Scientist",
        company="Fetch Corp",
        url="https://example.com/job2",
    )
    resume_pdf = tmp_path / "JD_Data_Scientist_Lokesh_20260801.pdf"
    resume_pdf.write_text("fake pdf")

    fake_html = """
    <html><body>
      <h1>Data Scientist</h1>
      <p>Machine learning and Python required.</p>
      <script>alert('noise')</script>
    </body></html>
    """

    with patch.object(capture, "_fetch_url", return_value=fake_html):
        md_path, html_path = capture.capture(job, resume_pdf)

    md_text = md_path.read_text(encoding="utf-8")
    assert "Machine learning and Python required." in md_text
    assert "alert('noise')" not in md_text

    html_text = html_path.read_text(encoding="utf-8")
    assert fake_html.strip() in html_text or "Machine learning" in html_text


def test_capture_falls_back_to_wrapper_when_fetch_fails(capture, tmp_path):
    job = JobApplication(
        title="Analyst",
        company="NoFetch Corp",
        url="https://example.com/job3",
    )
    resume_pdf = tmp_path / "JD_Analyst_Lokesh_20260801.pdf"
    resume_pdf.write_text("fake pdf")

    with patch("job_agent.agents.job_description_capture.requests.get") as mock_get:
        mock_get.side_effect = Exception("network down")
        md_path, html_path = capture.capture(job, resume_pdf)

    assert md_path.exists()
    assert html_path.exists()
    md_text = md_path.read_text(encoding="utf-8")
    assert "Job description not available." in md_text
