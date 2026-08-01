"""Tests for Track D end-to-end automation loop."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from job_agent.agents.scoring_agent import ScoringAgent
from job_agent.agents.tailoring_agent import TailoringAgent
from job_agent.config import Settings
from job_agent.discovery.company_pages import CompanyPagesDiscovery
from job_agent.discovery.greenhouse import GreenhouseDiscovery
from job_agent.discovery.lever import LeverDiscovery
from job_agent.discovery.registry import DiscoveryRegistry
from job_agent.models import ApplicationStatus, JobApplication


def test_settings_load_profile(tmp_path):
    profile = {
        "personal_info": {"name": "Test User", "email": "test@example.com"},
        "preferences": {"target_roles": ["Data Analyst"]},
        "skills": ["SQL"],
    }
    profile_path = tmp_path / "profile.json"
    profile_path.write_text('{"personal_info": {"name": "Test User", "email": "test@example.com"}, "preferences": {"target_roles": ["Data Analyst"]}, "skills": ["SQL"]}')

    settings = Settings(profile_json=profile_path, _env_file=None)
    loaded = settings.load_profile()
    assert loaded["personal_info"]["name"] == "Test User"
    assert loaded["skills"] == ["SQL"]


def test_settings_load_profile_fallback():
    settings = Settings(
        my_name="Fallback Name",
        profile_json=Path("/does/not/exist.json"),
        _env_file=None,
    )
    profile = settings.load_profile()
    assert profile["personal_info"]["name"] == "Fallback Name"


def test_settings_trusted_platforms():
    settings = Settings(trusted_platforms="governmentjobs, greenhouse", _env_file=None)
    assert settings.trusted_platform_list == ["governmentjobs", "greenhouse"]


def test_tailoring_agent_calls_bridge(tmp_path):
    settings = Settings(
        profile_json=tmp_path / "profile.json",
        resume_dir=tmp_path / "resume",
        _env_file=None,
    )
    (tmp_path / "profile.json").write_text('{"personal_info": {"name": "Test"}}')
    settings.resume_dir.mkdir(parents=True, exist_ok=True)

    agent = TailoringAgent(settings)
    fake_pdf = settings.resume_dir / "JD_Test_Test_20260801.pdf"
    fake_pdf.write_text("PDF")

    job = JobApplication(title="Data Analyst", company="Test", url="https://example.com")

    with patch.object(agent, "_run_bridge") as mock_bridge:
        mock_bridge.return_value = {
            "ok": True,
            "resume_pdf_path": str(fake_pdf),
        }
        result = agent.tailor_for_job(job)
        assert result == fake_pdf
        mock_bridge.assert_called_once()


def test_tailoring_agent_returns_none_on_failure(tmp_path):
    settings = Settings(
        profile_json=tmp_path / "profile.json",
        _env_file=None,
    )
    (tmp_path / "profile.json").write_text('{"personal_info": {"name": "Test"}}')
    agent = TailoringAgent(settings)
    job = JobApplication(title="Data Analyst", company="Test", url="https://example.com")

    with patch.object(agent, "_run_bridge") as mock_bridge:
        mock_bridge.return_value = {"ok": False, "error": "LLM failed"}
        assert agent.tailor_for_job(job) is None


def test_scoring_agent_parses_llm_output():
    settings = Settings(_env_file=None, min_fit_score=60, llm_fit_score_weight=1.0)
    agent = ScoringAgent(settings)
    job = JobApplication(title="Data Analyst", company="Test", url="https://example.com")

    with patch.object(agent, "_llm_chat") as mock_chat:
        mock_chat.return_value = '{"score": 85, "reason": "Strong SQL match"}'
        score, reason = agent.score(job)
        assert score == 85
        assert "Strong SQL match" in reason


def test_scoring_agent_parses_markdown_fenced_output():
    settings = Settings(_env_file=None, llm_fit_score_weight=1.0)
    agent = ScoringAgent(settings)
    job = JobApplication(title="Data Analyst", company="Test", url="https://example.com")

    with patch.object(agent, "_llm_chat") as mock_chat:
        mock_chat.return_value = "```json\n{\"score\": 45, \"reason\": \"Weak match\"}\n```"
        score, reason = agent.score(job)
        assert score == 45
        assert "Weak match" in reason


def test_scoring_agent_defaults_to_zero_on_bad_output():
    settings = Settings(_env_file=None, llm_fit_score_weight=1.0)
    agent = ScoringAgent(settings)
    job = JobApplication(title="Data Analyst", company="Test", url="https://example.com")

    with patch.object(agent, "_llm_chat") as mock_chat:
        mock_chat.return_value = "not json"
        score, reason = agent.score(job)
        assert score == 0
        assert "parse" in reason.lower()


def test_keyword_scorer_computes_coverage():
    profile = {
        "skills": ["SQL", "Python"],
        "experience_highlights": ["Built dashboards in Tableau"],
        "preferences": {"target_roles": ["Data Analyst"]},
    }
    from job_agent.agents.scoring_agent import KeywordScorer

    scorer = KeywordScorer(profile)
    job = JobApplication(
        title="Data Analyst",
        company="Test",
        url="https://example.com",
        description="Looking for SQL and Python skills. Tableau experience preferred.",
    )
    score, reason = scorer.score(job)
    assert score > 0
    assert "SQL" in reason or "Python" in reason or "overlap" in reason


def test_scoring_agent_combines_llm_and_keyword():
    settings = Settings(_env_file=None, llm_fit_score_weight=0.7)
    agent = ScoringAgent(settings)
    job = JobApplication(
        title="Data Analyst",
        company="Test",
        url="https://example.com",
        description="Must know SQL, Python, and Excel.",
    )

    with patch.object(agent, "_llm_chat") as mock_chat:
        mock_chat.return_value = '{"score": 80, "reason": "Strong match"}'
        score, reason = agent.score(job)
        assert 0 <= score <= 100
        assert "LLM" in reason and "keyword" in reason



import pytest
import requests

def test_greenhouse_discovery_filters_by_role(tmp_path):
    discovery = GreenhouseDiscovery(board_tokens=["gradial"])

    api_response = {
        "jobs": [
            {
                "title": "Applied AI Engineer",
                "absolute_url": "https://boards.greenhouse.io/gradial/jobs/1",
                "location": {"name": "Remote"},
                "content": "Build AI features.",
            },
            {
                "title": "Sales Rep",
                "absolute_url": "https://boards.greenhouse.io/gradial/jobs/2",
                "location": {"name": "Remote"},
                "content": "Sell things.",
            },
        ]
    }

    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock()
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = api_response

        import asyncio

        jobs = asyncio.run(
            discovery.discover(
                {
                    "preferences": {
                        "target_roles": ["AI Engineer", "Data Analyst"],
                        "greenhouse_boards": ["gradial"],
                    }
                }
            )
        )

    assert len(jobs) == 1
    assert jobs[0].title == "Applied AI Engineer"
    assert jobs[0].platform == "greenhouse"


def test_discovery_registry_lists_sources():
    registry = DiscoveryRegistry()
    assert "governmentjobs" in registry.list_sources()
    assert "greenhouse" in registry.list_sources()
    assert "lever" in registry.list_sources()
    assert "company_pages" in registry.list_sources()
    assert "linkedin" in registry.list_sources()
    assert "indeed" in registry.list_sources()


def test_cli_intake_creates_profile(tmp_path):
    from click.testing import CliRunner
    from job_agent.cli import cli

    runner = CliRunner()
    output = tmp_path / "profile.json"
    result = runner.invoke(
        cli,
        ["intake", "--output", str(output)],
        input="\n".join(
            [
                "Test User",  # name
                "test@example.com",  # email
                "+1-555-123-4567",  # phone
                "https://linkedin.com/in/test",  # linkedin
                "United States",  # location
                "US Citizen",  # work auth
                "Data Analyst, Data Scientist",  # target roles
                "United States, Remote",  # target locations
                "100000",  # salary floor
                "any",  # remote preference
                "n",  # relocate
                "moderate",  # fabrication tolerance
                str(tmp_path / "base resume"),  # base resume dir
                "base resume/Resume.docx",  # base template
                "base resume/Resume.pdf",  # base pdf
                "base cover letter",  # cover letter dir
                "gradial, openai",  # greenhouse boards
                "SQL, Python",  # skills
                "Built dashboards; Designed pipelines",  # highlights
                "MS, Example University, 2021",  # education
            ]
        ),
    )
    assert result.exit_code == 0, result.output
    assert output.exists()
    data = json.loads(output.read_text())
    assert data["personal_info"]["name"] == "Test User"
    assert data["preferences"]["fabrication_tolerance"] == "moderate"
    assert data["assets"]["base_resume_dir"] == str(tmp_path / "base resume")


def test_cli_schedule_dry_run(tmp_path):
    from click.testing import CliRunner
    from job_agent.cli import cli

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "schedule",
            "--dry-run",
            "--sources",
            "greenhouse",
            "--time",
            "10:00",
            "--name",
            "TestJobAgent",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Daily pipeline command" in result.output
    assert "Dry-run" in result.output


def test_cli_unschedule_with_mock_subprocess():
    from click.testing import CliRunner
    from job_agent.cli import cli
    from unittest.mock import patch, MagicMock

    runner = CliRunner()
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stderr = ""
    with patch("subprocess.run", return_value=fake_result) as mock_run:
        result = runner.invoke(cli, ["unschedule", "--name", "TestJobAgent"])
        assert result.exit_code == 0, result.output
        assert "removed" in result.output.lower()
        mock_run.assert_called_once()


def test_discovery_registry_unknown_source_raises():
    registry = DiscoveryRegistry()
    with pytest.raises(ValueError, match="Unknown discovery source"):
        registry.get("unknown", {})


def test_lever_discovery_filters_by_role():
    discovery = LeverDiscovery(site_slugs=["exampleco"])
    api_response = [
        {
            "text": "Senior Data Analyst",
            "hostedUrl": "https://jobs.lever.co/exampleco/abc123",
            "categories": {"location": "Remote", "commitment": "Full-time"},
            "description": "<p>We need a data analyst.</p>",
            "lists": [{"text": "Requirements", "content": "<ul><li>SQL</li><li>Python</li></ul>"}],
        },
        {
            "text": "Sales Development Representative",
            "hostedUrl": "https://jobs.lever.co/exampleco/def456",
            "categories": {"location": "Remote"},
            "description": "Sell our product.",
        },
    ]

    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock()
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = api_response

        import asyncio

        jobs = asyncio.run(
            discovery.discover(
                {
                    "preferences": {
                        "target_roles": ["Data Analyst", "Data Scientist"],
                        "lever_sites": ["exampleco"],
                    }
                }
            )
        )

    assert len(jobs) == 1
    assert jobs[0].title == "Senior Data Analyst"
    assert jobs[0].platform == "lever"
    assert "SQL" in (jobs[0].description or "")


def test_company_pages_discovery_extracts_job_links():
    html = """
    <html>
      <body>
        <a href="/jobs/123">Senior Data Analyst</a>
        <a href="/careers/456">Software Engineer</a>
        <a href="/privacy">Privacy Policy</a>
        <a href="https://boards.greenhouse.io/example/jobs/789">Product Manager</a>
      </body>
    </html>
    """
    discovery = CompanyPagesDiscovery(pages=["https://example.com/careers"])

    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock()
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.text = html

        import asyncio

        jobs = asyncio.run(
            discovery.discover(
                {
                    "preferences": {
                        "target_roles": ["Data Analyst"],
                        "company_career_pages": ["https://example.com/careers"],
                    }
                }
            )
        )

    assert len(jobs) == 1
    assert jobs[0].title == "Senior Data Analyst"
    assert jobs[0].platform is None


def test_linkedin_discovery_disabled_by_default():
    from job_agent.discovery.linkedin import LinkedInDiscovery

    discovery = LinkedInDiscovery()
    import asyncio

    jobs = asyncio.run(discovery.discover({"preferences": {}}))
    assert jobs == []


def test_indeed_discovery_disabled_by_default():
    from job_agent.discovery.indeed import IndeedDiscovery

    discovery = IndeedDiscovery()
    import asyncio

    jobs = asyncio.run(discovery.discover({"preferences": {}}))
    assert jobs == []


def test_linkedin_discovery_parses_html_when_enabled():
    from job_agent.discovery.linkedin import LinkedInDiscovery

    html_response = """
    <html>
      <body>
        <div class="base-card">
          <a class="base-card__full-link" href="/jobs/view/1"></a>
          <h3 class="base-search-card__title">Senior Data Analyst</h3>
          <h4 class="base-search-card__subtitle">Acme Corp</h4>
          <span class="job-search-card__location">Austin, TX</span>
        </div>
        <div class="base-card">
          <a class="base-card__full-link" href="/jobs/view/2"></a>
          <h3 class="base-search-card__title">Sales Representative</h3>
          <h4 class="base-search-card__subtitle">Other Corp</h4>
          <span class="job-search-card__location">Remote</span>
        </div>
      </body>
    </html>
    """

    discovery = LinkedInDiscovery(max_results=10)
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html_response
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        import asyncio

        jobs = asyncio.run(
            discovery.discover(
                {
                    "preferences": {
                        "enable_linkedin_discovery": True,
                        "target_roles": ["Data Analyst"],
                        "target_locations": ["United States"],
                    }
                }
            )
        )

    assert len(jobs) == 1
    assert jobs[0].title == "Senior Data Analyst"
    assert jobs[0].company == "Acme Corp"
    assert jobs[0].platform == "linkedin"
    assert "linkedin.com" in jobs[0].url


def test_indeed_discovery_parses_rss_when_enabled():
    from job_agent.discovery.indeed import IndeedDiscovery

    rss_response = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <item>
          <title>Senior Data Analyst</title>
          <link>https://www.indeed.com/viewjob?jk=abc123</link>
          <description>Acme Corp&lt;br&gt;Austin, TX</description>
        </item>
        <item>
          <title>Sales Representative</title>
          <link>https://www.indeed.com/viewjob?jk=def456</link>
          <description>Other Corp&lt;br&gt;Remote</description>
        </item>
      </channel>
    </rss>
    """

    discovery = IndeedDiscovery(max_results=10)
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = rss_response
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        import asyncio

        jobs = asyncio.run(
            discovery.discover(
                {
                    "preferences": {
                        "enable_indeed_discovery": True,
                        "target_roles": ["Data Analyst"],
                        "target_locations": ["United States"],
                    }
                }
            )
        )

    assert len(jobs) == 1
    assert jobs[0].title == "Senior Data Analyst"
    assert jobs[0].company == "Acme Corp"
    assert jobs[0].platform == "indeed"
    assert "indeed.com" in jobs[0].url

