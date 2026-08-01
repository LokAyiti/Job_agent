"""Orchestrator / CEO Agent — sequences the pipeline and coordinates agents."""
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from loguru import logger

from job_agent.agents.base_agent import BaseAgent
from job_agent.agents.email_agent import EmailAgent
from job_agent.agents.submission_agent import ApplicationSubmissionAgent
from job_agent.config import Settings
from job_agent.models import ApplicationStatus, JobApplication, Resume
from job_agent.persistence.credentials import CredentialStore
from job_agent.persistence.excel_logger import ExcelLogger
from job_agent.persistence.google_sync import GoogleSync
from job_agent.persistence.sqlite_queue import SQLiteQueue


class Orchestrator(BaseAgent):
    """Central controller that reads the queue, applies to jobs, and logs."""

    def __init__(
        self,
        settings: Settings,
        queue: SQLiteQueue | None = None,
        excel: ExcelLogger | None = None,
        google: GoogleSync | None = None,
        credential_store: CredentialStore | None = None,
    ):
        super().__init__(settings)
        self.settings.ensure_dirs()
        self.queue = queue or SQLiteQueue(self.settings.sqlite_db)
        self.excel = excel or ExcelLogger(self.settings.log_file)
        self.google = google or GoogleSync(
            self.settings.google_service_account_json,
            self.settings.google_sheet_id,
            self.settings.google_drive_folder_id,
        )
        self.credential_store = credential_store or CredentialStore(self.settings.sqlite_db)
        self.email_agent = EmailAgent(self.settings)

    def load_jobs_from_json(self, path: Path) -> list[JobApplication]:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        jobs = [JobApplication(**item) for item in raw]
        self.queue.add_many(jobs)
        return jobs

    def load_pending_jobs(self) -> list[JobApplication]:
        return self.queue.list_by_status(ApplicationStatus.PENDING)

    def _find_best_resume(self, job: JobApplication) -> Resume | None:
        if not self.settings.resume_dir.exists():
            return None

        candidates: list[Resume] = []
        for file in self.settings.resume_dir.iterdir():
            if file.is_file() and file.suffix.lower() in {".pdf", ".docx", ".doc"}:
                # Try to infer role from filename (Track A naming convention).
                role = file.stem.replace("_", " ").lower()
                candidates.append(Resume(path=file, role=role))

        if not candidates:
            return None

        # Prefer resumes whose filename contains the job title words.
        job_words = set(job.title.lower().split())
        scored = []
        for resume in candidates:
            resume_words = set(resume.role.split())
            score = len(job_words & resume_words)
            scored.append((score, resume))

        scored.sort(key=lambda x: x[0], reverse=True)
        best = scored[0][1]
        if not best.is_valid():
            return None
        return best

    async def process_job(self, job: JobApplication) -> JobApplication:
        logger.info(f"Processing job {job.id}: {job.title} @ {job.company}")

        # Duplicate check.
        if self.queue.is_duplicate(job) or self.excel.is_duplicate(job):
            logger.info(f"Duplicate job detected: {job.unique_key()}")
            job.status = ApplicationStatus.DUPLICATE
            job.notes = "Skipped duplicate application"
            self._persist(job)
            return job

        # Resume validation.
        resume = self._find_best_resume(job)
        if resume is None:
            job.status = ApplicationStatus.FAILED
            job.error_message = "No valid resume found in resume/ folder"
            self._persist(job)
            return job

        job.resume_path = resume.path
        job.status = ApplicationStatus.IN_PROGRESS
        self._persist(job)

        async with ApplicationSubmissionAgent(
            self.settings,
            credential_store=self.credential_store,
        ) as submission:
            result = await submission.apply_with_retry(job, resume.path)

        job.status = result.status
        job.error_message = result.message
        if result.status == ApplicationStatus.SUBMITTED:
            job.date_applied = datetime.now()
            job.notes = f"Submitted via {job.url}"
        elif result.status == ApplicationStatus.QUEUED:
            job.date_applied = datetime.now()
            job.notes = "Dry-run completed; enable ENABLE_AUTO_SUBMIT to submit"
        elif result.status == ApplicationStatus.NEEDS_HUMAN:
            job.notes = f"Needs human review: {result.message}"
        else:
            job.notes = f"Failed: {result.message}"

        # If failed and retries remain, schedule a future retry in the queue
        # but keep the returned job status as FAILED for accurate reporting.
        if job.status == ApplicationStatus.FAILED:
            stored = self.queue.get(job.id)
            if stored and stored.retry_count < self.settings.max_retries:
                job.retry_count += 1
                self.queue.update_status(
                    job.id,
                    ApplicationStatus.PENDING,
                    notes="Scheduled for retry",
                    increment_retry=True,
                )

        self._persist(job)
        return job

    def _persist(self, job: JobApplication) -> None:
        self.queue.add_or_update(job)
        self.excel.upsert(job)

    def sync_to_google(self) -> None:
        if not self.google.enabled:
            logger.info("Google sync not enabled; skipping")
            return

        # Sync Excel log.
        all_jobs = self.excel.list_applications()
        self.google.sync_applications_to_sheet(all_jobs)

        # Sync resumes.
        self.google.sync_resume_folder(self.settings.resume_dir)

        # Sync Excel file itself to Drive.
        self.google.upload_file(self.settings.log_file)

    def check_email_updates(self) -> None:
        if not self.email_agent.enabled:
            logger.info("Email agent not enabled; skipping email checks")
            return

        jobs = self.excel.list_applications()
        submitted = [j for j in jobs if j.status in {ApplicationStatus.SUBMITTED, ApplicationStatus.QUEUED}]
        if not submitted:
            return

        updates = self.email_agent.check_for_updates(submitted)
        for update in updates:
            job = self.excel.get_application_by_id(update.job_id)
            if job is None:
                continue
            job.status = update.new_status
            job.notes = update.reason
            self._persist(job)
            logger.info(f"Updated job {job.id} to {job.status.value} from email")

    def create_email_drafts(self) -> list:
        """Create human-tone draft replies for recruiter emails matching applied jobs."""
        if not self.email_agent.enabled:
            logger.info("Email agent not enabled; skipping draft creation")
            return []

        jobs = self.excel.list_applications()
        submitted = [j for j in jobs if j.status in {ApplicationStatus.SUBMITTED, ApplicationStatus.QUEUED}]
        if not submitted:
            logger.info("No submitted/queued jobs to create email drafts for")
            return []

        drafts = self.email_agent.create_drafts_for_jobs(submitted)
        logger.info(f"Created {len(drafts)} email drafts for review")
        return drafts

    async def run(self, jobs: Iterable[JobApplication] | None = None) -> list[JobApplication]:
        if jobs is not None:
            self.queue.add_many(jobs)

        pending = self.queue.list_by_status(ApplicationStatus.PENDING)
        if not pending:
            logger.info("No pending jobs to process")
            return []

        results: list[JobApplication] = []
        for job in pending:
            processed = await self.process_job(job)
            results.append(processed)
            if processed.status in {ApplicationStatus.FAILED, ApplicationStatus.NEEDS_HUMAN}:
                logger.warning(f"Job {job.id} stopped at status {processed.status.value}")
            # Rate limiting delay.
            await asyncio.sleep(self.settings.delay_between_jobs_seconds)

        self.check_email_updates()
        self.sync_to_google()
        return results

    def run_sync(self, jobs: Iterable[JobApplication] | None = None) -> list[JobApplication]:
        return asyncio.run(self.run(jobs))
