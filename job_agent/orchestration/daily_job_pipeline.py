"""Sample Apache Airflow DAG for the automated job application pipeline.

This DAG runs the full Track D pipeline once per day:
  discover -> score -> tailor -> submit (dry-run unless platform is trusted) -> email check

To use it:
1. Install Airflow in your environment:
       pip install apache-airflow
2. Set AIRFLOW_HOME and copy or symlink this file into your DAGs folder:
       cp job_agent/orchestration/daily_job_pipeline.py $AIRFLOW_HOME/dags/
3. Set environment variables or an Airflow connection/variable for the project path.
4. Trigger the DAG manually or wait for the schedule.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator


def _project_root() -> Path:
    """Return the repository root (two levels up from this file)."""
    return Path(__file__).resolve().parent.parent.parent


def _run_cli_command(*args: str) -> None:
    """Run a job_agent.cli command in the project virtual environment."""
    import subprocess
    import sys

    project_root = _project_root()
    python = project_root / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        python = Path(sys.executable)

    cmd = [str(python), "-m", "job_agent.cli", *args]
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(project_root))
    env["PYTHONUNBUFFERED"] = "1"

    result = subprocess.run(
        cmd,
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    print(result.stdout)
    if result.returncode != 0:
        raise RuntimeError(f"Pipeline step failed: {result.stderr}")


def discover_jobs(**context) -> None:
    sources = os.getenv("JOB_DISCOVERY_SOURCES", "governmentjobs")
    output = project_root / "data" / "discovered.json"
    _run_cli_command("discover", f"--sources={sources}", f"--output={output}")
    context["ti"].xcom_push(key="discovered_jobs_path", value=str(output))


def score_and_submit(**context) -> None:
    sources = os.getenv("JOB_DISCOVERY_SOURCES", "governmentjobs")
    _run_cli_command("pipeline", f"--sources={sources}", "--dry-run")


def check_email(**context) -> None:
    _run_cli_command("email")


project_root = _project_root()

with DAG(
    dag_id="daily_job_application_pipeline",
    default_args={
        "owner": "job-agent",
        "depends_on_past": False,
        "email_on_failure": False,
        "email_on_retry": False,
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
    },
    description="Discover jobs, score fit, tailor resumes, and submit applications.",
    schedule=timedelta(days=1),
    start_date=datetime(2026, 8, 1),
    catchup=False,
    tags=["job-agent"],
) as dag:
    discover_task = PythonOperator(
        task_id="discover_jobs",
        python_callable=discover_jobs,
    )

    score_and_submit_task = PythonOperator(
        task_id="score_and_submit",
        python_callable=score_and_submit,
    )

    email_check_task = PythonOperator(
        task_id="check_email",
        python_callable=check_email,
    )

    discover_task >> score_and_submit_task >> email_check_task
