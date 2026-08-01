"""Tests for the email agents (Gmail and Outlook)."""
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from job_agent.agents.email_agent import (
    DraftResponse,
    EmailAgent,
    GmailAgent,
    OutlookAgent,
)
from job_agent.config import Settings
from job_agent.models import ApplicationStatus, JobApplication


@pytest.fixture
def outlook_settings(temp_settings) -> Settings:
    temp_settings.outlook_client_id = "test-client-id"
    temp_settings.outlook_use_device_code = False
    temp_settings.outlook_authority = "https://login.microsoftonline.com/common"
    return temp_settings


class FakeToken:
    def __init__(self, access_token="fake-token"):
        self.access_token = access_token


def test_status_inference_and_job_matching():
    settings = Settings()
    agent = EmailAgent(settings)

    job = JobApplication(title="Software Engineer", company="Acme", url="https://example.com/1")
    assert agent._infer_status("Interview invitation", "phone screen") == ApplicationStatus.RESPONDED
    assert agent._infer_status("Position closed", "filled") == ApplicationStatus.FAILED
    assert agent._infer_status("Please verify", "captcha") == ApplicationStatus.NEEDS_HUMAN

    assert agent._matches_job(job, "Re: Software Engineer at Acme", "Hi", "recruiter@acme.com") is True
    assert agent._matches_job(job, "Random email", "Hi", "recruiter@other.com") is False


def test_reply_body_human_tone(outlook_settings):
    agent = OutlookAgent(outlook_settings)
    job = JobApplication(title="Software Engineer", company="Acme", url="https://example.com/1")

    body = agent._build_reply_body("availability", job, "Jane")
    assert "Hi Jane," in body
    assert "Software Engineer" in body
    assert "Acme" in body
    assert "9 AM" in body
    assert "Best," in body

    body = agent._build_reply_body("thank_you", job, "Jane")
    assert "thank you so much" in body.lower()
    assert "Best regards" in body

    body = agent._build_reply_body("follow_up", job, "Jane")
    assert "follow up" in body.lower()

    body = agent._build_reply_body("general", job, "Jane")
    assert "thank you for reaching out" in body.lower()


def test_classify_scenario(outlook_settings):
    agent = OutlookAgent(outlook_settings)
    assert agent._classify_scenario("Interview Request", "schedule a phone screen") == "availability"
    assert agent._classify_scenario("Thanks for speaking", "great conversation") == "thank_you"
    assert agent._classify_scenario("Follow up on application", "checking in") == "follow_up"
    assert agent._classify_scenario("Recruiter note", "please see attached") == "general"


def test_outlook_agent_enabled_only_when_client_id_present(outlook_settings):
    assert OutlookAgent(outlook_settings).enabled is True
    outlook_settings.outlook_client_id = None
    assert OutlookAgent(outlook_settings).enabled is False


@pytest.mark.asyncio
async def test_outlook_check_for_updates_with_mock_graph(outlook_settings, monkeypatch):
    agent = OutlookAgent(outlook_settings)

    received = datetime.utcnow() - timedelta(days=1)
    received_iso = received.strftime("%Y-%m-%dT%H:%M:%S") + "Z"

    fake_messages = {
        "value": [
            {
                "subject": "Interview Invitation - Software Engineer at Acme",
                "from": {"emailAddress": {"name": "Jane Doe", "address": "jane@acme.com"}},
                "bodyPreview": "We would like to schedule a phone screen.",
                "receivedDateTime": received_iso,
            }
        ]
    }

    def fake_graph_get(endpoint, params=None):
        assert endpoint == "/me/messages"
        return fake_messages

    monkeypatch.setattr(agent, "_graph_get", fake_graph_get)

    job = JobApplication(title="Software Engineer", company="Acme", url="https://example.com/1")
    updates = agent.check_for_updates([job])
    assert len(updates) == 1
    assert updates[0].new_status == ApplicationStatus.RESPONDED
    assert "Outlook:" in updates[0].reason


