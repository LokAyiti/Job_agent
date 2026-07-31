"""Centralised settings loaded from .env via pydantic-settings."""
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Paths
    resume_dir: Path = Field(default=_PROJECT_ROOT / "resume")
    log_file: Path = Field(default=_PROJECT_ROOT / "logs" / "applications.xlsx")
    sqlite_db: Path = Field(default=_PROJECT_ROOT / "logs" / "job_queue.db")
    screenshot_dir: Path = Field(default=_PROJECT_ROOT / "logs" / "screenshots")

    # Profile
    my_name: str = Field(default="")
    my_email: str = Field(default="")
    my_phone: str = Field(default="")
    my_linkedin: str = Field(default="")

    # Login credentials for first-time platform account creation
    login_email: str = Field(default="")
    login_password: str = Field(default="")

    # CAPTCHA solving
    twocaptcha_api_key: Optional[str] = Field(default=None)
    captcha_timeout_seconds: int = Field(default=120)

    # Google (optional)
    google_service_account_json: Optional[Path] = Field(default=None)
    google_sheet_id: Optional[str] = Field(default=None)
    google_drive_folder_id: Optional[str] = Field(default=None)

    # Gmail (optional)
    gmail_sender_email: Optional[str] = Field(default=None)
    gmail_credentials_json: Optional[Path] = Field(default=None)

    # Safety
    enable_auto_submit: bool = Field(default=False)
    human_in_the_loop: bool = Field(default=True)

    # Rate limiting / retries
    max_retries: int = Field(default=3)
    retry_delay_seconds: int = Field(default=5)
    delay_between_jobs_seconds: int = Field(default=10)
    browser_headless: bool = Field(default=True)

    @field_validator("resume_dir", "log_file", "sqlite_db", "screenshot_dir", mode="before")
    @classmethod
    def _resolve_paths(cls, value):
        if value is None:
            return value
        path = Path(value)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        return path

    @field_validator("google_service_account_json", "gmail_credentials_json", mode="before")
    @classmethod
    def _resolve_optional_paths(cls, value):
        if value is None or value == "":
            return None
        path = Path(value)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        return path

    def ensure_dirs(self) -> None:
        """Create directories that the application expects."""
        self.resume_dir.mkdir(parents=True, exist_ok=True)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.sqlite_db.parent.mkdir(parents=True, exist_ok=True)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

    @property
    def captcha_enabled(self) -> bool:
        return self.twocaptcha_api_key is not None and self.twocaptcha_api_key.strip() != ""

    @property
    def has_login_credentials(self) -> bool:
        return bool(self.login_email and self.login_password)

    @property
    def google_sync_enabled(self) -> bool:
        return (
            self.google_service_account_json is not None
            and self.google_service_account_json.exists()
        )

    @property
    def gmail_enabled(self) -> bool:
        return (
            self.gmail_credentials_json is not None
            and self.gmail_credentials_json.exists()
            and self.gmail_sender_email is not None
        )


# Singleton instance created lazily so tests can monkeypatch easily.
_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reload_settings() -> Settings:
    global _settings
    _settings = Settings()
    return _settings
