"""Subprocess bridge that lets Track B invoke the Track A resume tailoring engine.

Track A uses a separate package layout and its own .env. Rather than refactor
all of Track A's relative imports, this script is run from the
job_application_system directory and communicates via JSON on stdin/stdout.

This bridge now orchestrates the four resume-tailoring sub-agents:
  1. JD Analyzer Agent
  2. Resume Retriever Agent
  3. Rewrite / Fabrication Agent
  4. ATS / Recruiter Scoring Agent

and records every generated resume in the Consistency Ledger.
"""
import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure project root and job_application_system are on the path so both
# job_application_system relative imports and cross-package imports work.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "job_application_system"))

from agents.ats_recruiter_scorer import ATSRecruiterScorer
from agents.consistency_ledger import ConsistencyLedger
from agents.cover_letter_builder import CoverLetterBuilder
from agents.jd_analyzer import JDAnalyzer
from agents.jd_downloader import JDDownloader
from agents.resume_builder import ResumeBuilder
from agents.resume_retriever import ResumeRetriever
from agents.resume_tailor import ResumeTailor
from job_agent.agents.feedback_ledger import FeedbackLedger
from models.job_models import JobListing, TailoredResume

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Maximum rewrite attempts when the ATS/recruiter scorer rejects a draft.
MAX_REWRITE_ATTEMPTS = 3


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


def _resolve_path(value: str | Path | None, default: str) -> Path:
    path = Path(value) if value else PROJECT_ROOT / default
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _build_tailored_resume(job: JobListing, profile: dict) -> TailoredResume:
    """Generate a DOCX/PDF resume and cover letter for a single job."""
    assets = profile.get("assets", {})
    preferences = profile.get("preferences", {})

    base_resume_dir = _resolve_path(assets.get("base_resume_dir"), "base resume")
    fallback_template = _resolve_path(assets.get("base_resume_template"), "base resume/Resume AI Engineer.docx")
    output_resume_dir = _resolve_path(assets.get("output_resume_dir"), "resume")
    output_resume_dir.mkdir(parents=True, exist_ok=True)

    output_cover_dir = _resolve_path(assets.get("base_cover_letter_dir"), "base cover letter")
    output_cover_dir.mkdir(parents=True, exist_ok=True)

    output_jd_dir = _resolve_path(assets.get("output_jd_dir"), "base job description")
    output_jd_dir.mkdir(parents=True, exist_ok=True)

    fabrication_tolerance = preferences.get("fabrication_tolerance", "moderate")

    # 1. JD Analyzer Agent
    analyzer = JDAnalyzer()
    job_analysis = analyzer.analyze(job)

    # 2. Resume Retriever Agent
    retriever = ResumeRetriever(base_resume_dir, fallback_template=fallback_template)
    selected_template = retriever.retrieve(job, profile)

    # 3. Rewrite / Fabrication Agent with scorer feedback loop.
    tailor = ResumeTailor(selected_template)
    scorer = ATSRecruiterScorer()
    tailored_content = None
    revision = 1
    scorer_feedback = ""

    feedback_ledger = FeedbackLedger()
    feedback_hints = feedback_ledger.get_successful_claims(job.title)

    for attempt in range(1, MAX_REWRITE_ATTEMPTS + 1):
        logger.info(f"Resume rewrite attempt {attempt}/{MAX_REWRITE_ATTEMPTS} for {job.job_id}")
        tailored_content = tailor.tailor(
            job,
            fabrication_tolerance=fabrication_tolerance,
            feedback_hints=feedback_hints,
        )

        score = scorer.score(tailored_content, job)
        logger.info(
            f"ATS={score['ats_score']}, recruiter={score['recruiter_score']}, "
            f"passed={score['passed']}, feedback={score['feedback']}"
        )
        if score["passed"]:
            revision = attempt
            break

        scorer_feedback = score["feedback"]
        if attempt < MAX_REWRITE_ATTEMPTS:
            logger.info(f"Applying scorer feedback: {scorer_feedback}")
            # Inject feedback into the tailor for the next loop by appending to the
            # base resume text context. This is a simple feedback mechanism; the LLM
            # will see the feedback in the next rewrite.
            tailor.base_resume_text = (
                tailor.base_resume_text
                + f"\n\n[Previous draft scored ATS={score['ats_score']}, recruiter={score['recruiter_score']}. "
                f"Feedback from ATS/recruiter scorer: {scorer_feedback}]"
            )
        else:
            logger.warning(
                f"ATS/recruiter scorer did not pass after {MAX_REWRITE_ATTEMPTS} attempts; "
                f"using the last draft anyway."
            )
            revision = attempt

    if tailored_content is None:
        raise RuntimeError("Resume tailoring produced no content")

    # 4. Resume Builder exports DOCX/PDF.
    resume_builder = ResumeBuilder(selected_template, output_resume_dir)
    docx_path, pdf_path, base_name = resume_builder.build(
        tailored_content,
        job.title,
        job.company,
        job_id=job.job_id,
    )

    # 4b. Save JD text/HTML alongside the resume for future reference.
    jd_downloader = JDDownloader(output_jd_dir)
    jd_text_path, jd_html_path = jd_downloader.save(job, base_name)

    # 5. Cover Letter Builder.
    highlights = "\n".join(profile.get("experience_highlights", []))
    angle = job_analysis.get("cover_letter_angle", "")
    cover_builder = CoverLetterBuilder(output_cover_dir)
    cover_pdf_path = cover_builder.build(job, highlights, angle)

    # 6. Consistency Ledger.
    ledger = ConsistencyLedger()
    ledger.record(
        job=job,
        source_template=selected_template,
        fabrication_tolerance=fabrication_tolerance,
        tailored_content=tailored_content,
        output_paths={
            "resume_docx": docx_path,
            "resume_pdf": pdf_path,
            "cover_letter_pdf": cover_pdf_path,
            "jd_text": jd_text_path,
            "jd_html": jd_html_path,
        },
        revision=revision,
    )

    return TailoredResume(
        job=job,
        resume_docx_path=docx_path,
        resume_pdf_path=pdf_path,
        cover_letter_pdf_path=cover_pdf_path,
        jd_text_path=jd_text_path,
        jd_html_path=jd_html_path,
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
        "jd_text_path": str(result.jd_text_path) if result.jd_text_path else None,
        "jd_html_path": str(result.jd_html_path) if result.jd_html_path else None,
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
