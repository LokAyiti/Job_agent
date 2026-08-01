"""Email/Recruiter Communication Agent.

Monitors Gmail and Outlook for recruiter emails, infers job-application status
updates, and creates draft replies in a human tone for your review. When
credentials are unavailable the agent degrades to a no-op so the pipeline can
still run.
"""
import base64
import json
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Iterable

from loguru import logger

from job_agent.agents.base_agent import BaseAgent
from job_agent.config import Settings
from job_agent.models import ApplicationStatus, JobApplication

try:
    from google.oauth2.credentials import Credentials as OAuthCredentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    _GMAIL_AVAILABLE = True
except ImportError:
    _GMAIL_AVAILABLE = False

try:
    import msal
    _MSAL_AVAILABLE = True
except ImportError:
    _MSAL_AVAILABLE = False


class EmailStatusUpdate:
    def __init__(self, job_id: str, new_status: ApplicationStatus, reason: str):
        self.job_id = job_id
        self.new_status = new_status
        self.reason = reason


class DraftResponse:
    """A proposed email reply saved as a draft for human review."""

    def __init__(
        self,
        job_id: str,
        to: str,
        subject: str,
        body: str,
        provider: str,
        message_id: str | None = None,
    ):
        self.job_id = job_id
        self.to = to
        self.subject = subject
        self.body = body
        self.provider = provider
        self.message_id = message_id


class BaseEmailAgent(ABC, BaseAgent):
    """Shared status-inference logic for Gmail and Outlook."""

    # Simple keyword-based status inference.
    STATUS_KEYWORDS = {
        ApplicationStatus.NEEDS_HUMAN: ["captcha", "verify", "confirm your identity"],
        ApplicationStatus.RESPONDED: ["interview", "phone screen", "recruiter", "hiring manager"],
        ApplicationStatus.FAILED: ["position closed", "no longer available", "filled", "cancelled"],
    }

    def __init__(self, settings: Settings):
        super().__init__(settings)

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """Whether this provider is configured."""

    @abstractmethod
    def check_for_updates(self, jobs: Iterable[JobApplication]) -> list[EmailStatusUpdate]:
        """Return status updates inferred from emails."""

    def _normalize(self, text: str) -> str:
        return text.lower().strip()

    def _infer_status(self, subject: str, snippet: str) -> ApplicationStatus | None:
        combined = self._normalize(subject + " " + snippet)
        for status, keywords in self.STATUS_KEYWORDS.items():
            if any(kw in combined for kw in keywords):
                return status
        return None

    def _matches_job(self, job: JobApplication, subject: str, snippet: str, sender: str) -> bool:
        combined = self._normalize(subject + " " + snippet + " " + sender)
        company = self._normalize(job.company)
        title = self._normalize(job.title)
        return company in combined or title in combined

    def _build_reply_body(self, scenario: str, job: JobApplication, sender_name: str = "") -> str:
        """Generate a human-tone draft reply body.

        Scenarios: follow_up, thank_you, availability, general.
        """
        first_name = self.settings.my_name.split()[0] if self.settings.my_name else "I"
        greeting = f"Hi {sender_name}," if sender_name else "Hi there,"

        if scenario == "follow_up":
            body = (
                f"{greeting}\n\n"
                f"I hope you're doing well. I wanted to follow up on my application for the "
                f"{job.title} role at {job.company}. I'm still very interested in the opportunity and "
                f"would love to learn more about the next steps in the process.\n\n"
                f"Please let me know if there's anything else I can provide on my end.\n\n"
                f"Best,\n{first_name}"
            )
        elif scenario == "thank_you":
            body = (
                f"{greeting}\n\n"
                f"Thank you so much for taking the time to speak with me about the {job.title} "
                f"position at {job.company}. I really enjoyed our conversation and learning more "
                f"about the team and the work you're doing.\n\n"
                f"I'm very excited about the opportunity and look forward to hearing about next steps.\n\n"
                f"Best regards,\n{first_name}"
            )
        elif scenario == "availability":
            body = (
                f"{greeting}\n\n"
                f"Thank you for reaching out about the {job.title} role at {job.company}. I'm "
                f"excited to move forward and would be happy to schedule a time to talk.\n\n"
                f"I'm generally available during standard business hours (9 AM – 5 PM ET) on weekdays, "
                f"and I can be flexible if another time works better for the team.\n\n"
                f"Please let me know what works best for you.\n\n"
                f"Best,\n{first_name}"
            )
        else:  # general
            body = (
                f"{greeting}\n\n"
                f"Thank you for reaching out regarding the {job.title} role at {job.company}. "
                f"I really appreciate the update and am happy to provide any additional information "
                f"you might need.\n\n"
                f"Looking forward to hearing from you.\n\n"
                f"Best,\n{first_name}"
            )
        return body

    def _classify_scenario(self, subject: str, snippet: str) -> str:
        text = self._normalize(subject + " " + snippet)
        if any(kw in text for kw in ["interview", "phone screen", "schedule", "time to chat"]):
            return "availability"
        if any(kw in text for kw in ["thank you", "thanks for speaking", "great speaking"]):
            return "thank_you"
        if any(kw in text for kw in ["follow up", "checking in", "status", "update"]):
            return "follow_up"
        return "general"

    def _extract_sender_name(self, sender: str) -> str:
        """Extract a first name from 'Name <email@example.com>'.

        Returns an empty string if the input is just an email address, because
        'Hi jane@example.com' does not sound human.
        """
        if "<" in sender and ">" in sender:
            name = sender.split("<")[0].strip()
        else:
            candidate = sender.strip()
            if "@" in candidate:
                return ""
            name = candidate
        return name.split()[0] if name else ""


