"""Bridge to Track A resume/cover-letter generation.

Because Track A uses a separate package layout with relative top-level imports,
we invoke its generation code through a subprocess wrapper that reads JSON and
returns generated file paths.
"""
import json
import subprocess
import tempfile
from pathlib import Path

from loguru import logger

from job_agent.config import Settings
from job_agent.models import JobApplication


class TailoringAgent:
    """Generate tailored resumes and cover letters for job applications."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._bridge = Path(__file__).resolve().parent.parent.parent / "job_application_system" / "tailor_bridge.py"

    def tailor_for_job(self, job: JobApplication) -> Path | None:
        """Generate a tailored resume + cover letter and return the PDF path.

        Returns the generated resume PDF path, or None if generation fails.
        """
        profile = self.settings.load_profile()
        profile_path = self._write_profile_json(profile)
        job_path = self._write_job_json(job)

        try:
            result = self._run_bridge(job_path, profile_path)
            if not result.get("ok"):
                logger.error(f"Tailoring failed for job {job.id}: {result.get('error')}")
                return None

            pdf_path = Path(result["resume_pdf_path"])
            if not pdf_path.exists():
                logger.error(f"Tailoring reported success but PDF not found: {pdf_path}")
                return None

            logger.info(f"Tailored resume generated: {pdf_path}")
            return pdf_path
        except Exception as exc:
            logger.exception(f"Tailoring subprocess failed for job {job.id}: {exc}")
            return None
        finally:
            self._safe_delete(profile_path)
            self._safe_delete(job_path)

    def _run_bridge(self, job_path: Path, profile_path: Path) -> dict:
        """Run the Track A bridge script and parse its JSON output."""
        python = self._python_executable()
        cmd = [
            str(python),
            str(self._bridge),
            "--job",
            str(job_path),
            "--profile",
            str(profile_path),
        ]
        logger.debug(f"Running tailoring bridge: {' '.join(cmd)}")

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            cwd=self._bridge.parent.parent,
        )

        if proc.returncode != 0:
            raise RuntimeError(f"tailor_bridge.py exited {proc.returncode}: {proc.stderr or proc.stdout}")

        # The bridge prints a JSON object as its last line.
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        if not lines:
            raise RuntimeError("tailor_bridge.py produced no output")

        try:
            return json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Could not parse bridge output: {exc}\nOutput: {proc.stdout}")

    def _python_executable(self) -> Path:
        """Return the Python interpreter for the active virtual environment."""
        import sys

        return Path(sys.executable)

    def _write_profile_json(self, profile: dict) -> Path:
        handle, path = tempfile.mkstemp(suffix=".json", prefix="profile_")
        with open(handle, "w", encoding="utf-8") as f:
            json.dump(profile, f, indent=2)
        return Path(path)

    def _write_job_json(self, job: JobApplication) -> Path:
        handle, path = tempfile.mkstemp(suffix=".json", prefix="job_")
        with open(handle, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "id": job.id,
                    "title": job.title,
                    "company": job.company,
                    "location": job.location or "",
                    "url": job.url,
                },
                f,
                indent=2,
            )
        return Path(path)

    def _safe_delete(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
