"""Centralised settings loaded from .env via pydantic-settings."""
import json
from pathlib import Path
from typing import Any, Optional

from loguru import logger
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

    # Profile (legacy .env fields; overridden by profile_json when present)
    my_name: str = Field(default="")
    my_email: str = Field(default="")
    my_phone: str = Field(default="")
    my_linkedin: str = Field(default="")

    # Unified profile JSON (preferred). Path relative to project root or absolute.
    profile_json: Optional[Path] = Field(default=_PROJECT_ROOT / "profile.json")
    base_resume_dir: Path = Field(default=_PROJECT_ROOT / "base resume")
    base_resume_template: Optional[Path] = Field(default=None)
    base_cover_letter_dir: Optional[Path] = Field(default=None)

    # Fabrication tolerance for resume tailoring (none | moderate | aggressive)
    fabrication_tolerance: str = Field(default="moderate")

    # Login credentials for first-time platform account creation
    login_email: str = Field(default="")
    login_password: str = Field(default="")

    # CAPTCHA solving
    twocaptcha_api_key: Optional[str] = Field(default=None, validation_alias="TWOCAPTCHA_API_KEY")
    captcha_timeout_seconds: int = Field(default=120)

    # Google (optional)
    google_service_account_json: Optional[Path] = Field(default=None)
    google_sheet_id: Optional[str] = Field(default=None)
    google_drive_folder_id: Optional[str] = Field(default=None)

    # Gmail (optional)
    gmail_sender_email: Optional[str] = Field(default=None)
    gmail_credentials_json: Optional[Path] = Field(default=None)

    # Outlook / Microsoft 365 (optional)
    outlook_client_id: Optional[str] = Field(default=None)
    outlook_use_device_code: bool = Field(default=True)
    outlook_authority: Optional[str] = Field(default="https://login.microsoftonline.com/common")

    # Credential encryption (optional)
    credential_master_key: Optional[str] = Field(default=None, validation_alias="CREDENTIAL_MASTER_KEY")
    credential_key_file: Optional[Path] = Field(default=None, validation_alias="CREDENTIAL_KEY_FILE")

    # Proxy / anti-detection
    proxy_list: Optional[str] = Field(default=None, validation_alias="PROXY_LIST")
    use_stealth: bool = Field(default=True)
    browser_headless: bool = Field(default=True)

    # Human-like pacing (seconds)
    humanizer_min_delay: float = Field(default=0.15)
    humanizer_max_delay: float = Field(default=0.55)
    typing_delay_min: float = Field(default=0.03)
    typing_delay_max: float = Field(default=0.12)
    delay_between_jobs_seconds: int = Field(default=10)
    jitter_between_jobs: bool = Field(default=True)

    # Logging
    log_level: str = Field(default="INFO")
    log_to_file: bool = Field(default=False)
    agent_log_file: Path = Field(default=_PROJECT_ROOT / "logs" / "job_agent.log")
    json_logs: bool = Field(default=False)

    # Safety
    enable_auto_submit: bool = Field(default=False)
    human_in_the_loop: bool = Field(default=True)

    # Auto-approval / trusted platforms (comma-separated list of adapter names)
    trusted_platforms: str = Field(default="")
    auto_approve_after_successes: int = Field(default=3)

    # LLM-based fit scoring
    min_fit_score: int = Field(default=60)
    llm_fit_score_weight: float = Field(default=0.7)

    @field_validator("llm_fit_score_weight", mode="before")
    @classmethod
    def _validate_weight_range(cls, value):
        if value is None:
            return 0.7
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"llm_fit_score_weight must be a float between 0 and 1, got {value}")
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"llm_fit_score_weight must be between 0 and 1, got {value}")
        return value

    # Rate limiting / retries
    max_retries: int = Field(default=3)
    retry_delay_seconds: int = Field(default=5)
    circuit_breaker_failure_threshold: int = Field(default=3)
    circuit_breaker_recovery_timeout: float = Field(default=120.0)

    @field_validator("resume_dir", "log_file", "sqlite_db", "screenshot_dir", "agent_log_file", mode="before")
    @classmethod
    def _resolve_paths(cls, value):
        if value is None:
            return value
        path = Path(value)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        return path

    @field_validator("profile_json", "base_resume_dir", "base_resume_template", "base_cover_letter_dir", "google_service_account_json", "gmail_credentials_json", "credential_key_file", mode="before")
    @classmethod
    def _resolve_optional_paths(cls, value):
        if value is None or value == "":
            return None
        path = Path(value)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        return path

    @field_validator("fabrication_tolerance", mode="before")
    @classmethod
    def _validate_fabrication_tolerance(cls, value: str) -> str:
        if value is None:
            return "moderate"
        value = str(value).strip().lower()
        allowed = {"none", "moderate", "aggressive"}
        if value not in allowed:
            raise ValueError(f"fabrication_tolerance must be one of {allowed}, got {value}")
        return value

    def ensure_dirs(self) -> None:
        """Create directories that the application expects."""
        self.resume_dir.mkdir(parents=True, exist_ok=True)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.sqlite_db.parent.mkdir(parents=True, exist_ok=True)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.base_resume_dir.mkdir(parents=True, exist_ok=True)

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

    @property
    def outlook_enabled(self) -> bool:
        return self.outlook_client_id is not None and self.outlook_client_id.strip() != ""

    def load_profile(self) -> dict[str, Any]:
        """Load the unified profile JSON, falling back to legacy .env fields."""
        if self.profile_json and self.profile_json.exists():
            try:
                with open(self.profile_json, "r", encoding="utf-8") as f:
                    profile = json.load(f)
            except Exception as exc:
                logger.warning(f"Could not load profile_json {self.profile_json}: {exc}")
                profile = None
        else:
            profile = None

        if profile is None:
            profile = {
                "personal_info": {
                    "name": self.my_name,
                    "email": self.my_email,
                    "phone": self.my_phone,
                    "linkedin": self.my_linkedin,
                },
                "preferences": {},
                "assets": {},
                "skills": [],
                "experience_highlights": [],
            }

        # Ensure required keys exist with sensible defaults.
        preferences = profile.setdefault("preferences", {})
        if "fabrication_tolerance" not in preferences:
            preferences["fabrication_tolerance"] = self.fabrication_tolerance

        assets = profile.setdefault("assets", {})
        if "base_resume_dir" not in assets:
            assets["base_resume_dir"] = str(self.base_resume_dir)

        return profile

    @property
    def trusted_platform_list(self) -> list[str]:
        """Parse comma-separated trusted platform adapter names."""
        if not self.trusted_platforms:
            return []
        return [p.strip() for p in self.trusted_platforms.split(",") if p.strip()]


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