class GmailAgent(BaseEmailAgent):
    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"]

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self._service_cache = None

    @property
    def enabled(self) -> bool:
        return _GMAIL_AVAILABLE and self.settings.gmail_enabled

    def _get_service(self):
        if self._service_cache is not None:
            return self._service_cache
        if not self.enabled:
            return None
        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.settings.gmail_credentials_json), self.SCOPES
            )
            creds = flow.run_local_server(port=0)
            self._service_cache = build("gmail", "v1", credentials=creds)
            return self._service_cache
        except Exception as exc:
            logger.warning(f"Gmail API init failed: {exc}")
            return None

    def check_for_updates(self, jobs: Iterable[JobApplication]) -> list[EmailStatusUpdate]:
        service = self._get_service()
        if service is None:
            logger.info("Gmail integration not enabled; skipping email checks")
            return []

        updates: list[EmailStatusUpdate] = []
        after_date = (datetime.now() - timedelta(days=7)).strftime("%Y/%m/%d")
        query = f"from:({self.settings.gmail_sender_email}) OR after:{after_date}"

        try:
            results = service.users().messages().list(userId="me", q=query, maxResults=50).execute()
            messages = results.get("messages", [])
        except Exception as exc:
            logger.warning(f"Gmail list query failed: {exc}")
            return []

        for msg_meta in messages:
            try:
                msg = service.users().messages().get(userId="me", id=msg_meta["id"], format="metadata").execute()
                headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
                subject = headers.get("subject", "")
                sender = headers.get("from", "")
                snippet = msg.get("snippet", "")
                inferred = self._infer_status(subject, snippet)
                if inferred is None:
                    continue

                for job in jobs:
                    if self._matches_job(job, subject, snippet, sender):
                        updates.append(EmailStatusUpdate(job.id, inferred, f"Email: {subject} | {snippet[:100]}"))
                        break
            except Exception as exc:
                logger.warning(f"Failed to process email {msg_meta.get('id')}: {exc}")
                continue

        logger.info(f"Gmail agent found {len(updates)} status updates")
        return updates

    def create_draft(self, job: JobApplication, to: str, subject: str, scenario: str = "general") -> DraftResponse | None:
        """Create a Gmail draft reply for human review."""
        service = self._get_service()
        if service is None:
            logger.warning("Cannot create Gmail draft: Gmail integration not enabled")
            return None

        body = self._build_reply_body(scenario, job, self._extract_sender_name(to))
        message = f"From: {self.settings.gmail_sender_email}\nTo: {to}\nSubject: {subject}\n\n{body}"
        raw = base64.urlsafe_b64encode(message.encode("utf-8")).decode("utf-8")
        try:
            draft = service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
            draft_id = draft.get("id")
            logger.info(f"Created Gmail draft for {to} (draft id={draft_id})")
            return DraftResponse(job.id, to, subject, body, provider="gmail", message_id=draft_id)
        except Exception as exc:
            logger.warning(f"Failed to create Gmail draft for {to}: {exc}")
            return None

    def send_response(self, to: str, subject: str, body: str) -> bool:
        service = self._get_service()
        if service is None:
            logger.warning("Cannot send email: Gmail integration not enabled")
            return False

        message = f"From: {self.settings.gmail_sender_email}\nTo: {to}\nSubject: {subject}\n\n{body}"
        raw = base64.urlsafe_b64encode(message.encode("utf-8")).decode("utf-8")
        try:
            service.users().messages().send(userId="me", body={"raw": raw}).execute()
            logger.info(f"Sent Gmail to {to}: {subject}")
            return True
        except Exception as exc:
            logger.warning(f"Failed to send Gmail to {to}: {exc}")
            return False


