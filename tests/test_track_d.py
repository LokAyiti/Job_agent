"""Tests for Track D end-to-end automation loop."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from job_agent.agents.scoring_agent import ScoringAgent
from job_agent.agents.tailoring_agent import TailoringAgent
from job_agent.config import Settings
from job_agent.discovery.greenhouse import GreenhouseDiscovery
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
    settings = Settings(_env_file=None, min_fit_score=60)
    agent = ScoringAgent(settings)
    job = JobApplication(title="Data Analyst", company="Test", url="https://example.com")

    with patch.object(agent, "_llm_chat") as mock_chat:
        mock_chat.return_value = '{"score": 85, "reason": "Strong SQL match"}'
        score, reason = agent.score(job)
        assert score == 85
        assert "Strong SQL match" in reason


def test_scoring_agent_parses_markdown_fenced_output():
    settings = Settings(_env_file=None)
    agent = ScoringAgent(settings)
    job = JobApplication(title="Data Analyst", company="Test", url="https://example.com")

    with patch.object(agent, "_llm_chat") as mock_chat:
        mock_chat.return_value = "```json\n{\"score\": 45, \"reason\": \"Weak match\"}\n```"
        score, reason = agent.score(job)
        assert score == 45
        assert "Weak match" in reason


def test_scoring_agent_defaults_to_zero_on_bad_output():
    settings = Settings(_env_file=None)
    agent = ScoringAgent(settings)
    job = JobApplication(title="Data Analyst", company="Test", url="https://example.com")

    with patch.object(agent, "_llm_chat") as mock_chat:
        mock_chat.return_value = "not json"
        score, reason = agent.score(job)
        assert score == 0
        assert "parse" in reason.lower()


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


def test_discovery_registry_unknown_source_raises():
    registry = DiscoveryRegistry()
    with pytest.raises(ValueError, match="Unknown discovery source"):
        registry.get("unknown", {})
