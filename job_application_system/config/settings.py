"""Application configuration and environment settings."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load project .env
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class Settings:
    """Centralized settings loaded from environment variables."""

    # Paths
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    DATA_DIR = PROJECT_ROOT / "data"
    RESUME_DIR = PROJECT_ROOT / "resume"
    COVER_LETTER_DIR = PROJECT_ROOT / "cover_letter"
    LOGS_DIR = PROJECT_ROOT / "logs"
    TEMPLATES_DIR = PROJECT_ROOT / "templates"

    # Base assets
    BASE_RESUME_TEMPLATE = Path(
        os.getenv("BASE_RESUME_TEMPLATE", "")
    )
    BASE_RESUME_PDF = Path(
        os.getenv("BASE_RESUME_PDF", "")
    )
    BASE_COVER_LETTER_DIR = Path(
        os.getenv("BASE_COVER_LETTER_DIR", "")
    )

    # Output paths
    OUTPUT_RESUME_DIR = Path(
        os.getenv("OUTPUT_RESUME_DIR", str(RESUME_DIR))
    )
    OUTPUT_COVER_LETTER_DIR = Path(
        os.getenv("OUTPUT_COVER_LETTER_DIR", str(COVER_LETTER_DIR))
    )

    # Job search
    TARGET_URL = os.getenv("TARGET_URL", "https://www.governmentjobs.com/")
    LOGIN_EMAIL = os.getenv("LOGIN_EMAIL", "")
    LOGIN_PASSWORD = os.getenv("LOGIN_PASSWORD", "")
    JOB_TITLE = os.getenv("JOB_TITLE", "Data Analyst")
    LOCATION = os.getenv("LOCATION", "United States")

    # Safety
    REQUIRES_APPROVAL = os.getenv("REQUIRES_APPROVAL", "true").lower() in (
        "true",
        "1",
        "yes",
    )

    # LLM providers
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    DATABRICKS_TOKEN = os.getenv("DATABRICKS_TOKEN", "")
    DATABRICKS_SONNET_ENDPOINT = os.getenv("DATABRICKS_SONNET_ENDPOINT", "")
    DATABRICKS_OPUS_ENDPOINT = os.getenv("DATABRICKS_OPUS_ENDPOINT", "")

    # CAPTCHA
    CAPTCHA_API_KEY = os.getenv("2CAPTCHA_API_KEY", "") or os.getenv("2captcha_key", "") or os.getenv("2CAPTCHA_KEY", "") or os.getenv("TWOCAPTCHA_API_KEY", "")

    # LLM model selection
    PRIMARY_MODEL = os.getenv("PRIMARY_MODEL", "openai/gpt-4o")
    BACKUP_MODEL = os.getenv("BACKUP_MODEL", "")

    @classmethod
    def ensure_directories(cls) -> None:
        """Create all output directories if they do not exist."""
        for directory in (
            cls.DATA_DIR,
            cls.RESUME_DIR,
            cls.COVER_LETTER_DIR,
            cls.LOGS_DIR,
            cls.OUTPUT_RESUME_DIR,
            cls.OUTPUT_COVER_LETTER_DIR,
        ):
            directory.mkdir(parents=True, exist_ok=True)


Settings.ensure_directories()
