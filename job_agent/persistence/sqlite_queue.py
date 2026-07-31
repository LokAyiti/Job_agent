"""SQLite-backed job queue for idempotency and crash recovery."""
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

from job_agent.models import ApplicationStatus, JobApplication

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    url TEXT NOT NULL,
    location TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    resume_path TEXT,
    date_applied TEXT,
    error_message TEXT,
    notes TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_company_title ON jobs(company, title);
"""


class SQLiteQueue:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _row_to_application(self, row: sqlite3.Row) -> JobApplication:
        return JobApplication(
            id=row["id"],
            title=row["title"],
            company=row["company"],
            url=row["url"],
            location=row["location"] or None,
            status=ApplicationStatus(row["status"]),
            resume_path=Path(row["resume_path"]) if row["resume_path"] else None,
            date_applied=datetime.fromisoformat(row["date_applied"]) if row["date_applied"] else None,
            error_message=row["error_message"] or None,
            notes=row["notes"] or None,
            retry_count=row["retry_count"] or 0,
        )

    def add_or_update(self, application: JobApplication) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (id, title, company, url, location, status, resume_path,
                                  date_applied, error_message, notes, retry_count, created_at, updated_at)
                VALUES (:id, :title, :company, :url, :location, :status, :resume_path,
                        :date_applied, :error_message, :notes, :retry_count, :now, :now)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    company=excluded.company,
                    url=excluded.url,
                    location=excluded.location,
                    status=excluded.status,
                    resume_path=excluded.resume_path,
                    date_applied=excluded.date_applied,
                    error_message=excluded.error_message,
                    notes=excluded.notes,
                    retry_count=excluded.retry_count,
                    updated_at=excluded.updated_at
                """,
                {
                    "id": application.id,
                    "title": application.title,
                    "company": application.company,
                    "url": application.url,
                    "location": application.location,
                    "status": application.status.value,
                    "resume_path": str(application.resume_path) if application.resume_path else None,
                    "date_applied": application.date_applied.isoformat() if application.date_applied else None,
                    "error_message": application.error_message,
                    "notes": application.notes,
                    "retry_count": application.retry_count,
                    "now": now,
                },
            )

    def add_many(self, applications: Iterable[JobApplication]) -> None:
        for app in applications:
            self.add_or_update(app)

    def list_by_status(self, status: ApplicationStatus | None = None) -> list[JobApplication]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status = ? ORDER BY updated_at",
                    (status.value,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM jobs ORDER BY updated_at").fetchall()
        return [self._row_to_application(row) for row in rows]

    def get(self, job_id: str) -> JobApplication | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_application(row)

    def update_status(
        self,
        job_id: str,
        status: ApplicationStatus,
        error_message: str | None = None,
        notes: str | None = None,
        increment_retry: bool = False,
    ) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            if increment_retry:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, error_message = ?, notes = ?, updated_at = ?, retry_count = retry_count + 1
                    WHERE id = ?
                    """,
                    (status.value, error_message, notes, now, job_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, error_message = ?, notes = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status.value, error_message, notes, now, job_id),
                )

    def delete(self, job_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def is_duplicate(self, application: JobApplication) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM jobs
                WHERE LOWER(company) = LOWER(?) AND LOWER(title) = LOWER(?)
                  AND COALESCE(location, '') = COALESCE(?, '')
                  AND status IN ('submitted', 'queued', 'in_progress', 'responded')
                """,
                (
                    application.company.strip(),
                    application.title.strip(),
                    application.location or "",
                ),
            ).fetchone()
        return row is not None