@pytest.mark.asyncio
async def test_outlook_create_draft_with_mock_graph(outlook_settings, monkeypatch):
    agent = OutlookAgent(outlook_settings)

    created_payloads = []

    def fake_graph_post(endpoint, payload):
        created_payloads.append((endpoint, payload))
        return {"id": "draft-123"}

    monkeypatch.setattr(agent, "_graph_post", fake_graph_post)
    monkeypatch.setattr(
        agent,
        "_acquire_token",
        lambda: {"access_token": "fake"},
    )

    job = JobApplication(title="Software Engineer", company="Acme", url="https://example.com/1")
    draft = agent.create_draft(job, "Jane Doe <jane@acme.com>", "Re: Interview", scenario="availability")
    assert draft is not None
    assert draft.provider == "outlook"
    assert draft.message_id == "draft-123"
    assert "Hi Jane," in draft.body

    assert len(created_payloads) == 1
    endpoint, payload = created_payloads[0]
    assert endpoint == "/me/messages"
    assert payload["isDraft"] is True
    assert payload["subject"] == "Re: Interview"
    assert payload["toRecipients"][0]["emailAddress"]["address"] == "jane@acme.com"


def test_email_agent_wrapper_uses_both_providers(monkeypatch, temp_settings):
    """EmailAgent wrapper should aggregate status updates from Gmail and Outlook."""
    temp_settings.outlook_client_id = "test-client-id"
    wrapper = EmailAgent(temp_settings)

    # Patch the inner agents.
    wrapper.gmail.check_for_updates = MagicMock(return_value=[])
    wrapper.outlook.check_for_updates = MagicMock(
        return_value=[
            type("Update", (), {
                "job_id": "job-1",
                "new_status": ApplicationStatus.RESPONDED,
                "reason": "Outlook interview",
            })()
        ]
    )

    updates = wrapper.check_for_updates([JobApplication(title="T", company="C", url="https://example.com/1")])
    assert len(updates) == 1
    assert updates[0].new_status == ApplicationStatus.RESPONDED


def test_gmail_create_draft(monkeypatch, temp_settings):
    """Gmail create_draft should build a base64 message and call the drafts API."""
    temp_settings.gmail_sender_email = "me@example.com"
    temp_settings.gmail_credentials_json = Path("/fake/path.json")

    agent = GmailAgent(temp_settings)
    created_drafts = []

    class DraftsResource:
        def create(self, userId, body):
            created_drafts.append(body)
            return self

        def execute(self):
            return {"id": "draft-456"}

    class UsersResource:
        def drafts(self):
            return DraftsResource()

    class FakeService:
        def users(self):
            return UsersResource()

    monkeypatch.setattr(agent, "_service_cache", FakeService())

    job = JobApplication(title="Software Engineer", company="Acme", url="https://example.com/1")
    draft = agent.create_draft(job, "jane@acme.com", "Re: Interview", scenario="thank_you")
    assert draft is not None
    assert draft.provider == "gmail"
    assert draft.message_id == "draft-456"
    assert "thank you" in draft.body.lower()

    assert len(created_drafts) == 1
    raw_message = created_drafts[0]["message"]["raw"]
    import base64
    decoded = base64.urlsafe_b64decode(raw_message).decode("utf-8")
    assert "From: me@example.com" in decoded
    assert "To: jane@acme.com" in decoded


def test_email_agent_disabled_when_no_providers_configured(monkeypatch):
    monkeypatch.setenv("OUTLOOK_CLIENT_ID", "")
    monkeypatch.setenv("GMAIL_CREDENTIALS_JSON", "")
    monkeypatch.setenv("GMAIL_SENDER_EMAIL", "")
    settings = Settings()
    assert EmailAgent(settings).enabled is False


def test_outlook_agent_disabled_when_msal_missing(monkeypatch, temp_settings):
    temp_settings.outlook_client_id = "test-client-id"
    monkeypatch.setattr("job_agent.agents.email_agent._MSAL_AVAILABLE", False)
    assert OutlookAgent(temp_settings).enabled is False


def test_gmail_agent_disabled_when_google_missing(monkeypatch, temp_settings):
    temp_settings.gmail_sender_email = "me@example.com"
    temp_settings.gmail_credentials_json = Path("/fake/path.json")
    monkeypatch.setattr("job_agent.agents.email_agent._GMAIL_AVAILABLE", False)
    assert GmailAgent(temp_settings).enabled is False


def test_extract_sender_name():
    settings = Settings()
    agent = EmailAgent(settings)
    assert agent._extract_sender_name("Jane Doe <jane@acme.com>") == "Jane"
    assert agent._extract_sender_name("jane@acme.com") == ""
