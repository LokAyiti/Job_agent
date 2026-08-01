"""Command-line interface for the job-application orchestrator."""
import json
from pathlib import Path

import click
from loguru import logger

from job_agent.agents.orchestrator import Orchestrator
from job_agent.config import Settings, get_settings, reload_settings
from job_agent.models import ApplicationStatus, JobApplication


@click.group()
def cli():
    """Automated job application system — Track B CLI."""
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

    settings = get_settings()
    orchestrator = Orchestrator(settings)
    orchestrator.sync_to_google()
    click.echo("Google sync complete")


@cli.command()
@click.option("--env-file", type=click.Path(path_type=Path), default=None)
def email(env_file: Path | None):
    """Check Gmail for recruiter updates and update the log."""
    if env_file:
        import os
        os.environ.setdefault("ENV_FILE", str(env_file))
        reload_settings()

    settings = get_settings()
    orchestrator = Orchestrator(settings)
    orchestrator.check_email_updates()
    click.echo("Email check complete")


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


@cli.command("show-config")
def show_config():
    """Print the current effective configuration."""
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


if __name__ == "__main__":
    cli()
