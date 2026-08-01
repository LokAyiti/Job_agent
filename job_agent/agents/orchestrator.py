"""Orchestrator / CEO Agent — sequences the pipeline and coordinates agents."""
import asyncio
import json
import random
from datetime import datetime
from pathlib import Path
from typing import Iterable

from loguru import logger

from job_agent.agents.base_agent import BaseAgent
from job_agent.agents.email_agent import EmailAgent
from job_agent.agents.scoring_agent import ScoringAgent
from job_agent.agents.submission_agent import ApplicationSubmissionAgent
from job_agent.agents.tailoring_agent import TailoringAgent
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
        tailoring_agent: TailoringAgent | None = None,
        scoring_agent: ScoringAgent | None = None,
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
        self.credential_store = credential_store or CredentialStore(
            self.settings.sqlite_db,
            settings=self.settings,
        )
        self.email_agent = EmailAgent(self.settings)
        self.tailoring_agent = tailoring_agent or TailoringAgent(self.settings)
        self.scoring_agent = scoring_agent or ScoringAgent(self.settings)

    def load_jobs_from_json(self, path: Path) -> list[JobApplication]:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        jobs = [JobApplication(**item) for item in raw]
        self.queue.add_many(jobs)
        return jobs

    def load_pending_jobs(self) -> list[JobApplication]:
        return self.queue.list_by_status(ApplicationStatus.PENDING)

    def discover_jobs(self, sources: list[str] | None = None) -> list[JobApplication]:
        """Run configured discovery sources and add jobs to the queue."""
        from job_agent.discovery.registry import DiscoveryRegistry

        profile = self.settings.load_profile()
        registry = DiscoveryRegistry()
        discovered = registry.discover_all(profile, sources=sources)
        if discovered:
            self.queue.add_many(discovered)
            logger.info(f"Discovered and queued {len(discovered)} jobs")
        return discovered

    def score_jobs(self, jobs: list[JobApplication]) -> list[JobApplication]:
        """Score each job and filter out those below the configured threshold."""
        threshold = self.settings.min_fit_score
        scored: list[JobApplication] = []
        for job in jobs:
            score, reason = self.scoring_agent.score(job)
            job.fit_score = score
            job.notes = reason
            if score >= threshold:
                logger.info(f"Job {job.id} scored {score}/100 — queued")
                scored.append(job)
            else:
                logger.info(f"Job {job.id} scored {score}/100 — filtered out")
                job.status = ApplicationStatus.FAILED
                job.error_message = f"Fit score {score} below threshold {threshold}: {reason}"
            self._persist(job)
        return scored

    def approve_job(self, job_id: str) -> JobApplication | None:
        """Mark a queued job as approved and consider auto-promoting its platform."""
        job = self.queue.get(job_id)
        if job is None:
            return None
        if job.status not in {ApplicationStatus.QUEUED, ApplicationStatus.SUBMITTED}:
            logger.warning(f"Cannot approve job {job_id} with status {job.status.value}")
            return job

        platform = job.platform or self._detect_platform(job.url)
        if platform and platform not in self.settings.trusted_platform_list:
            self._record_success_and_maybe_trust(platform)
        job.notes = f"Approved by human. Platform: {platform}"
        self._persist(job)
        logger.info(f"Job {job_id} approved")
        return job

    def _detect_platform(self, url: str) -> str | None:
        from job_agent.sites.registry import build_default_registry

        try:
            registry = build_default_registry()
            return registry.detect_platform(url)
        except Exception:
            return None

    def _record_success_and_maybe_trust(self, platform: str) -> None:
        """After enough successful approvals on a platform, mark it as trusted."""
        # Count existing approved/queued/submitted jobs for this platform.
        count = sum(
            1
            for j in self.excel.list_applications()
            if (j.platform or self._detect_platform(j.url)) == platform
            and j.status.value in {"queued", "submitted", "responded"}
        )
        threshold = self.settings.auto_approve_after_successes
        if count >= threshold and platform not in self.settings.trusted_platform_list:
            logger.info(
                f"Platform {platform} has {count} approved/submitted jobs; adding to trusted_platforms"
            )
            self._add_trusted_platform(platform)

    def _add_trusted_platform(self, platform: str) -> None:
        """Persist a newly trusted platform. In this MVP we log and update settings."""
        current = self.settings.trusted_platform_list
        if platform not in current:
            current.append(platform)
            # Settings are loaded from env; we cannot rewrite .env cleanly here.
            # Store the updated list in settings for the current process lifetime.
            self.settings.trusted_platforms = ",".join(current)
            logger.info(f"Trusted platforms updated: {current}")

    def _should_auto_submit(self, platform: str | None) -> bool:
        """Auto-submit only when explicitly enabled AND platform is trusted."""
        if not self.settings.enable_auto_submit:
            return False
        if not platform:
            return False
        return platform in self.settings.trusted_platform_list

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

        # Fit scoring for jobs discovered without a score (manual jobs skip scoring).
        if job.fit_score is None and job.source:
            score, reason = self.scoring_agent.score(job)
            job.fit_score = score
            logger.info(f"Fit score for job {job.id}: {score}/100")

        if job.fit_score is not None and job.fit_score < self.settings.min_fit_score:
            job.status = ApplicationStatus.FAILED
            job.error_message = f"Fit score {job.fit_score} below threshold {self.settings.min_fit_score}"
            job.notes = job.error_message
            self._persist(job)
            return job

        # Resume validation / auto-tailoring for discovered jobs only.
        resume = self._find_best_resume(job)
        if resume is None and job.source:
            logger.info(f"No existing resume for discovered job {job.id}; generating tailored resume")
            tailored_path = self.tailoring_agent.tailor_for_job(job)
            if tailored_path is None:
                job.status = ApplicationStatus.FAILED
                job.error_message = "Tailored resume generation failed"
                self._persist(job)
                return job
            resume = Resume(path=tailored_path, role=job.title.lower())

        if resume is None or not resume.is_valid():
            job.status = ApplicationStatus.FAILED
            job.error_message = "No valid resume found in resume/ folder"
            self._persist(job)
            return job

        job.resume_path = resume.path
        job.status = ApplicationStatus.IN_PROGRESS
        self._persist(job)

        # Determine platform for trusted-platform gating.
        platform = job.platform or self._detect_platform(job.url)
        auto_submit = self._should_auto_submit(platform)
        if self.settings.enable_auto_submit and not auto_submit:
            logger.info(
                f"Auto-submit enabled but platform '{platform}' is not trusted; forcing dry-run behavior"
            )

        async with ApplicationSubmissionAgent(
            self.settings,
            credential_store=self.credential_store,
        ) as submission:
            # Temporarily override auto-submit if platform is not trusted.
            original_auto_submit = self.settings.enable_auto_submit
            if original_auto_submit and not auto_submit:
                self.settings.enable_auto_submit = False
            try:
                result = await submission.apply_with_retry(job, resume.path)
            finally:
                self.settings.enable_auto_submit = original_auto_submit

        job.status = result.status
        job.error_message = result.message
        if result.status == ApplicationStatus.SUBMITTED:
            job.date_applied = datetime.now()
            job.notes = f"Submitted via {job.url}"
            if platform:
                self._record_success_and_maybe_trust(platform)
        elif result.status == ApplicationStatus.QUEUED:
            job.date_applied = datetime.now()
            job.notes = "Dry-run completed; approve platform to enable auto-submit"
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
        submitted = [j for j in jobs if j.status in {ApplicationStatus.SUBMITTED, ApplicationStatus.QUEUED, ApplicationStatus.RESPONDED}]
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

        # Create draft replies for recruiter messages related to queued/submitted jobs.
        drafts = self.email_agent.create_drafts_for_jobs(submitted)
        if drafts:
            logger.info(f"Created {len(drafts)} email draft replies for review")

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
            # Rate limiting delay with optional jitter.
            jitter = random.uniform(0.5, 1.5) if self.settings.jitter_between_jobs else 1.0
            delay = self.settings.delay_between_jobs_seconds * jitter
            await asyncio.sleep(delay)

        self.check_email_updates()
        self.sync_to_google()
        return results

    def run_sync(self, jobs: Iterable[JobApplication] | None = None) -> list[JobApplication]:
        return asyncio.run(self.run(jobs))
