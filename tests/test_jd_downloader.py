"""Tests for the job-description downloader."""
from pathlib import Path

import pytest

from job_application_system.agents.jd_downloader import JDDownloader
from job_application_system.models.job_models import JobListing


def test_jd_downloader_saves_text_and_html(tmp_path):
    """JDDownloader should save description text and, when possible, HTML."""
    output_dir = tmp_path / "jds"
    downloader = JDDownloader(output_dir)

    job = JobListing(
        job_id="12345",
        title="Data Analyst",
        company="State of Texas",
        location="Austin, TX",
        description="Analyze public health data.",
        requirements="SQL, Python, 2 years experience.",
        application_url="https://example.com/job/12345",
    )

    text_path, html_path = downloader.save(job, "JD_Data_Analyst_Lokesh_12345_20260804")

    assert text_path is not None
    assert text_path.exists()
    assert text_path.suffix == ".txt"
    content = text_path.read_text(encoding="utf-8")
    assert "Analyze public health data." in content
    assert "SQL, Python, 2 years experience." in content
    assert "State of Texas" in content

    # HTML fetch will fail for example.com; ensure it returns None gracefully.
    assert html_path is None


def test_jd_downloader_skips_invalid_url(tmp_path):
    """JDDownloader should skip HTML fetch when the application URL is invalid."""
    output_dir = tmp_path / "jds"
    downloader = JDDownloader(output_dir)

    job = JobListing(
        job_id="99999",
        title="Tester",
        company="Example",
        application_url="not-a-url",
    )

    text_path, html_path = downloader.save(job, "base_name")
    assert text_path.exists()
    assert html_path is None
