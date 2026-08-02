"""Backfill logs/applications.xlsx from dry-run JSON reports."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from job_agent.models import ApplicationStatus, JobApplication
from job_agent.persistence.excel_logger import ExcelLogger

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORTS = [
    PROJECT_ROOT / "logs" / "dry_run_greenhouse_20260801_182322.json",
    PROJECT_ROOT / "logs" / "dry_run_workday_20260801_182347.json",
    PROJECT_ROOT / "logs" / "dry_run_oracle_20260801_182138.json",
]


def main() -> None:
    logger = ExcelLogger(PROJECT_ROOT / "logs" / "applications.xlsx")

    # Remove any test rows that may have been inserted accidentally.
    wb = logger._load()
    ws = wb.active
    rows_to_delete = []
    for idx, row in enumerate(ws.iter_rows(min_row=2, values_only=False), start=2):
        source_cell = row[12].value if len(row) > 12 else None
        title_cell = row[1].value
        if source_cell == "test" or (title_cell and str(title_cell).startswith("Test ")):
            rows_to_delete.append(idx)
    for idx in reversed(rows_to_delete):
        ws.delete_rows(idx)
    wb.save(logger.log_file)

    for report_path in REPORTS:
        if not report_path.exists():
            print(f"Missing report: {report_path}")
            continue
        report = json.loads(report_path.read_text(encoding="utf-8"))
        platform = report["meta"]["platform"]
        for job_record in report["jobs"]:
            status_str = job_record.get("final_status", "failed")
            status = ApplicationStatus(status_str) if status_str in ApplicationStatus._value2member_map_ else ApplicationStatus.FAILED
            finished_at = job_record.get("finished_at")
            date_applied = datetime.fromisoformat(finished_at) if finished_at else datetime.now()

            app = JobApplication(
                id=job_record["job_id"],
                title=job_record["title"],
                company=job_record["company"],
                url=job_record["url"],
                status=status,
                date_applied=date_applied,
                error_message=job_record.get("error") or None,
                notes=(job_record.get("error") if status == ApplicationStatus.NEEDS_HUMAN else None),
                source="dry_run_test",
                platform=platform,
            )
            resume_path = job_record.get("resume_path")
            if not resume_path:
                for step in job_record.get("steps", []):
                    if step.get("step") == "resume_selection" and step.get("detail", {}).get("path"):
                        resume_path = step["detail"]["path"]
                        break
            if resume_path:
                app.resume_path = Path(resume_path)
            if status == ApplicationStatus.QUEUED:
                app.notes = "Dry-run test: form filled, submit step reached"
            logger.upsert(app)
            print(f"Upserted {platform}: {app.id} {app.title} ({app.status.value})")

    print("Backfill complete.")


if __name__ == "__main__":
    main()
