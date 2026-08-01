"""Feedback Ledger — records outcomes from recruiter emails to influence future tailoring.

When the Email Agent detects a callback, rejection, or no response for a job, it
writes an outcome record here. The Resume Tailoring Engine reads the ledger and
gives higher weight to rewrite patterns and claims that previously produced callbacks.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from job_agent.models import ApplicationStatus, JobApplication


class FeedbackLedger:
    """Store outcome records per resume variant and provide tailoring hints."""

    def __init__(self, ledger_path: Path | str | None = None):
        if ledger_path is None:
            project_root = Path(__file__).resolve().parent.parent.parent
            ledger_path = project_root / "data" / "feedback_ledger.json"
        self.ledger_path = Path(ledger_path)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        job: JobApplication,
        outcome: str,  # "callback", "rejection", "no_response", "human"
        source_resume: str | None = None,
        fabrication_tolerance: str | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        """Append an outcome record and return the entry."""
        if outcome not in {"callback", "rejection", "no_response", "human"}:
            raise ValueError(f"Invalid outcome: {outcome}")

        entry = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "job_id": job.id,
            "job_title": job.title,
            "job_company": job.company,
            "outcome": outcome,
            "source_resume": source_resume or (str(job.resume_path) if job.resume_path else None),
            "fabrication_tolerance": fabrication_tolerance,
            "notes": notes,
        }
        self._append(entry)
        logger.info(f"Recorded feedback outcome '{outcome}' for {job.id}")
        return entry

    def record_from_status_update(
        self,
        job: JobApplication,
        new_status: ApplicationStatus,
        source_resume: str | None = None,
        fabrication_tolerance: str | None = None,
        notes: str = "",
    ) -> dict[str, Any] | None:
        """Map an ApplicationStatus update to a feedback outcome and record it."""
        mapping = {
            ApplicationStatus.RESPONDED: "callback",
            ApplicationStatus.FAILED: "rejection",
            ApplicationStatus.NEEDS_HUMAN: "human",
        }
        outcome = mapping.get(new_status)
        if outcome is None:
            return None
        return self.record(
            job=job,
            outcome=outcome,
            source_resume=source_resume,
            fabrication_tolerance=fabrication_tolerance,
            notes=notes,
        )

    def _append(self, entry: dict[str, Any]) -> None:
        records = []
        if self.ledger_path.exists():
            try:
                with open(self.ledger_path, "r", encoding="utf-8") as f:
                    records = json.load(f)
            except Exception as exc:
                logger.warning(f"Could not read feedback ledger {self.ledger_path}: {exc}")

        records.append(entry)
        with open(self.ledger_path, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2)

    def list_entries(self) -> list[dict[str, Any]]:
        if not self.ledger_path.exists():
            return []
        with open(self.ledger_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_entries_for_job(self, job_id: str) -> list[dict[str, Any]]:
        return [entry for entry in self.list_entries() if entry.get("job_id") == job_id]

    def get_successful_claims(
        self,
        job_title: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return recent callback records, optionally filtered by job title similarity.

        The resume tailor can use these as hints for phrasing that has already
        produced recruiter responses.
        """
        records = self.list_entries()
        callbacks = [r for r in records if r.get("outcome") == "callback"]
        if job_title:
            title_lower = job_title.lower()
            callbacks = [
                r for r in callbacks
                if title_lower in (r.get("job_title") or "").lower()
                or (r.get("job_title") or "").lower() in title_lower
            ]
        callbacks.sort(key=lambda r: r.get("recorded_at", ""), reverse=True)
        return callbacks[:limit]

    def get_historical_outcomes(self, job_title: str) -> dict[str, int]:
        """Return counts of outcomes for a given job title (or similar titles)."""
        title_lower = job_title.lower()
        counts: dict[str, int] = {}
        for entry in self.list_entries():
            entry_title = (entry.get("job_title") or "").lower()
            if title_lower in entry_title or entry_title in title_lower:
                outcome = entry.get("outcome", "unknown")
                counts[outcome] = counts.get(outcome, 0) + 1
        return counts
