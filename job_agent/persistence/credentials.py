"""Local credential store for platform/company accounts.

WARNING: credentials are stored as plaintext in the SQLite database in this first
phase. This is sufficient for local development and starting-phase testing, but
should be replaced with encryption (e.g., keyring, local master key, or OS
credential store) before production use.
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from job_agent.models import Account

SCHEMA = """
CREATE TABLE IF NOT EXISTS credentials (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    company TEXT NOT NULL,
    username TEXT NOT NULL,
    password TEXT NOT NULL,
    profile_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_credentials_platform_company ON credentials(platform, company);
"""


class CredentialStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _row_to_account(self, row: sqlite3.Row) -> Account:
        return Account(
            id=row["id"],
            platform=row["platform"],
            company=row["company"],
            username=row["username"],
            password=row["password"],
            profile_json=row["profile_json"] or None,
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
        )

    def get(self, platform: str, company: str) -> Optional[Account]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM credentials WHERE platform = ? AND company = ?",
                (platform, company),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_account(row)

    def exists(self, platform: str, company: str) -> bool:
        return self.get(platform, company) is not None

    def save(
        self,
        platform: str,
        company: str,
        username: str,
        password: str,
        profile_json: Optional[dict] = None,
    ) -> Account:
        now = datetime.utcnow().isoformat()
        account = Account(
            platform=platform,
            company=company,
            username=username,
            password=password,
            profile_json=json.dumps(profile_json) if profile_json else None,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO credentials (id, platform, company, username, password, profile_json, created_at, updated_at)
                VALUES (:id, :platform, :company, :username, :password, :profile_json, :now, :now)
                ON CONFLICT(platform, company) DO UPDATE SET
                    username=excluded.username,
                    password=excluded.password,
                    profile_json=excluded.profile_json,
                    updated_at=excluded.updated_at
                """,
                {
                    "id": account.id,
                    "platform": platform,
                    "company": company,
                    "username": username,
                    "password": password,
                    "profile_json": account.profile_json,
                    "now": now,
                },
            )
        return account

    def list_all(self) -> list[Account]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM credentials ORDER BY updated_at").fetchall()
        return [self._row_to_account(row) for row in rows]

    def delete(self, platform: str, company: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM credentials WHERE platform = ? AND company = ?",
                (platform, company),
            )
