"""Consistency Ledger — tracks every generated resume back to its source."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from models.job_models import JobListing


class ConsistencyLedger:
    """Records claims made by each generated resume so the user can review them."""

    def __init__(self, ledger_path: Path | str | None = None):
        if ledger_path is None:
            # Default to project root / data / consistency_ledger.json
            project_root = Path(__file__).resolve().parent.parent.parent
            ledger_path = project_root / "data" / "consistency_ledger.json"
        self.ledger_path = Path(ledger_path)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        job: JobListing,
        source_template: Path,
        fabrication_tolerance: str,
        tailored_content: dict[str, Any],
        output_paths: dict[str, Path | str | None],
        revision: int = 1,
    ) -> dict[str, Any]:
        """Append a record for a generated resume and return the entry."""
        entry = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "job_id": job.job_id,
            "job_title": job.title,
            "job_company": job.company,
            "job_url": job.application_url,
            "source_template": str(source_template),
            "fabrication_tolerance": fabrication_tolerance,
            "revision": revision,
            "claims": self._extract_claims(tailored_content),
            "output_paths": {
                k: str(v) if v else None
                for k, v in output_paths.items()
            },
        }
        self._append(entry)
        logger.info(f"Recorded consistency ledger entry for {job.job_id} (revision {revision})")
        return entry

    def _extract_claims(self, content: dict[str, Any]) -> dict[str, Any]:
        """Pull the claimable facts from the tailored content."""
        claims = {
            "professional_title": content.get("professional_title", ""),
            "professional_summary": content.get("professional_summary", ""),
            "technical_skills": content.get("technical_skills", []),
            "experience_headers": [],
            "bullets": [],
        }
        for exp in content.get("experience", []):
            header = exp.get("job_header", "")
            if header:
                claims["experience_headers"].append(header)
            for bullet in exp.get("bullets", []):
                claims["bullets"].append(bullet)
        return claims

    def _append(self, entry: dict[str, Any]) -> None:
        records = []
        if self.ledger_path.exists():
            try:
                with open(self.ledger_path, "r", encoding="utf-8") as f:
                    records = json.load(f)
            except Exception as exc:
                logger.warning(f"Could not read ledger {self.ledger_path}: {exc}")

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
