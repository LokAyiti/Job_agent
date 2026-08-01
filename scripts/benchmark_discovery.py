"""Benchmark the Scrapling-based discovery layer against a list of real URLs.

Usage:
    python scripts/benchmark_discovery.py --urls https://careers.example.com ...
    python scripts/benchmark_discovery.py --urls-file data/benchmark_urls.txt

Results are written to logs/benchmark_discovery.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from job_agent.config import Settings
from job_agent.scrapling_client import ScraplingClient, ScraplingServiceError
from job_agent.discovery.universal import UniversalDiscovery


DEFAULT_URLS = [
    "https://boards.greenhouse.io/gradial",
    "https://jobs.lever.co/exampleco",
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=data%20analyst&location=United%20States&start=0&count=10",
    "https://www.indeed.com/rss?q=data+analyst&l=United+States&limit=10&sort=date",
    "https://www.governmentjobs.com/careers/home",
]


def fetch_snapshot(client: ScraplingClient, url: str) -> dict:
    start = time.perf_counter()
    try:
        response = client.fetch(url, stealth=True)
        elapsed = time.perf_counter() - start
        data = response.json() if response.text.strip().startswith("{") else {}
        html = response.text
        blocked = "cloudflare" in html.lower() or "just a moment" in html.lower()
        return {
            "url": url,
            "success": not blocked and response.status < 400,
            "status": response.status,
            "time_seconds": round(elapsed, 3),
            "html_length": len(html),
            "cloudflare_detected": blocked,
            "error": None,
        }
    except ScraplingServiceError as exc:
        return {
            "url": url,
            "success": False,
            "status": None,
            "time_seconds": round(time.perf_counter() - start, 3),
            "html_length": 0,
            "cloudflare_detected": False,
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "url": url,
            "success": False,
            "status": None,
            "time_seconds": round(time.perf_counter() - start, 3),
            "html_length": 0,
            "cloudflare_detected": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def run_spider_benchmark(client: ScraplingClient, url: str) -> dict:
    start = time.perf_counter()
    try:
        items = client.run_spider(
            {
                "start_urls": [url],
                "max_depth": 1,
                "use_stealth": True,
                "concurrent_requests": 2,
                "crawldir": "./crawl_data",
            }
        )
        elapsed = time.perf_counter() - start
        return {
            "url": url,
            "success": True,
            "jobs_found": len(items),
            "time_seconds": round(elapsed, 3),
            "error": None,
        }
    except Exception as exc:
        return {
            "url": url,
            "success": False,
            "jobs_found": 0,
            "time_seconds": round(time.perf_counter() - start, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def main():
    parser = argparse.ArgumentParser(description="Benchmark Scrapling-based discovery")
    parser.add_argument("--urls", nargs="*", help="URLs to benchmark")
    parser.add_argument("--urls-file", type=Path, help="File with one URL per line")
    parser.add_argument("--output", type=Path, default=Path("logs/benchmark_discovery.json"))
    parser.add_argument("--spider", action="store_true", help="Also run the generic spider benchmark")
    args = parser.parse_args()

    urls = args.urls or []
    if args.urls_file:
        urls.extend(line.strip() for line in args.urls_file.read_text(encoding="utf-8").splitlines() if line.strip())
    if not urls:
        urls = DEFAULT_URLS

    settings = Settings(_env_file=None)
    client = ScraplingClient(settings)

    if not client.use_service:
        print(
            "WARNING: SCRAPLING_USE_SERVICE is false; falling back to plain requests. "
            "Set SCRAPLING_USE_SERVICE=true and start the scrapling-service for real anti-bot results.",
            file=sys.stderr,
        )

    fetch_results = [fetch_snapshot(client, url) for url in urls]
    spider_results = []
    if args.spider:
        spider_results = [run_spider_benchmark(client, url) for url in urls]

    summary = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "service_url": settings.scrapling_service_url,
        "service_enabled": settings.scrapling_use_service,
        "urls_tested": len(urls),
        "fetch_success_rate": sum(1 for r in fetch_results if r["success"]) / len(fetch_results),
        "avg_fetch_time_seconds": round(sum(r["time_seconds"] for r in fetch_results) / len(fetch_results), 3) if fetch_results else 0,
        "fetch_results": fetch_results,
        "spider_results": spider_results,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Benchmark complete: {args.output}")
    print(f"Success rate: {summary['fetch_success_rate']:.0%}")
    print(f"Avg fetch time: {summary['avg_fetch_time_seconds']}s")


if __name__ == "__main__":
    main()
