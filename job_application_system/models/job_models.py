"""Pydantic models for job application data."""

from datetime import datetime
from pathlib import Path
from pydantic import BaseModel, Field


class JobListing(BaseModel):
    """A scraped job listing."""

    job_id: str = ""
    title: str
    company: str = ""
    location: str = ""
    description: str = ""
    requirements: str = ""
    application_url: str = ""
    posted_date: str = ""
    scraped_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        arbitrary_types_allowed = True


class TailoredResume(BaseModel):
    """A generated resume with file paths."""

    job: JobListing
    resume_docx_path: Path
    resume_pdf_path: Path
    cover_letter_pdf_path: Path | None = None
    jd_text_path: Path | None = None
    jd_html_path: Path | None = None
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    status: str = "generated"  # generated, queued, applied, failed
