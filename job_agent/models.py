"""Core data models for the job application pipeline."""
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class ApplicationStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    FAILED = "failed"
    DUPLICATE = "duplicate"
    NEEDS_HUMAN = "needs_human"
    RESPONDED = "responded"


class JobApplication(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4())[:8])
    title: str
    company: str
    url: str
    location: Optional[str] = None
    description: Optional[str] = None
    requirements: Optional[str] = None
    status: ApplicationStatus = ApplicationStatus.PENDING
    resume_path: Optional[Path] = None
    date_applied: Optional[datetime] = None
    error_message: Optional[str] = None
    notes: Optional[str] = None
    retry_count: int = Field(default=0)
    fit_score: Optional[int] = None
    source: Optional[str] = None
    platform: Optional[str] = None

    @field_validator("url")
    @classmethod
    def _url_must_be_http(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return value

    def unique_key(self) -> str:
        """Canonical key used for duplicate prevention."""
        return f"{self.company.lower().strip()}::{self.title.lower().strip()}::{self.location or ''}"

    def to_log_row(self) -> dict:
        return {
            "job_id": self.id,
            "title": self.title,
            "company": self.company,
            "location": self.location or "",
            "date_applied": self.date_applied.isoformat() if self.date_applied else "",
            "status": self.status.value,
            "link": self.url,
            "resume_path": str(self.resume_path) if self.resume_path else "",
            "error_message": self.error_message or "",
            "notes": self.notes or "",
            "retry_count": self.retry_count,
            "fit_score": self.fit_score if self.fit_score is not None else "",
            "source": self.source or "",
            "platform": self.platform or "",
        }

    @classmethod
    def from_log_row(cls, row: dict) -> "JobApplication":
        return cls(
            id=row.get("job_id", ""),
            title=row.get("title", ""),
            company=row.get("company", ""),
            url=row.get("link", ""),
            location=row.get("location") or None,
            status=ApplicationStatus(row.get("status", "pending")),
            resume_path=Path(row["resume_path"]) if row.get("resume_path") else None,
            date_applied=datetime.fromisoformat(row["date_applied"]) if row.get("date_applied") else None,
            error_message=row.get("error_message") or None,
            notes=row.get("notes") or None,
            retry_count=int(row.get("retry_count", 0)) if row.get("retry_count") else 0,
            fit_score=int(row["fit_score"]) if row.get("fit_score") else None,
            source=row.get("source") or None,
            platform=row.get("platform") or None,
        )


class Account(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4())[:8])
    platform: str
    company: str
    username: str
    password: str
    profile_json: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return self.model_dump()


class Resume(BaseModel):
    path: Path
    role: str
    generated_date: Optional[datetime] = None
    checksum: Optional[str] = None

    def exists(self) -> bool:
        return self.path.exists() and self.path.is_file()

    def is_valid(self) -> bool:
        if not self.exists():
            return False
        if self.path.stat().st_size == 0:
            return False
        return self.path.suffix.lower() in {".pdf", ".docx", ".doc"}
