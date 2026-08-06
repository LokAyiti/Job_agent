"""Excel job application log."""

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from models.job_models import JobListing, TailoredResume

logger = logging.getLogger(__name__)


class JobsLog:
    """Persist job application records to an Excel sheet."""

    COLUMNS = [
        "job_id",
        "title",
        "company",
        "location",
        "date_applied",
        "status",
        "application_link",
        "resume_pdf_path",
        "cover_letter_pdf_path",
        "jd_text_path",
        "jd_html_path",
    ]

    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path

    def _load_df(self) -> pd.DataFrame:
        if self.log_path.exists():
            return pd.read_excel(self.log_path)
        return pd.DataFrame(columns=self.COLUMNS)

    def _save_df(self, df: pd.DataFrame) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_excel(self.log_path, index=False)

    def exists(self, job_id: str) -> bool:
        """Return True if the job_id already exists in the log."""
        if not self.log_path.exists():
            return False
        df = self._load_df()
        return job_id in df["job_id"].values

    def add(
        self,
        job: JobListing,
        tailored: TailoredResume,
        status: str = "generated",
    ) -> None:
        """Append a new job record to the log."""
        df = self._load_df()

        # Avoid duplicate rows by job_id
        if job.job_id in df["job_id"].values:
            logger.info("Job %s already in log; skipping duplicate", job.job_id)
            return

        new_row = pd.DataFrame([{
            "job_id": job.job_id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "date_applied": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": status,
            "application_link": job.application_url,
            "resume_pdf_path": str(tailored.resume_pdf_path) if tailored.resume_pdf_path else "",
            "cover_letter_pdf_path": (
                str(tailored.cover_letter_pdf_path) if tailored.cover_letter_pdf_path else ""
            ),
            "jd_text_path": str(tailored.jd_text_path) if tailored.jd_text_path else "",
            "jd_html_path": str(tailored.jd_html_path) if tailored.jd_html_path else "",
        }])
        df = pd.concat([df, new_row], ignore_index=True)
        self._save_df(df)
        logger.info("Added job %s to log: %s", job.job_id, self.log_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from pathlib import Path
    log = JobsLog(Path("test_jobs.xlsx"))
    sample_job = JobListing(
        job_id="12345",
        title="Data Analyst",
        company="State",
        location="TX",
        application_url="https://example.com",
    )
    sample_tailored = TailoredResume(
        job=sample_job,
        resume_docx_path=Path("resume.docx"),
        resume_pdf_path=Path("resume.pdf"),
        jd_text_path=Path("jd.txt"),
    )
    log.add(sample_job, sample_tailored)
