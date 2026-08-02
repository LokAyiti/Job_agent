"""Reusable end-to-end dry-run harness for site adapters.

The harness runs real postings through an adapter with ``ENABLE_AUTO_SUBMIT``
forced to ``False``, captures every pipeline step, classifies failures, and
persists screenshots/HTML snapshots plus structured JSON and Excel reports.
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from job_agent.agents.submission_agent import ApplicationSubmissionAgent
from job_agent.config import Settings, get_settings
from job_agent.models import ApplicationStatus, JobApplication, Resume
from job_agent.persistence.excel_logger import ExcelLogger
from job_agent.persistence.sqlite_queue import SQLiteQueue
from job_agent.sites.base import FormChallenge
from job_agent.sites.registry import build_default_registry
from job_agent.utils.domain_throttler import AsyncDomainThrottler


class DryRunFailureReason(str, Enum):
    CAPTCHA_BLOCKED = "captcha_blocked"
    LOGIN_WALL = "login_wall"
    FIELD_NOT_FOUND = "field_not_found"
    TIMEOUT = "timeout"
    UNEXPECTED_ERROR = "unexpected_error"


def _now() -> str:
    return datetime.now().isoformat()


def make_job(entry: dict[str, str]) -> JobApplication:
    return JobApplication(
        title=entry["title"],
        company=entry["company"],
        url=entry["url"],
        location=entry.get("location"),
        source="dry_run_test",
    )


def _classify_failure(error: Exception, adapter_name: str) -> DryRunFailureReason:
    msg = str(error).lower()
    exc_type = type(error).__name__.lower()

    if "captcha" in msg or "captcha" in exc_type:
        return DryRunFailureReason.CAPTCHA_BLOCKED
    if "login" in msg or "sign in" in msg or "account required" in msg or "login wall" in msg:
        return DryRunFailureReason.LOGIN_WALL
    if "timeout" in exc_type or "timed out" in msg:
        return DryRunFailureReason.TIMEOUT
    if "field" in msg or "submit button not found" in msg or "selector" in msg:
        return DryRunFailureReason.FIELD_NOT_FOUND
    return DryRunFailureReason.UNEXPECTED_ERROR


class DryRunHarness:
    """Run a list of job URLs through a site adapter and capture step output."""

    def __init__(
        self,
        settings: Settings,
        draft_adapter_path: Path | None = None,
    ):
        self.settings = settings
        self.settings.enable_auto_submit = False
        self.registry = build_default_registry()
        if draft_adapter_path:
            self._register_draft(draft_adapter_path)
        self.excel = ExcelLogger(self.settings.log_file)
        self.queue = SQLiteQueue(self.settings.sqlite_db)
        self.throttler = AsyncDomainThrottler(min_delay=2.0, max_delay=3.0)
        self.html_snapshot_dir = getattr(
            self.settings, "html_snapshot_dir", self.settings.log_file.parent / "html_snapshots"
        )
        self.html_snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.report: dict[str, Any] = {
            "meta": {
                "started_at": _now(),
                "platform": None,
                "enable_auto_submit": False,
            },
            "jobs": [],
        }

    def _register_draft(self, path: Path) -> None:
        """Dynamically load a SiteAdapter class from a draft file for validation."""
        import importlib.util

        from job_agent.sites.base import SiteAdapter

        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Could not load draft adapter from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for attr in dir(module):
            obj = getattr(module, attr)
            if isinstance(obj, type) and issubclass(obj, SiteAdapter) and obj is not SiteAdapter:
                self.registry.register(obj)
                logger.info(f"Loaded draft adapter {obj.__name__} from {path}")
                return
        raise ValueError(f"No SiteAdapter subclass found in {path}")

    def _find_best_resume(self, job: JobApplication) -> Resume | None:
        """Select the best resume from the resume/ folder for a job."""
        if not self.settings.resume_dir.exists():
            return None
        candidates: list[Resume] = []
        for file in self.settings.resume_dir.iterdir():
            if file.is_file() and file.suffix.lower() in {".pdf", ".docx", ".doc"}:
                role = file.stem.replace("_", " ").lower()
                candidates.append(Resume(path=file, role=role))
        if not candidates:
            return None
        job_words = set(job.title.lower().split())
        scored = []
        for resume in candidates:
            resume_words = set(resume.role.split())
            score = len(job_words & resume_words)
            scored.append((score, resume))
        scored.sort(key=lambda x: x[0], reverse=True)
        best = scored[0][1]
        return best if best.is_valid() else None

    async def _capture_failure_snapshot(
        self,
        page: Any,
        record: dict[str, Any],
        agent: ApplicationSubmissionAgent,
    ) -> None:
        """Save a screenshot and full HTML snapshot when a step fails."""
        job_id = record.get("job_id", "unknown")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            screenshot = await agent._save_screenshot(page, f"failure_{job_id}_{timestamp}")
            if screenshot:
                record["failure_screenshot"] = str(screenshot)
        except Exception as exc:
            logger.debug(f"Failure screenshot failed: {exc}")

        try:
            html = await page.content()
            html_path = self.html_snapshot_dir / f"failure_{job_id}_{timestamp}.html"
            html_path.write_text(html, encoding="utf-8")
            record["failure_html_snapshot"] = str(html_path)
        except Exception as exc:
            logger.debug(f"Failure HTML snapshot failed: {exc}")

    async def run_job(self, job: JobApplication) -> dict[str, Any]:
        """Run one URL dry-run and return a step-by-step record."""
        record: dict[str, Any] = {
            "job_id": job.id,
            "title": job.title,
            "company": job.company,
            "url": job.url,
            "started_at": _now(),
            "steps": [],
        }

        def log_step(name: str, status: str, detail: Any = None, error: str | None = None):
            step = {
                "step": name,
                "status": status,
                "timestamp": _now(),
            }
            if detail is not None:
                step["detail"] = detail
            if error:
                step["error"] = error
            record["steps"].append(step)
            emoji = "✓" if status == "success" else "✗" if status == "failure" else "⚠"
            logger.info(f"{emoji} [{job.id}] {name}: {status}")

        try:
            adapter = self.registry.get_adapter(job.url)
            job.platform = adapter.platform_name()
            log_step("adapter_selection", "success", {"adapter": adapter.name()})
        except Exception as exc:
            log_step("adapter_selection", "failure", error=str(exc))
            record["final_status"] = "failed"
            record["failure_reason"] = DryRunFailureReason.UNEXPECTED_ERROR.value
            record["error"] = str(exc)
            return record

        async with ApplicationSubmissionAgent(self.settings, registry=self.registry) as agent:
            try:
                domain = urlparse(job.url).hostname or ""
                await self.throttler.wait(domain)
                context = await agent._new_context(domain)
                page = await context.new_page()
                if self.settings.use_stealth:
                    from playwright_stealth import Stealth
                    await Stealth().apply_stealth_async(page)
                log_step("browser_context", "success")
            except Exception as exc:
                log_step("browser_context", "failure", error=str(exc))
                record["final_status"] = "failed"
                record["failure_reason"] = _classify_failure(exc, adapter.name()).value
                record["error"] = str(exc)
                return record

            try:
                await page.goto(job.url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(1.5)
                log_step("page_navigation", "success", {"url": page.url})
            except Exception as exc:
                log_step("page_navigation", "failure", error=str(exc))
                record["final_status"] = "failed"
                record["failure_reason"] = _classify_failure(exc, adapter.name()).value
                record["error"] = str(exc)
                await self._capture_failure_snapshot(page, record, agent)
                await page.close()
                await context.close()
                return record

            # Authentication step.
            try:
                if await adapter.is_login_required(page):
                    from job_agent.persistence.credentials import CredentialStore

                    store = CredentialStore(self.settings.sqlite_db, settings=self.settings)
                    account = store.get(adapter.platform_name(), job.company)
                    if account:
                        success = await adapter.authenticate(page, account, create_account=False)
                        if success:
                            log_step("authentication", "success", {"account": account.username})
                        else:
                            log_step("authentication", "failure", error="Saved credentials failed")
                            record["final_status"] = "needs_human"
                            record["failure_reason"] = DryRunFailureReason.LOGIN_WALL.value
                            record["error"] = "Saved credentials failed"
                            await self._capture_failure_snapshot(page, record, agent)
                            await page.close()
                            await context.close()
                            return record
                    else:
                        log_step("authentication", "skipped", {"note": "dry-run: no saved credentials; skipping account creation"})
                else:
                    log_step("authentication", "success", {"account": None, "note": "login not required"})
            except Exception as exc:
                log_step("authentication", "failure", error=str(exc))
                # Continue anyway; some forms are public.

            # Challenge detection.
            try:
                await adapter.detect_challenges(page, dry_run=True)
                log_step("challenge_detection", "success", {"note": "no blocking challenge"})
            except Exception as exc:
                log_step("challenge_detection", "warning", error=str(exc))
                record["final_status"] = "needs_human"
                record["failure_reason"] = DryRunFailureReason.CAPTCHA_BLOCKED.value if "captcha" in str(exc).lower() else DryRunFailureReason.UNEXPECTED_ERROR.value
                record["error"] = str(exc)
                await self._capture_failure_snapshot(page, record, agent)

            # Form parsing / field schema.
            try:
                form_schema = await adapter.parse_form(page)
                record["form_schema"] = form_schema
                summary = form_schema.get("summary", {})
                log_step(
                    "parse_form",
                    "success",
                    {
                        "total_fields": summary.get("total_fields"),
                        "mapped_fields": summary.get("mapped_fields"),
                        "unmapped_fields": summary.get("unmapped_fields"),
                        "has_submit": summary.get("has_submit"),
                    },
                )
            except Exception as exc:
                log_step("parse_form", "failure", error=str(exc))
                record["final_status"] = "failed"
                record["failure_reason"] = _classify_failure(exc, adapter.name()).value
                record["error"] = str(exc)
                await self._capture_failure_snapshot(page, record, agent)
                await page.close()
                await context.close()
                return record

            # Resume selection.
            resume = self._find_best_resume(job)
            if resume is None or not resume.is_valid():
                base_pdf = self.settings.base_resume_dir / "Resume AI Engineer.pdf"
                if base_pdf.exists():
                    resume = Resume(path=base_pdf, role="default")
            if resume and resume.is_valid():
                log_step("resume_selection", "success", {"path": str(resume.path)})
                job.resume_path = resume.path
            else:
                log_step("resume_selection", "failure", error="No valid resume found")
                record["final_status"] = "failed"
                record["failure_reason"] = DryRunFailureReason.FIELD_NOT_FOUND.value
                record["error"] = "No valid resume found"
                await self._capture_failure_snapshot(page, record, agent)
                await page.close()
                await context.close()
                return record

            # Form filling.
            try:
                await adapter.fill_application(
                    page,
                    job,
                    str(resume.path.resolve()),
                    agent._profile_dict(),
                    dry_run=True,
                )
                log_step("fill_application", "success")
            except Exception as exc:
                log_step("fill_application", "failure", error=str(exc))
                record["final_status"] = "failed"
                record["failure_reason"] = _classify_failure(exc, adapter.name()).value
                record["error"] = str(exc)
                await self._capture_failure_snapshot(page, record, agent)
                await page.close()
                await context.close()
                return record

            # Final submit step (dry-run: should not click).
            try:
                reached = await adapter.submit(page, dry_run=True)
                if reached:
                    log_step("submit", "success", {"note": "dry-run reached final submit step without clicking"})
                    record["final_status"] = record.get("final_status") or "queued"
                else:
                    log_step("submit", "failure", error="submit button not found")
                    form_summary = record.get("form_schema", {}).get("summary", {})
                    total_fields = form_summary.get("total_fields", 0)
                    if adapter.platform_name() == "workday" and total_fields == 0:
                        record["final_status"] = "needs_human"
                        record["failure_reason"] = DryRunFailureReason.LOGIN_WALL.value
                        record["error"] = "Workday application form not reachable; likely requires successful login or unsupported extension flow"
                    else:
                        record["final_status"] = "failed"
                        record["failure_reason"] = DryRunFailureReason.FIELD_NOT_FOUND.value
                        record["error"] = "submit button not found"
                    await self._capture_failure_snapshot(page, record, agent)
            except Exception as exc:
                log_step("submit", "failure", error=str(exc))
                record["final_status"] = "failed"
                record["failure_reason"] = _classify_failure(exc, adapter.name()).value
                record["error"] = str(exc)
                await self._capture_failure_snapshot(page, record, agent)

            try:
                screenshot = await agent._save_screenshot(page, f"dryrun_{job.id}")
                if screenshot:
                    record["screenshot"] = str(screenshot)
            except Exception as exc:
                logger.debug(f"Screenshot failed: {exc}")

            await page.close()
            await context.close()

        record["finished_at"] = _now()

        # Persist to Excel/queue.
        final_status = record.get("final_status", "failed")
        if final_status == "queued":
            job.status = ApplicationStatus.QUEUED
            job.notes = "Dry-run test: form filled, submit step reached"
            job.failure_reason = None
        elif final_status == "needs_human":
            job.status = ApplicationStatus.NEEDS_HUMAN
            job.notes = record.get("error", "Challenge detected")
            job.failure_reason = record.get("failure_reason")
        else:
            job.status = ApplicationStatus.FAILED
            job.error_message = record.get("error", "Unknown dry-run failure")
            job.notes = job.error_message
            job.failure_reason = record.get("failure_reason")

        job.date_applied = datetime.now()
        self.queue.add_or_update(job)
        self.excel.upsert(job)

        return record

    async def run(self, platform: str, entries: list[dict[str, str]]) -> dict[str, Any]:
        self.report["meta"]["platform"] = platform
        for entry in entries:
            job = make_job(entry)
            record = await self.run_job(job)
            self.report["jobs"].append(record)
        self.report["meta"]["finished_at"] = _now()
        return self.report
