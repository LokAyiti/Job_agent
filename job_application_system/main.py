"""Track A orchestrator: scrape, analyze, tailor, generate, log."""

import argparse
import json
import logging
import sys
from pathlib import Path

from agents.jd_downloader import JDDownloader
from config.settings import Settings
from models.job_models import JobListing, TailoredResume
from agents.scraper import GovernmentJobsScraper
from agents.jd_analyzer import JDAnalyzer
from agents.resume_tailor import ResumeTailor
from agents.resume_builder import ResumeBuilder
from agents.cover_letter_builder import CoverLetterBuilder
from data.jobs_log import JobsLog

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(Settings.LOGS_DIR / "track_a.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


class TrackAOrchestrator:
    """Run the Data & Intelligence layer for one job board."""

    def __init__(
        self,
        max_pages_per_state: int = 1,
        max_states: int | None = 1,
        headless: bool = True,
        title_filter: str = "data analyst",
    ) -> None:
        self.max_pages_per_state = max_pages_per_state
        self.max_states = max_states
        self.headless = headless
        self.title_filter = title_filter
        self.tailor = ResumeTailor(Settings.BASE_RESUME_TEMPLATE)
        self.resume_builder = ResumeBuilder(
            Settings.BASE_RESUME_TEMPLATE, Settings.OUTPUT_RESUME_DIR
        )
        self.cover_letter_builder = CoverLetterBuilder(
            Settings.OUTPUT_COVER_LETTER_DIR
        )
        self.jd_downloader = JDDownloader(Settings.OUTPUT_JD_DIR)
        self.log = JobsLog(Settings.DATA_DIR / "jobs_log.xlsx")

    def run(self) -> list[TailoredResume]:
        """Scrape jobs, tailor resumes, generate cover letters, and log results."""
        logger.info("Starting Track A orchestrator for %s", Settings.TARGET_URL)

        # Scrape
        scraper = GovernmentJobsScraper(
            headless=self.headless,
            max_pages_per_state=self.max_pages_per_state,
            max_states=self.max_states,
            title_filter=self.title_filter,
        )
        jobs = scraper.scrape(login=False)
        logger.info("Scraped %s jobs", len(jobs))

        results: list[TailoredResume] = []
        for job in jobs:
            try:
                logger.info("Processing job: %s at %s", job.title, job.company)

                if self.log.exists(job.job_id):
                    logger.info("Skipping duplicate job %s", job.job_id)
                    continue

                # Analyze JD
                jd_analyzer = JDAnalyzer()
                analysis = jd_analyzer.analyze(job)
                logger.info("JD analysis complete for %s", job.job_id)

                # Tailor resume content
                tailored_content = self.tailor.tailor(job)
                logger.info("Resume content tailored for %s", job.job_id)

                # Build resume files
                docx_path, pdf_path, base_name = self.resume_builder.build(
                    tailored_content, job.title, job.company, job.job_id
                )

                # Save JD text/HTML for future reference, named to match the resume.
                jd_text_path, jd_html_path = self.jd_downloader.save(job, base_name)

                # Build cover letter
                highlights = self._build_highlights(tailored_content)
                cover_letter_pdf = self.cover_letter_builder.build(
                    job,
                    highlights,
                    analysis.get("cover_letter_angle", ""),
                )

                tailored = TailoredResume(
                    job=job,
                    resume_docx_path=docx_path,
                    resume_pdf_path=pdf_path,
                    cover_letter_pdf_path=cover_letter_pdf,
                    jd_text_path=jd_text_path,
                    jd_html_path=jd_html_path,
                    status="generated",
                )
                results.append(tailored)

                # Log to Excel
                self.log.add(job, tailored, status="generated")

                # Save raw job data for Track B
                self._save_job_json(job, analysis, tailored_content)

            except Exception as exc:
                logger.error("Failed to process job %s: %s", job.job_id, exc)
                continue

        logger.info("Track A complete. Generated %s resumes", len(results))
        return results

    def submit_application(
        self,
        job: JobListing,
        resume_path: Path,
        cover_letter_path: Path,
    ) -> None:
        """Stub for Track B submission agent.

        Blocks actual submission unless REQUIRES_APPROVAL is explicitly false.
        """
        if Settings.REQUIRES_APPROVAL:
            logger.warning(
                "Submission blocked for %s: REQUIRES_APPROVAL=true. "
                "Set REQUIRES_APPROVAL=false in .env or pass explicit approval to submit.",
                job.job_id,
            )
            return
        logger.info("Submission approved for %s (implementation pending in Track B)", job.job_id)

    def _build_highlights(self, tailored_content: dict) -> str:
        """Create a short highlights string for the cover letter."""
        title = tailored_content.get("professional_title", "")
        summary = tailored_content.get("professional_summary", "")
        skills = ""
        for skill in tailored_content.get("technical_skills", [])[:3]:
            skills += skill.get("skills", "") + ", "
        return f"{title}\n{summary}\nKey skills: {skills.rstrip(', ')}"

    def _save_job_json(self, job: JobListing, analysis: dict, tailored_content: dict) -> None:
        """Persist job data so Track B can pick it up."""
        payload = {
            "job": job.model_dump(mode="json"),
            "analysis": analysis,
            "tailored_content": tailored_content,
        }
        out_path = Settings.DATA_DIR / f"{job.job_id}.json"
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Track A: scrape and tailor resumes")
    parser.add_argument(
        "--pages-per-state",
        type=int,
        default=1,
        help="Number of search result pages to scrape per state",
    )
    parser.add_argument(
        "--states",
        type=int,
        default=1,
        help="Number of US states to search (max 50; default 1 for testing)",
    )
    parser.add_argument(
        "--title-filter",
        type=str,
        default="data analyst",
        help="Keyword filter for job titles (default: data analyst)",
    )
    parser.add_argument(
        "--visible",
        action="store_true",
        help="Run browser in visible mode for debugging",
    )
    args = parser.parse_args()

    orchestrator = TrackAOrchestrator(
        max_pages_per_state=args.pages_per_state,
        max_states=args.states,
        headless=not args.visible,
        title_filter=args.title_filter,
    )
    orchestrator.run()
