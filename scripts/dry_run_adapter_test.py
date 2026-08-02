"""CLI entry point for the reusable dry-run harness.

Usage:
    .venv\Scripts\python scripts\dry_run_adapter_test.py --platform workday --urls urls.json
    .venv\Scripts\python scripts\dry_run_adapter_test.py --platform greenhouse --urls urls.json

The harness is implemented in ``job_agent.agents.dry_run_harness`` so it can also
be reused by the weekly regression runner.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from loguru import logger

from job_agent.agents.dry_run_harness import DryRunHarness
from job_agent.config import get_settings
from job_agent.sites.promotion_tracker import PromotionTracker
from job_agent.utils.structured_logging import configure_logging


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_urls(path: Path) -> list[dict[str, str]]:
    """Load a JSON list of {title, company, url, location?} entries."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return [data]
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run validate a site adapter against real postings")
    parser.add_argument("--platform", required=True, help="Adapter platform to test")
    parser.add_argument("--urls", required=True, type=Path, help="JSON file with job postings")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "logs", help="Directory for JSON report")
    parser.add_argument("--headless", type=lambda x: x.lower() in ("1", "true", "yes"), default=True, help="Run browser headless")
    parser.add_argument("--draft-adapter", type=Path, default=None, help="Path to an unapproved SiteAdapter draft to validate")
    args = parser.parse_args()

    configure_logging(level="INFO", log_file=PROJECT_ROOT / "logs" / "dry_run_test.log", json_file=False)

    settings = get_settings()
    settings.enable_auto_submit = False
    settings.browser_headless = args.headless

    entries = load_urls(args.urls)
    harness = DryRunHarness(settings, draft_adapter_path=args.draft_adapter)
    report = asyncio.run(harness.run(args.platform, entries))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = args.output_dir / f"dry_run_{args.platform}_{timestamp}.json"
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    total = len(report["jobs"])
    success = sum(1 for j in report["jobs"] if j.get("final_status") == "queued")
    needs_human = sum(1 for j in report["jobs"] if j.get("final_status") == "needs_human")
    failed = sum(1 for j in report["jobs"] if j.get("final_status") == "failed")

    # Track promotion eligibility.
    tracker = PromotionTracker(settings)
    for job_record in report["jobs"]:
        if job_record.get("final_status") == "queued":
            promoted = tracker.record_success(args.platform, job_record["job_id"])
            if promoted:
                logger.info(f"Platform '{args.platform}' is now promoted to trusted after consecutive dry-run successes")
        else:
            tracker.record_failure(args.platform, job_record.get("failure_reason") or "unknown")

    logger.info("=" * 60)
    logger.info(f"Dry-run complete for {args.platform}")
    logger.info(f"Total: {total} | Reached submit: {success} | Needs human: {needs_human} | Failed: {failed}")
    logger.info(f"Structured report: {report_path}")
    logger.info(f"Excel log: {settings.log_file}")
    logger.info("=" * 60)

    print(f"\nDry-run report: {report_path}")
    print(f"Success: {success}/{total} | Needs human: {needs_human} | Failed: {failed}")


if __name__ == "__main__":
    main()
