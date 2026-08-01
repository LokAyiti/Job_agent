"""Subprocess bridge that lets Track B invoke Track A resume generation.

Track A uses a separate package layout and its own .env. Rather than refactor
all of Track A's relative imports, this script is run from the
job_application_system directory and communicates via JSON on stdin/stdout.
"""
import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure job_application_system is on the path so relative top-level imports work.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "job_application_system"))

from agents.jd_analyzer import JDAnalyzer
from agents.resume_builder import ResumeBuilder
from agents.resume_tailor import ResumeTailor
from agents.cover_letter_builder import CoverLetterBuilder
from models.job_models import JobListing, TailoredResume

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _coerce_job_listing(raw: dict) -> JobListing:
    """Build a Track A JobListing from a Track B job dict."""
    return JobListing(
        job_id=raw.get("id", raw.get("job_id", "")),
        title=raw.get("title", ""),
        company=raw.get("company", ""),
        location=raw.get("location", ""),
        description=raw.get("description", ""),
        requirements=raw.get("requirements", ""),
        application_url=raw.get("url", raw.get("application_url", "")),
    )


def _build_tailored_resume(job: JobListing, profile: dict) -> TailoredResume:
    """Generate a DOCX/PDF resume and cover letter for a single job."""
    assets = profile.get("assets", {})
    template = Path(assets.get("base_resume_template", "base resume/Resume AI Engineer.docx"))
    if not template.is_absolute():
        template = PROJECT_ROOT / template

    output_resume_dir = Path(assets.get("output_resume_dir", "resume"))
    if not output_resume_dir.is_absolute():
        output_resume_dir = PROJECT_ROOT / output_resume_dir
    output_resume_dir.mkdir(parents=True, exist_ok=True)

    output_cover_dir = Path(assets.get("base_cover_letter_dir", "base cover letter"))
    if not output_cover_dir.is_absolute():
        output_cover_dir = PROJECT_ROOT / output_cover_dir
    output_cover_dir.mkdir(parents=True, exist_ok=True)

    analyzer = JDAnalyzer()
    job_analysis = analyzer.analyze(job)

    tailor = ResumeTailor(template)
    tailored_content = tailor.tailor(job)

    resume_builder = ResumeBuilder(template, output_resume_dir)
    docx_path, pdf_path = resume_builder.build(
        tailored_content,
        job.title,
        job.company,
        job_id=job.job_id,
    )

    highlights = "\n".join(profile.get("experience_highlights", []))
    angle = job_analysis.get("cover_letter_angle", "")
    cover_builder = CoverLetterBuilder(output_cover_dir)
    cover_pdf_path = cover_builder.build(job, highlights, angle)

    return TailoredResume(
        job=job,
        resume_docx_path=docx_path,
        resume_pdf_path=pdf_path,
        cover_letter_pdf_path=cover_pdf_path,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate tailored resume + cover letter.")
    parser.add_argument("--job", required=True, help="Path to JSON file describing the job.")
    parser.add_argument("--profile", required=True, help="Path to unified profile JSON.")
    args = parser.parse_args()

    with open(args.job, "r", encoding="utf-8") as f:
        job_raw = json.load(f)
    with open(args.profile, "r", encoding="utf-8") as f:
        profile = json.load(f)

    job = _coerce_job_listing(job_raw)
    result = _build_tailored_resume(job, profile)

    output = {
        "ok": True,
        "resume_docx_path": str(result.resume_docx_path),
        "resume_pdf_path": str(result.resume_pdf_path),
        "cover_letter_pdf_path": str(result.cover_letter_pdf_path) if result.cover_letter_pdf_path else None,
        "job_id": result.job.job_id,
        "title": result.job.title,
        "company": result.job.company,
    }
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        logger.exception("Tailoring failed")
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)
