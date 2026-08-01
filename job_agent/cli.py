"""Command-line interface for the job-application orchestrator."""
import json
import sys
from pathlib import Path

import click
from loguru import logger

from job_agent.agents.orchestrator import Orchestrator
from job_agent.config import Settings, get_settings, reload_settings
from job_agent.models import ApplicationStatus, JobApplication
from job_agent.utils.structured_logging import configure_logging


def _configure_logging_from_settings():
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        log_file=settings.agent_log_file if settings.log_to_file else None,
        json_file=settings.json_logs,
    )


@click.group()
def cli():
    """Automated job application system — Track B CLI."""
    _configure_logging_from_settings()
    pass


@cli.command()
@click.option("--jobs", "jobs_path", type=click.Path(exists=True, path_type=Path), required=False)
@click.option("--dry-run/--no-dry-run", default=None, help="Override ENABLE_AUTO_SUBMIT in .env")
@click.option("--env-file", type=click.Path(path_type=Path), default=None)
def run(jobs_path: Path | None, dry_run: bool | None, env_file: Path | None):
    """Process pending jobs: submit applications and log results."""
    if env_file:
        import os
        os.environ.setdefault("ENV_FILE", str(env_file))
        reload_settings()
        _configure_logging_from_settings()

    settings = get_settings()
    if dry_run is not None:
        settings.enable_auto_submit = not dry_run

    logger.info(f"Starting orchestrator (auto_submit={settings.enable_auto_submit})")
    orchestrator = Orchestrator(settings)

    jobs: list[JobApplication] | None = None
    if jobs_path:
        jobs = orchestrator.load_jobs_from_json(jobs_path)
        logger.info(f"Loaded {len(jobs)} jobs from {jobs_path}")
    else:
        jobs = orchestrator.load_pending_jobs()
        logger.info(f"Found {len(jobs)} pending jobs in local queue")

    results = orchestrator.run_sync(jobs)

    submitted = sum(1 for r in results if r.status == ApplicationStatus.SUBMITTED)
    queued = sum(1 for r in results if r.status == ApplicationStatus.QUEUED)
    failed = sum(1 for r in results if r.status == ApplicationStatus.FAILED)
    human = sum(1 for r in results if r.status == ApplicationStatus.NEEDS_HUMAN)
    duplicate = sum(1 for r in results if r.status == ApplicationStatus.DUPLICATE)

    click.echo(f"Done — submitted: {submitted}, dry-run queued: {queued}, failed: {failed}, needs human: {human}, duplicate: {duplicate}")
    click.echo(f"Log written to: {settings.log_file}")


@cli.command("drafts")
@click.option("--env-file", type=click.Path(path_type=Path), default=None)
def drafts(env_file: Path | None):
    """Scan Gmail/Outlook and create draft replies for recruiter emails."""
    if env_file:
        import os
        os.environ.setdefault("ENV_FILE", str(env_file))
        reload_settings()
        _configure_logging_from_settings()

    settings = get_settings()
    orchestrator = Orchestrator(settings)
    created = orchestrator.create_email_drafts()
    click.echo(f"Created {len(created)} draft replies for review")
    for draft in created:
        click.echo(f"  [{draft.provider}] {draft.to}: {draft.subject}")


@cli.command()
@click.option("--env-file", type=click.Path(path_type=Path), default=None)
def sync(env_file: Path | None):
    """Sync local Excel log and resumes to Google Drive/Sheets."""
    if env_file:
        import os
        os.environ.setdefault("ENV_FILE", str(env_file))
        reload_settings()
        _configure_logging_from_settings()

    settings = get_settings()
    orchestrator = Orchestrator(settings)
    orchestrator.sync_to_google()
    click.echo("Google sync complete")


@cli.command()
@click.option("--env-file", type=click.Path(path_type=Path), default=None)
def email(env_file: Path | None):
    """Check Gmail/Outlook for recruiter updates and update the log."""
    if env_file:
        import os
        os.environ.setdefault("ENV_FILE", str(env_file))
        reload_settings()
        _configure_logging_from_settings()

    settings = get_settings()
    orchestrator = Orchestrator(settings)
    orchestrator.check_email_updates()
    click.echo("Email check complete")