class OutlookAgent(BaseEmailAgent):
    """Microsoft Outlook integration via Microsoft Graph (MSAL)."""

    SCOPES = ["Mail.Read", "Mail.ReadWrite", "Mail.Send"]
    GRAPH_BASE = "https://graph.microsoft.com/v1.0"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self._token_cache: dict | None = None

    @property
    def enabled(self) -> bool:
        return (
            _MSAL_AVAILABLE
            and self.settings.outlook_client_id is not None
            and self.settings.outlook_client_id.strip() != ""
        )

    def _get_app(self) -> "msal.PublicClientApplication | None":
        if not self.enabled:
            return None
        try:
            return msal.PublicClientApplication(
                self.settings.outlook_client_id,
                authority=self.settings.outlook_authority or "https://login.microsoftonline.com/common",
            )
        except Exception as exc:
            logger.warning(f"Outlook MSAL app init failed: {exc}")
            return None

    def _acquire_token(self) -> dict | None:
        if self._token_cache is not None:
            return self._token_cache
        app = self._get_app()
        if app is None:
            return None

        try:
            accounts = app.get_accounts()
            if accounts:
                result = app.acquire_token_silent(self.SCOPES, account=accounts[0])
                if result and "access_token" in result:
                    self._token_cache = result
                    return result

            if self.settings.outlook_use_device_code:
                flow = app.initiate_device_flow(scopes=self.SCOPES)
                if "user_code" not in flow:
                    raise RuntimeError(f"Device flow failed: {flow}")
                print(flow["message"])
                result = app.acquire_token_by_device_flow(flow)
            else:
                result = app.acquire_token_interactive(scopes=self.SCOPES)

            if "access_token" in result:
                self._token_cache = result
                return result
            logger.warning(f"Outlook token acquisition failed: {result}")
            return None
        except Exception as exc:
            logger.warning(f"Outlook token acquisition failed: {exc}")
            return None

    def _graph_get(self, endpoint: str, params: dict | None = None) -> dict | None:
        token = self._acquire_token()
        if token is None:
            return None
        import requests

        url = f"{self.GRAPH_BASE}{endpoint}"
        headers = {"Authorization": f"Bearer {token['access_token']}"}
        try:
            resp = requests.get(url, headers=headers, params=params or {}, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning(f"Outlook Graph GET {endpoint} failed: {exc}")
            return None

    def _graph_post(self, endpoint: str, payload: dict) -> dict | None:
        token = self._acquire_token()
        if token is None:
            return None
        import requests

        url = f"{self.GRAPH_BASE}{endpoint}"
        headers = {
            "Authorization": f"Bearer {token['access_token']}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning(f"Outlook Graph POST {endpoint} failed: {exc}")
            return None

    def check_for_updates(self, jobs: Iterable[JobApplication]) -> list[EmailStatusUpdate]:
        if not self.enabled:
            logger.info("Outlook integration not enabled; skipping Outlook email checks")
            return []

        after = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
        # Graph filter requires ISO 8601 with timezone; append Z if absent.
        if not after.endswith("Z"):
            after = after + "Z"
        filter_query = f"receivedDateTime ge {after}"
        data = self._graph_get("/me/messages", params={"$filter": filter_query, "$top": 50, "$select": "subject,from,bodyPreview"})
        if data is None:
            return []

        updates: list[EmailStatusUpdate] = []
        messages = data.get("value", [])
        for msg in messages:
            try:
                subject = msg.get("subject", "")
                sender_obj = msg.get("from", {}).get("emailAddress", {})
                sender = f"{sender_obj.get('name', '')} <{sender_obj.get('address', '')}>"
                snippet = msg.get("bodyPreview", "")
                inferred = self._infer_status(subject, snippet)
                if inferred is None:
                    continue

                for job in jobs:
                    if self._matches_job(job, subject, snippet, sender):
                        updates.append(EmailStatusUpdate(job.id, inferred, f"Outlook: {subject} | {snippet[:100]}"))
                        break
            except Exception as exc:
                logger.warning(f"Failed to process Outlook message: {exc}")
                continue

        logger.info(f"Outlook agent found {len(updates)} status updates")
        return updates

    @staticmethod
    def _parse_email_address(raw: str) -> str:
        """Return the email address from 'Name <email@example.com>' or raw email."""
        raw = raw.strip()
        if "<" in raw and ">" in raw:
            start = raw.find("<") + 1
            end = raw.find(">")
            return raw[start:end].strip()
        return raw

    def create_draft(self, job: JobApplication, to: str, subject: str, scenario: str = "general") -> DraftResponse | None:
        """Create an Outlook draft reply for human review."""
        if not self.enabled:
            logger.warning("Cannot create Outlook draft: Outlook integration not enabled")
            return None

        address = self._parse_email_address(to)
        body = self._build_reply_body(scenario, job, self._extract_sender_name(to))
        payload = {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [
                {"emailAddress": {"name": self._extract_sender_name(to), "address": address}}
            ],
            "isDraft": True,
        }
        try:
            result = self._graph_post("/me/messages", payload)
            if result is None:
                return None
            message_id = result.get("id")
            logger.info(f"Created Outlook draft for {to} (message id={message_id})")
            return DraftResponse(job.id, to, subject, body, provider="outlook", message_id=message_id)
        except Exception as exc:
            logger.warning(f"Failed to create Outlook draft for {to}: {exc}")
            return None

    def send_response(self, to: str, subject: str, body: str) -> bool:
        """Send an Outlook email directly (not recommended; use create_draft instead)."""
        if not self.enabled:
            logger.warning("Cannot send Outlook email: Outlook integration not enabled")
            return False

        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [
                    {"emailAddress": {"address": to}}
                ],
            },
            "saveToSentItems": True,
        }
        try:
            self._graph_post("/me/sendMail", payload)
            logger.info(f"Sent Outlook email to {to}: {subject}")
            return True
        except Exception as exc:
            logger.warning(f"Failed to send Outlook email to {to}: {exc}")
            return False


