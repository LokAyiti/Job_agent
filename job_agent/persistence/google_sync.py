"""Optional Google Drive and Google Sheets sync.

This module fails gracefully when credentials are missing so the pipeline can
run entirely locally until the user is ready to connect Google.
"""
from pathlib import Path
from typing import Iterable

from loguru import logger

from job_agent.models import JobApplication

try:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    import gspread
    _GOOGLE_AVAILABLE = True
except ImportError:
    _GOOGLE_AVAILABLE = False


class GoogleSync:
    def __init__(self, service_account_json: Path | None, sheet_id: str | None, drive_folder_id: str | None):
        self.service_account_json = service_account_json
        self.sheet_id = sheet_id
        self.drive_folder_id = drive_folder_id
        self._creds: "Credentials" | None = None
        self._drive_service = None
        self._sheets_client = None

    @property
    def enabled(self) -> bool:
        if not _GOOGLE_AVAILABLE:
            return False
        if self.service_account_json is None or not self.service_account_json.exists():
            return False
        return True

    def _credentials(self) -> "Credentials":
        if self._creds is None:
            scopes = [
                "https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/spreadsheets",
            ]
            self._creds = Credentials.from_service_account_file(
                str(self.service_account_json), scopes=scopes
            )
        return self._creds

    def _drive(self):
        if self._drive_service is None:
            self._drive_service = build("drive", "v3", credentials=self._credentials())
        return self._drive_service

    def _sheets(self):
        if self._sheets_client is None:
            self._sheets_client = gspread.authorize(self._credentials())
        return self._sheets_client

    def upload_file(self, local_path: Path, filename: str | None = None) -> str | None:
        """Upload a file to Google Drive. Returns the file ID or None."""
        if not self.enabled or self.drive_folder_id is None:
            return None
        if not local_path.exists():
            logger.warning(f"Cannot upload missing file: {local_path}")
            return None

        try:
            name = filename or local_path.name
            media = MediaFileUpload(str(local_path), resumable=True)
            metadata = {
                "name": name,
                "parents": [self.drive_folder_id],
            }
            file = (
                self._drive()
                .files()
                .create(body=metadata, media_body=media, fields="id")
                .execute()
            )
            logger.info(f"Uploaded {name} to Google Drive, id={file['id']}")
            return file["id"]
        except Exception as exc:
            logger.warning(f"Google Drive upload failed for {local_path}: {exc}")
            return None

    def sync_applications_to_sheet(self, applications: Iterable[JobApplication]) -> bool:
        """Push application rows to the configured Google Sheet."""
        if not self.enabled or self.sheet_id is None:
            return False

        try:
            sheet = self._sheets().open_by_key(self.sheet_id)
            worksheet = sheet.worksheet("Applications")
        except gspread.WorksheetNotFound:
            worksheet = sheet.add_worksheet("Applications", rows=1000, cols=10)
            worksheet.append_row([
                "job_id", "title", "company", "location", "date_applied",
                "status", "link", "resume_path", "error_message", "notes",
            ])
        except Exception as exc:
            logger.warning(f"Could not open Google Sheet {self.sheet_id}: {exc}")
            return False

        rows = [list(app.to_log_row().values()) for app in applications]
        if not rows:
            return True

        try:
            worksheet.clear()
            worksheet.append_row([
                "job_id", "title", "company", "location", "date_applied",
                "status", "link", "resume_path", "error_message", "notes",
            ])
            worksheet.append_rows(rows)
            logger.info(f"Synced {len(rows)} rows to Google Sheet {self.sheet_id}")
            return True
        except Exception as exc:
            logger.warning(f"Google Sheets sync failed: {exc}")
            return False

    def sync_resume_folder(self, resume_dir: Path) -> None:
        if not self.enabled or self.drive_folder_id is None:
            return
        for file in resume_dir.iterdir():
            if file.is_file() and file.suffix.lower() in {".pdf", ".docx", ".doc"}:
                self.upload_file(file)