@cli.command()
@click.option("--output", type=click.Path(path_type=Path), default=None)
@click.option("--env-file", type=click.Path(path_type=Path), default=None)
def intake(output: Path | None, env_file: Path | None):
    """Interactive wizard to create a unified profile.json."""
    if env_file:
        import os
        os.environ.setdefault("ENV_FILE", str(env_file))
        reload_settings()
        _configure_logging_from_settings()

    settings = get_settings()
    click.echo("\n=== Automated Job Application System — Profile Intake ===\n")

    name = click.prompt("Full name", default=settings.my_name or "Lokesh Ayiti")
    email = click.prompt("Email address", default=settings.my_email or "")
    phone = click.prompt("Phone number", default=settings.my_phone or "")
    linkedin = click.prompt("LinkedIn URL", default=settings.my_linkedin or "")
    location = click.prompt("Current location", default="United States")
    work_auth = click.prompt("Work authorization", default="US Citizen")

    target_roles = click.prompt(
        "Target job roles (comma-separated)",
        default="Data Analyst, Data Scientist, Business Analyst, AI Engineer, Data Engineer",
    )
    target_locations = click.prompt(
        "Target locations (comma-separated)", default="United States, Remote"
    )
    salary_floor = click.prompt("Minimum annual salary (USD)", default="90000", type=int)
    remote_pref = click.prompt("Remote preference (any/remote_only/hybrid/onsite)", default="any")
    relocate = click.confirm("Willing to relocate?", default=False)

    fab = click.prompt(
        "Fabrication tolerance (none | moderate | aggressive)",
        default=settings.fabrication_tolerance,
        type=click.Choice(["none", "moderate", "aggressive"], case_sensitive=False),
    ).lower()

    base_resume_dir = click.prompt(
        "Base resume directory", default=str(settings.base_resume_dir)
    )
    base_template = click.prompt(
        "Primary base resume template (DOCX path, relative to project root)",
        default="base resume/Resume AI Engineer.docx",
    )
    base_pdf = click.prompt(
        "Primary base resume PDF path", default="base resume/Resume AI Engineer.pdf"
    )
    cover_letter_dir = click.prompt(
        "Base cover letter directory", default="base cover letter"
    )

    greenhouse_default = "gradial, openai, anthropic"
    greenhouse_boards = click.prompt(
        "Greenhouse board tokens (comma-separated, optional)", default=greenhouse_default
    )

    skills_default = "SQL, Python, Power BI, Tableau, Excel, Azure, AWS, Databricks, PySpark, Pandas, NumPy"
    skills = click.prompt("Key skills (comma-separated)", default=skills_default)

    highlights_default = (
        "6+ years analyzing complex datasets and building dashboards for executive stakeholders; "
        "Designed and optimized ETL pipelines using SQL, Python, and Azure Data Factory; "
        "Built predictive models and statistical analyses to drive cost-saving decisions; "
        "Collaborated with cross-functional teams to translate business needs into data products"
    )
    highlights = click.prompt(
        "Experience highlights (semicolon-separated)", default=highlights_default
    )

    education_default = "Master of Science in Data Science, University of Illinois at Urbana-Champaign, 2021"
    education_input = click.prompt(
        "Education (degree, institution, year — semicolon-separated for multiple)",
        default=education_default,
    )

    def _split(input_str: str, sep: str = ",") -> list[str]:
        return [item.strip() for item in input_str.split(sep) if item.strip()]

    def _split_semicolon(input_str: str) -> list[str]:
        return [item.strip() for item in input_str.split(";") if item.strip()]

    education_entries = []
    for entry in _split_semicolon(education_input):
        parts = [p.strip() for p in entry.split(",")]
        education_entries.append(
            {
                "degree": parts[0] if len(parts) > 0 else entry,
                "institution": parts[1] if len(parts) > 1 else "",
                "year": parts[2] if len(parts) > 2 else "",
            }
        )

    profile = {
        "personal_info": {
            "name": name,
            "email": email,
            "phone": phone,
            "linkedin": linkedin,
            "location": location,
            "work_authorization": work_auth,
        },
        "preferences": {
            "target_roles": _split(target_roles),
            "target_locations": _split(target_locations),
            "salary_floor_usd": salary_floor,
            "remote_preference": remote_pref,
            "willing_to_relocate": relocate,
            "fabrication_tolerance": fab,
            "greenhouse_boards": _split(greenhouse_boards),
        },
        "assets": {
            "base_resume_dir": base_resume_dir,
            "base_resume_template": base_template,
            "base_resume_pdf": base_pdf,
            "base_cover_letter_dir": cover_letter_dir,
        },
        "skills": _split(skills),
        "experience_highlights": _split_semicolon(highlights),
        "education": education_entries,
    }

    output_path = output or settings.profile_json
    if output_path is None:
        output_path = Path("profile.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)

    click.echo(f"\nProfile written to {output_path}")
    click.echo("Run `python -m job_agent.cli show-config` to verify.")

@cli.command()
@click.option("--sources", default="governmentjobs,greenhouse", help="Comma-separated discovery sources")
@click.option("--time", "task_time", default="09:00", help="Daily run time (HH:MM, 24-hour)")
@click.option("--name", "task_name", default="JobAgentDaily", help="Windows Task Scheduler task name")
@click.option("--dry-run/--no-dry-run", default=False, help="Print command but do not create task")
@click.option("--env-file", type=click.Path(path_type=Path), default=None)
def schedule(sources: str, task_time: str, task_name: str, dry_run: bool, env_file: Path | None):
    """Create a daily scheduled task (Windows Task Scheduler or cron line)."""
    if env_file:
        import os
        os.environ.setdefault("ENV_FILE", str(env_file))
        reload_settings()
        _configure_logging_from_settings()

    settings = get_settings()
    project_root = settings.resume_dir.parent
    python = Path(sys.executable)
    # Prefer the venv interpreter if it exists.
    venv_python = project_root / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        python = venv_python

    source_list = ",".join(s.strip() for s in sources.split(",") if s.strip())
    pipeline_cmd = f'"{python}" -m job_agent.cli pipeline --sources {source_list}'
    if env_file:
        pipeline_cmd += f' --env-file "{env_file}"'

    # Windows Task Scheduler command.
    task_cmd = f'cd /d "{project_root}" && {pipeline_cmd}'

    click.echo(f"Daily pipeline command: {task_cmd}")
    click.echo(f"Scheduled time: {task_time}")

    if dry_run:
        click.echo("Dry-run: task not created. Run again without --dry-run to create it.")
        return

    if sys.platform == "win32":
        import subprocess

        schtasks_cmd = [
            "schtasks",
            "/create",
            "/f",
            "/tn",
            task_name,
            "/tr",
            task_cmd,
            "/sc",
            "daily",
            "/st",
            task_time,
            "/rl",
            "lowest",
        ]
        click.echo(f"Creating Windows Task Scheduler task '{task_name}'...")
        result = subprocess.run(schtasks_cmd, capture_output=True, text=True, shell=False)
        if result.returncode != 0:
            click.echo(f"Failed to create task: {result.stderr}")
            click.echo("You can create it manually with the command above.")
        else:
            click.echo(f"Task '{task_name}' created successfully.")
            click.echo(f"View it with: schtasks /query /tn {task_name}")
            click.echo(f"Remove with: python -m job_agent.cli unschedule --name {task_name}")
    else:
        cron_line = f"0 {task_time.split(':')[0]} * * * cd {project_root} && {pipeline_cmd}"
        click.echo("Add this cron entry to your crontab (e.g., crontab -e):")
        click.echo(cron_line)


@cli.command()
@click.option("--name", "task_name", default="JobAgentDaily", help="Windows Task Scheduler task name")
@click.option("--env-file", type=click.Path(path_type=Path), default=None)
def unschedule(task_name: str, env_file: Path | None):
    """Remove the scheduled Windows Task Scheduler task."""
    if env_file:
        import os
        os.environ.setdefault("ENV_FILE", str(env_file))
        reload_settings()
        _configure_logging_from_settings()

    if sys.platform != "win32":
        click.echo("unschedule is only implemented for Windows Task Scheduler.")
        click.echo("Remove the cron line you added manually.")
        return

    import subprocess

    result = subprocess.run(
        ["schtasks", "/delete", "/f", "/tn", task_name],
        capture_output=True,
        text=True,
        shell=False,
    )
    if result.returncode != 0:
        click.echo(f"Failed to remove task: {result.stderr}")
    else:
        click.echo(f"Task '{task_name}' removed.")


@cli.command("create-sample-jobs")
@click.option("--output", type=click.Path(path_type=Path), default=Path("data/sample_jobs.json"))
def create_sample_jobs(output: Path):
    """Generate a sample job list for testing."""
    output.parent.mkdir(parents=True, exist_ok=True)
    sample = [
        {
            "title": "Senior Software Engineer",
            "company": "Example Corp",
            "url": "https://boards.greenhouse.io/example/jobs/12345",
            "location": "Remote",
        },
        {
            "title": "Machine Learning Engineer",
            "company": "Another Corp",
            "url": "https://boards.greenhouse.io/another/jobs/67890",
            "location": "New York, NY",
        },
    ]
    with open(output, "w", encoding="utf-8") as f:
        json.dump(sample, f, indent=2)
    click.echo(f"Sample jobs written to {output}")


@cli.command()
@click.option("--sources", default="governmentjobs", help="Comma-separated discovery sources")
@click.option("--output", type=click.Path(path_type=Path), default=None)
@click.option("--env-file", type=click.Path(path_type=Path), default=None)
def discover(sources: str, output: Path | None, env_file: Path | None):
    """Discover jobs from configured sources and add them to the queue."""
    if env_file:
        import os
        os.environ.setdefault("ENV_FILE", str(env_file))
        reload_settings()
        _configure_logging_from_settings()

    settings = get_settings()
    orchestrator = Orchestrator(settings)
    source_list = [s.strip() for s in sources.split(",") if s.strip()]
    jobs = orchestrator.discover_jobs(sources=source_list)
    click.echo(f"Discovered {len(jobs)} jobs from {', '.join(source_list)}")
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w", encoding="utf-8") as f:
            json.dump([j.model_dump(mode="json") for j in jobs], f, indent=2)
        click.echo(f"Saved discovered jobs to {output}")


@cli.command()
@click.option("--job-id", required=True, help="Job ID to approve")
@click.option("--env-file", type=click.Path(path_type=Path), default=None)
def approve(job_id: str, env_file: Path | None):
    """Approve a queued job and consider auto-promoting its platform."""
    if env_file:
        import os
        os.environ.setdefault("ENV_FILE", str(env_file))
        reload_settings()
        _configure_logging_from_settings()

    settings = get_settings()
    orchestrator = Orchestrator(settings)
    job = orchestrator.approve_job(job_id)
    if job is None:
        raise click.ClickException(f"Job {job_id} not found")
    click.echo(f"Approved job {job_id} ({job.title} @ {job.company})")
    click.echo(f"Trusted platforms: {settings.trusted_platform_list}")


@cli.command()
@click.option("--sources", default="governmentjobs", help="Comma-separated discovery sources")
@click.option("--dry-run/--no-dry-run", default=None, help="Override ENABLE_AUTO_SUBMIT")
@click.option("--env-file", type=click.Path(path_type=Path), default=None)
def pipeline(sources: str, dry_run: bool | None, env_file: Path | None):
    """Run the full pipeline: discover -> score -> tailor -> submit (dry-run) -> email."""
    if env_file:
        import os
        os.environ.setdefault("ENV_FILE", str(env_file))
        reload_settings()
        _configure_logging_from_settings()

    settings = get_settings()
    if dry_run is not None:
        settings.enable_auto_submit = not dry_run

    orchestrator = Orchestrator(settings)
    source_list = [s.strip() for s in sources.split(",") if s.strip()]

    # 1. Discover
    discovered = orchestrator.discover_jobs(sources=source_list)
    click.echo(f"Discovered {len(discovered)} jobs")

    # 2. Score / filter
    scored = orchestrator.score_jobs(discovered)
    click.echo(f"Scored; {len(scored)} jobs passed threshold {settings.min_fit_score}")

    # 3. Submit (respects trusted-platform gating)
    results = orchestrator.run_sync(scored)

    # 4. Email check
    orchestrator.check_email_updates()

    submitted = sum(1 for r in results if r.status == ApplicationStatus.SUBMITTED)
    queued = sum(1 for r in results if r.status == ApplicationStatus.QUEUED)
    failed = sum(1 for r in results if r.status == ApplicationStatus.FAILED)
    human = sum(1 for r in results if r.status == ApplicationStatus.NEEDS_HUMAN)
    duplicate = sum(1 for r in results if r.status == ApplicationStatus.DUPLICATE)
    click.echo(f"Done — submitted: {submitted}, dry-run queued: {queued}, failed: {failed}, needs human: {human}, duplicate: {duplicate}")


@cli.command("show-config")
def show_config():
    """Print the current effective configuration."""
    _configure_logging_from_settings()
    settings = get_settings()
    click.echo(f"resume_dir: {settings.resume_dir}")
    click.echo(f"log_file: {settings.log_file}")
    click.echo(f"sqlite_db: {settings.sqlite_db}")
    click.echo(f"enable_auto_submit: {settings.enable_auto_submit}")
    click.echo(f"google_sync_enabled: {settings.google_sync_enabled}")
    click.echo(f"gmail_enabled: {settings.gmail_enabled}")
    click.echo(f"outlook_enabled: {settings.outlook_enabled}")
    click.echo(f"captcha_enabled: {settings.captcha_enabled}")
    click.echo(f"human_in_the_loop: {settings.human_in_the_loop}")
    click.echo(f"use_stealth: {settings.use_stealth}")
    click.echo(f"proxy_list: {settings.proxy_list}")
    click.echo(f"log_level: {settings.log_level}")
    click.echo(f"log_to_file: {settings.log_to_file}")
    click.echo(f"agent_log_file: {settings.agent_log_file}")
    click.echo(f"json_logs: {settings.json_logs}")
    click.echo(f"base_resume_dir: {settings.base_resume_dir}")
    click.echo(f"fabrication_tolerance: {settings.fabrication_tolerance}")
    click.echo(f"min_fit_score: {settings.min_fit_score}")
    click.echo(f"llm_fit_score_weight: {settings.llm_fit_score_weight}")
    click.echo(f"trusted_platforms: {settings.trusted_platform_list}")


@cli.command("generate-adapter")
@click.option("--snapshot", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--platform", default=None, help="Platform name (auto-detected if omitted)")
@click.option("--output", type=click.Path(path_type=Path), default=None)
@click.option("--env-file", type=click.Path(path_type=Path), default=None)
def generate_adapter(snapshot: Path, platform: str | None, output: Path | None, env_file: Path | None):
    """Generate a SiteAdapter draft from a Chrome-extension snapshot."""
    if env_file:
        import os

        os.environ.setdefault("ENV_FILE", str(env_file))
        reload_settings()
        _configure_logging_from_settings()

    from job_agent.sites.adapter_generator import generate_adapter as _generate
    from job_agent.sites.approval_registry import ApprovalRegistry

    settings = get_settings()
    registry = ApprovalRegistry(settings)
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    if platform:
        data["platform"] = platform

    result = _generate(data, settings)
    generated_code = result["code"]

    draft_path = registry.add_draft(result["platform"], generated_code, snapshot=str(snapshot))
    if output:
        output.write_text(generated_code, encoding="utf-8")
        click.echo(f"Draft code also written to {output}")

    click.echo(f"Generated adapter for {result['platform']}")
    click.echo(f"Draft saved to: {draft_path}")
    click.echo("Review the draft, then run:")
    click.echo(f"  python -m job_agent.cli approve-adapter --platform {result['platform']}")


@cli.command("approve-adapter")
@click.option("--platform", required=True, help="Platform name to approve")
@click.option("--env-file", type=click.Path(path_type=Path), default=None)
def approve_adapter(platform: str, env_file: Path | None):
    """Approve a generated adapter so it is used for autonomous submissions."""
    if env_file:
        import os

        os.environ.setdefault("ENV_FILE", str(env_file))
        reload_settings()
        _configure_logging_from_settings()

    from job_agent.sites.approval_registry import ApprovalRegistry

    settings = get_settings()
    registry = ApprovalRegistry(settings)
    approved_path = registry.approve(platform)
    click.echo(f"Approved {platform} adapter: {approved_path}")


@cli.command("reject-adapter")
@click.option("--platform", required=True, help="Platform name to reject")
@click.option("--env-file", type=click.Path(path_type=Path), default=None)
def reject_adapter(platform: str, env_file: Path | None):
    """Reject a generated adapter draft."""
    if env_file:
        import os

        os.environ.setdefault("ENV_FILE", str(env_file))
        reload_settings()
        _configure_logging_from_settings()

    from job_agent.sites.approval_registry import ApprovalRegistry

    settings = get_settings()
    registry = ApprovalRegistry(settings)
    registry.reject(platform)
    click.echo(f"Rejected {platform} adapter drafts")


if __name__ == "__main__":
    cli()