class EmailAgent(BaseEmailAgent):
    """Combined email agent that checks Gmail and Outlook."""

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.gmail = GmailAgent(settings)
        self.outlook = OutlookAgent(settings)

    @property
    def enabled(self) -> bool:
        return self.gmail.enabled or self.outlook.enabled

    def check_for_updates(self, jobs: Iterable[JobApplication]) -> list[EmailStatusUpdate]:
        updates = []
        updates.extend(self.gmail.check_for_updates(jobs))
        updates.extend(self.outlook.check_for_updates(jobs))
        return updates

    def create_drafts_for_jobs(self, jobs: Iterable[JobApplication]) -> list[DraftResponse]:
        """Create human-tone draft replies for any recruiter emails matching the given jobs."""
        drafts: list[DraftResponse] = []
        after = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
        if not after.endswith("Z"):
            after = after + "Z"

        if self.gmail.enabled:
            try:
                from googleapiclient.discovery import build
            except ImportError:
                pass

        # Check Gmail messages.
        if self.gmail.enabled:
            service = self.gmail._get_service()
            if service is not None:
                query_date = (datetime.now() - timedelta(days=7)).strftime("%Y/%m/%d")
                query = f"after:{query_date}"
                try:
                    results = service.users().messages().list(userId="me", q=query, maxResults=50).execute()
                    for msg_meta in results.get("messages", []):
                        msg = service.users().messages().get(userId="me", id=msg_meta["id"], format="metadata").execute()
                        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
                        subject = headers.get("subject", "")
                        sender = headers.get("from", "")
                        snippet = msg.get("snippet", "")
                        for job in jobs:
                            if self._matches_job(job, subject, snippet, sender):
                                scenario = self._classify_scenario(subject, snippet)
                                reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
                                draft = self.gmail.create_draft(job, sender, reply_subject, scenario)
                                if draft:
                                    drafts.append(draft)
                                break
                except Exception as exc:
                    logger.warning(f"Failed to create Gmail drafts: {exc}")

        # Check Outlook messages.
        if self.outlook.enabled:
            data = self.outlook._graph_get("/me/messages", params={"$filter": f"receivedDateTime ge {after}", "$top": 50, "$select": "subject,from,bodyPreview"})
            if data is not None:
                for msg in data.get("value", []):
                    subject = msg.get("subject", "")
                    sender_obj = msg.get("from", {}).get("emailAddress", {})
                    sender_address = sender_obj.get("address", "")
                    sender = f"{sender_obj.get('name', '')} <{sender_address}>"
                    snippet = msg.get("bodyPreview", "")
                    for job in jobs:
                        if self._matches_job(job, subject, snippet, sender):
                            scenario = self._classify_scenario(subject, snippet)
                            reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
                            draft = self.outlook.create_draft(job, sender_address, reply_subject, scenario)
                            if draft:
                                drafts.append(draft)
                            break

        logger.info(f"Email agent created {len(drafts)} draft replies")
        return drafts

    def send_response(self, to: str, subject: str, body: str) -> bool:
        """Send via the first enabled provider."""
        if self.gmail.enabled:
            return self.gmail.send_response(to, subject, body)
        if self.outlook.enabled:
            return self.outlook.send_response(to, subject, body)
        logger.warning("Cannot send email: no email provider is enabled")
        return False
