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

from job_agent.agents.consistency_agent import ConsistencyAgent
from job_agent.agents.form_verifier import FormVerifier
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
    PROTECTED_REQUIRED_QUESTION_NO_DECLINE_OPTION = "protected_required_question_no_decline_option"
    REQUIRED_FIELD_NOT_FILLED = "required_field_not_filled"
    VALIDATION_ERROR_DETECTED = "validation_error_detected"
    UNIDENTIFIABLE_REQUIRED_FIELD = "unidentifiable_required_field"
    CONSISTENCY_CHECK_FAILED = "consistency_check_failed"
    SUBMIT_CONTROL_MISSING_OR_DISABLED = "submit_control_missing_or_disabled"


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

    @staticmethod
    def _final_status_for_failure(reason: DryRunFailureReason) -> str:
        if reason in (DryRunFailureReason.CAPTCHA_BLOCKED, DryRunFailureReason.LOGIN_WALL):
            return "needs_human"
        return "failed"

    @staticmethod
    def _is_hidden_audit_entry(entry: dict[str, Any]) -> bool:
        return entry.get("field_type") == "hidden" or not entry.get("visible", True)

    @staticmethod
    def _blocking_entries(field_audit: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return required fields that are blocking submission, ignoring hidden-only failures."""
        return [
            entry
            for entry in field_audit
            if entry.get("required")
            and entry.get("disposition") in ("needs_human", "skipped")
            and not DryRunHarness._is_hidden_audit_entry(entry)
        ]

    @staticmethod
    def _determine_failure_reason(blocking: list[dict[str, Any]]) -> DryRunFailureReason:
        """Pick the most specific failure reason from the blocking entries."""
        reasons = [entry.get("reason") for entry in blocking]
        if "protected_required_question_no_decline_option" in reasons:
            return DryRunFailureReason.PROTECTED_REQUIRED_QUESTION_NO_DECLINE_OPTION
        if "unidentifiable_required_field" in reasons:
            return DryRunFailureReason.UNIDENTIFIABLE_REQUIRED_FIELD
        if "required_numeric_date_question_not_answered" in reasons:
            return DryRunFailureReason.REQUIRED_FIELD_NOT_FILLED
        return DryRunFailureReason.REQUIRED_FIELD_NOT_FILLED

    @staticmethod
    def _field_audit_summary(field_audit: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "total": len(field_audit),
            "required": sum(1 for e in field_audit if e.get("required")),
            "filled": sum(1 for e in field_audit if e.get("disposition") == "filled"),
            "needs_human": sum(1 for e in field_audit if e.get("disposition") == "needs_human"),
            "skipped": sum(1 for e in field_audit if e.get("disposition") == "skipped"),
        }

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
                log_step("browser_context", "success")
            except Exception as exc:
                log_step("browser_context", "failure", error=str(exc))
                record["failure_reason"] = _classify_failure(exc, adapter.name()).value
                record["final_status"] = self._final_status_for_failure(DryRunFailureReason(record["failure_reason"]))
                record["error"] = str(exc)
                return record

            try:
                await page.goto(job.url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(1.5)
                log_step("page_navigation", "success", {"url": page.url})
            except Exception as exc:
                log_step("page_navigation", "failure", error=str(exc))
                record["failure_reason"] = _classify_failure(exc, adapter.name()).value
                record["final_status"] = self._final_status_for_failure(DryRunFailureReason(record["failure_reason"]))
                record["error"] = str(exc)
                await self._capture_failure_snapshot(page, record, agent)
                await page.close()
                await context.close()
                return record

            # Authentication step.
            account = None
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

            # Adapter-specific preparation (navigate to apply page, etc.)
            try:
                if hasattr(adapter, "prepare_application"):
                    await adapter.prepare_application(page, job, account)
                    log_step("prepare_application", "success", {"url": page.url})
            except Exception as exc:
                log_step("prepare_application", "failure", error=str(exc))
                record["final_status"] = "needs_human"
                record["failure_reason"] = DryRunFailureReason.LOGIN_WALL.value if "login" in str(exc).lower() else DryRunFailureReason.UNEXPECTED_ERROR.value
                record["error"] = str(exc)
                await self._capture_failure_snapshot(page, record, agent)
                await page.close()
                await context.close()
                return record

            # Re-check login after preparation; some platforms only show the gate on the apply page.
            try:
                if await adapter.is_login_required(page):
                    if account is None:
                        log_step("post_prepare_login", "skipped", {"note": "login required on apply page but no saved credentials"})
                        record["final_status"] = "needs_human"
                        record["failure_reason"] = DryRunFailureReason.LOGIN_WALL.value
                        record["error"] = "Login required on apply page; no saved credentials"
                        await self._capture_failure_snapshot(page, record, agent)
                        await page.close()
                        await context.close()
                        return record
                    else:
                        success = await adapter.authenticate(page, account, create_account=False)
                        if not success:
                            log_step("post_prepare_login", "failure", error="Saved credentials failed on apply page")
                            record["final_status"] = "needs_human"
                            record["failure_reason"] = DryRunFailureReason.LOGIN_WALL.value
                            record["error"] = "Saved credentials failed on apply page"
                            await self._capture_failure_snapshot(page, record, agent)
                            await page.close()
                            await context.close()
                            return record
                        log_step("post_prepare_login", "success", {"account": account.username})
            except Exception as exc:
                log_step("post_prepare_login", "failure", error=str(exc))

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
                record["failure_reason"] = _classify_failure(exc, adapter.name()).value
                record["final_status"] = self._final_status_for_failure(DryRunFailureReason(record["failure_reason"]))
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
            profile = agent._profile_dict()
            try:
                fill_result = await adapter.fill_application(
                    page,
                    job,
                    str(resume.path.resolve()),
                    profile,
                    dry_run=True,
                    form_schema=form_schema,
                )
                log_step("fill_application", "success")

                # Browser-side verification and consistency checks.
                agent_audit = getattr(fill_result, "audit", None) or []
                form_verifier = FormVerifier()
                consistency_agent = ConsistencyAgent()
                field_audit = await form_verifier.verify(page, form_schema, agent_audit)
                record["field_audit"] = field_audit
                record["consistency_issues"] = consistency_agent.check(profile, job, field_audit)
                record["validation_errors"] = await form_verifier.detect_validation_errors(page)
                submit_reason = await form_verifier.check_submit_control(page, form_schema)
                record["submit_control_status"] = submit_reason or "ok"
                record["field_audit_summary"] = self._field_audit_summary(field_audit)
            except Exception as exc:
                log_step("fill_application", "failure", error=str(exc))
                record["failure_reason"] = _classify_failure(exc, adapter.name()).value
                record["final_status"] = self._final_status_for_failure(DryRunFailureReason(record["failure_reason"]))
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
                else:
                    log_step("submit", "failure", error="submit button not found")
            except Exception as exc:
                log_step("submit", "failure", error=str(exc))
                reached = False

            # Decide final status based on verification, consistency, and submit reachability.
            validation_errors = record.get("validation_errors", [])
            consistency_issues = record.get("consistency_issues", [])
            submit_reason = record.get("submit_control_status")
            field_audit = record.get("field_audit", [])

            if not reached:
                form_summary = record.get("form_schema", {}).get("summary", {})
                total_fields = form_summary.get("total_fields", 0)
                if adapter.platform_name() == "workday" and total_fields == 0:
                    record["final_status"] = "needs_human"
                    record["failure_reason"] = DryRunFailureReason.LOGIN_WALL.value
                    record["error"] = "Workday application form not reachable; likely requires successful login or unsupported extension flow"
                else:
                    record["final_status"] = "needs_human"
                    record["failure_reason"] = DryRunFailureReason.SUBMIT_CONTROL_MISSING_OR_DISABLED.value
                    record["error"] = "submit button not found"
                await self._capture_failure_snapshot(page, record, agent)
            elif validation_errors:
                record["final_status"] = "needs_human"
                record["failure_reason"] = DryRunFailureReason.VALIDATION_ERROR_DETECTED.value
                record["error"] = f"Validation errors detected: {validation_errors[:3]}"
                await self._capture_failure_snapshot(page, record, agent)
            elif consistency_issues:
                record["final_status"] = "needs_human"
                record["failure_reason"] = DryRunFailureReason.CONSISTENCY_CHECK_FAILED.value
                record["error"] = f"Consistency checks failed: {consistency_issues[:3]}"
                await self._capture_failure_snapshot(page, record, agent)
            elif submit_reason and submit_reason != "ok":
                record["final_status"] = "needs_human"
                record["failure_reason"] = DryRunFailureReason.SUBMIT_CONTROL_MISSING_OR_DISABLED.value
                record["error"] = f"Submit control issue: {submit_reason}"
                await self._capture_failure_snapshot(page, record, agent)
            else:
                blocking = self._blocking_entries(field_audit)
                if blocking:
                    failure_reason = self._determine_failure_reason(blocking)
                    record["final_status"] = "needs_human"
                    record["failure_reason"] = failure_reason.value
                    record["error"] = f"Required fields not filled: {[a.get('label') for a in blocking]}"
                    await self._capture_failure_snapshot(page, record, agent)
                else:
                    record["final_status"] = record.get("final_status") or "queued"
                    record["failure_reason"] = None

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
