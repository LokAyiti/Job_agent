"""Email/Recruiter Communication Agent.

Monitors Gmail for recruiter emails and updates job statuses. When credentials
are unavailable the agent degrades to a no-op so the pipeline can still run.
"""
import base64
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


class EmailStatusUpdate:
    def __init__(self, job_id: str, new_status: ApplicationStatus, reason: str):
        self.job_id = job_id
        self.new_status = new_status
        self.reason = reason


class EmailAgent(BaseAgent):
    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"]

    # Simple keyword-based status inference.
    STATUS_KEYWORDS = {
        ApplicationStatus.NEEDS_HUMAN: ["captcha", "verify", "confirm your identity"],
        ApplicationStatus.RESPONDED: ["interview", "phone screen", "recruiter", "hiring manager"],
        ApplicationStatus.FAILED: ["position closed", "no longer available", "filled", "cancelled"],
    }

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

    def check_for_updates(self, jobs: Iterable[JobApplication]) -> list[EmailStatusUpdate]:
        service = self._get_service()
        if service is None:
            logger.info("Gmail integration not enabled; skipping email checks")
            return []

        updates: list[EmailStatusUpdate] = []
        # Look at last 7 days only.
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

        logger.info(f"Email agent found {len(updates)} status updates")
        return updates

    def send_response(self, to: str, subject: str, body: str) -> bool:
        service = self._get_service()
        if service is None:
            logger.warning("Cannot send email: Gmail integration not enabled")
            return False

        message = f"From: {self.settings.gmail_sender_email}\nTo: {to}\nSubject: {subject}\n\n{body}"
        raw = base64.urlsafe_b64encode(message.encode("utf-8")).decode("utf-8")
        try:
            service.users().messages().send(userId="me", body={"raw": raw}).execute()
            logger.info(f"Sent email to {to}: {subject}")
            return True
        except Exception as exc:
            logger.warning(f"Failed to send email to {to}: {exc}")
            return False
