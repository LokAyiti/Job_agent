"""Tests for the auto-generated SiteAdapter pipeline."""
import json
from pathlib import Path

import pytest

from job_agent.sites.adapter_generator import AdapterGenerator, generate_adapter
from job_agent.sites.approval_registry import ApprovalRegistry


def test_adapter_generator_produces_heuristic_code(tmp_path):
    generator = AdapterGenerator()
    snapshot = {
        "url": "https://apply.example.com/job/123",
        "platform": "example",
        "fields": [
            {"tag": "input", "type": "text", "name": "first_name", "id": "firstName", "label": "First Name"},
            {"tag": "input", "type": "text", "name": "last_name", "id": "lastName", "label": "Last Name"},
            {"tag": "input", "type": "email", "name": "email", "id": "email", "label": "Email"},
            {"tag": "input", "type": "file", "name": "resume", "id": "resume", "label": "Resume"},
            {"tag": "button", "type": "submit", "name": "submit", "id": "submit", "label": "Submit"},
        ],
    }

    result = generator.generate_from_snapshot(snapshot)
    code = result["code"]

    assert "ExampleAdapter" in code
    assert "class ExampleAdapter" in code
    assert "first_name" in code or "firstName" in code
    assert "resume" in code


def test_adapter_generator_returns_empty_for_no_fields():
    generator = AdapterGenerator()
    result = generator.generate_from_snapshot({"url": "https://example.com/", "fields": []})
    assert "ExampleComAdapter" in result["code"] or "Adapter" in result["code"]


def test_approval_registry_lifecycle(tmp_path):
    settings = {
        "adapter_drafts_dir": tmp_path / "drafts",
        "adapter_registry_file": tmp_path / "registry.json",
        "_env_file": None,
    }
    from job_agent.config import Settings

    registry = ApprovalRegistry(Settings(**settings))
    code = "class OracleAdapter(SiteAdapter):\n    pass\n"
    draft_path = registry.add_draft("oracle", code)

    assert draft_path.exists()
    assert registry.list_drafts("oracle")

    approved_path = registry.approve("oracle")
    assert approved_path.exists()
    assert "oracle" in registry.approved_platforms()

    registry.reject("oracle")
    assert "oracle" not in registry.approved_platforms()


def test_approval_registry_unknown_approval_raises():
    from job_agent.config import Settings

    registry = ApprovalRegistry(Settings(_env_file=None))
    with pytest.raises(ValueError):
        registry.approve("nonexistent_platform")
