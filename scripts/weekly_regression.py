"""Weekly regression runner for all configured site adapters.

This script runs the dry-run harness against every platform that has a
``data/test_<platform>_urls.json`` file, records failures with screenshots and
HTML snapshots, updates promotion status, and writes a combined JSON report.

Intended to be scheduled once per week via Windows Task Scheduler or cron.
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
TEST_URL_GLOB = "data/test_*_urls.json"


def discover_platforms() -> list[tuple[str, Path]]:
    """Return (platform, url_file_path) for every test URL file."""
    platforms = []
    for path in sorted(PROJECT_ROOT.glob(TEST_URL_GLOB)):
        # Filename format: test_<platform>_urls.json
        stem = path.stem
        if stem.startswith("test_") and stem.endswith("_urls"):
            platform = stem[len("test_"): -len("_urls")]
            platforms.append((platform, path))
    return platforms


def load_urls(path: Path) -> list[dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return [data]
    return data


async def run_regression(
    output_dir: Path,
    platforms: list[str] | None = None,
    headless: bool = True,
) -> dict[str, Any]:
    settings = get_settings()
    settings.enable_auto_submit = False
    settings.browser_headless = headless

    discovered = discover_platforms()
    if platforms:
        discovered = [(p, path) for p, path in discovered if p in platforms]

    tracker = PromotionTracker(settings)
    combined_report = {
        "meta": {
            "started_at": datetime.now().isoformat(),
            "platforms": [p for p, _ in discovered],
            "enable_auto_submit": False,
        },
        "results": {},
        "summary": {
            "total_jobs": 0,
            "reached_submit": 0,
            "needs_human": 0,
            "failed": 0,
        },
    }

    for platform, url_file in discovered:
        logger.info(f"Running weekly regression for {platform}")
        entries = load_urls(url_file)
        harness = DryRunHarness(settings)
        report = await harness.run(platform, entries)

        # Update promotion tracker.
        for job_record in report["jobs"]:
            if job_record.get("final_status") == "queued":
                promoted = tracker.record_success(platform, job_record["job_id"])
                if promoted:
                    logger.info(f"Platform '{platform}' promoted to trusted after regression")
            else:
                tracker.record_failure(platform, job_record.get("failure_reason") or "unknown")

        combined_report["results"][platform] = report
        combined_report["summary"]["total_jobs"] += len(report["jobs"])
        combined_report["summary"]["reached_submit"] += sum(
            1 for j in report["jobs"] if j.get("final_status") == "queued"
        )
        combined_report["summary"]["needs_human"] += sum(
            1 for j in report["jobs"] if j.get("final_status") == "needs_human"
        )
        combined_report["summary"]["failed"] += sum(
            1 for j in report["jobs"] if j.get("final_status") == "failed"
        )

    combined_report["meta"]["finished_at"] = datetime.now().isoformat()

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"weekly_regression_{timestamp}.json"
    report_path.write_text(json.dumps(combined_report, indent=2, default=str), encoding="utf-8")

    logger.info("=" * 60)
    logger.info("Weekly regression complete")
    logger.info(combined_report["summary"])
    logger.info(f"Combined report: {report_path}")
    logger.info("=" * 60)

    return combined_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Weekly adapter regression runner")
    parser.add_argument(
        "--platforms",
        default=None,
        help="Comma-separated list of platforms to test (default: all discovered)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "logs",
        help="Directory for the combined JSON report",
    )
    parser.add_argument(
        "--headless",
        type=lambda x: x.lower() in ("1", "true", "yes"),
        default=True,
        help="Run browser headless",
    )
    args = parser.parse_args()

    configure_logging(
        level="INFO",
        log_file=PROJECT_ROOT / "logs" / "weekly_regression.log",
        json_file=False,
    )

    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()] if args.platforms else None
    report = asyncio.run(run_regression(args.output_dir, platforms=platforms, headless=args.headless))

    print(f"\nWeekly regression report: {args.output_dir}")
    print(report["summary"])


if __name__ == "__main__":
    main()
