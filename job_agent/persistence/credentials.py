"""Local credential store for platform/company accounts.

Passwords and profile JSON are encrypted at rest with Fernet. The master key is
read from `CREDENTIAL_MASTER_KEY` or a key file. Existing plaintext rows from
earlier versions are read as-is until they are next written, at which point they
are encrypted.
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from job_agent.config import Settings
from job_agent.models import Account
from job_agent.utils.encryption import CredentialVault

SCHEMA = """
CREATE TABLE IF NOT EXISTS credentials (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    company TEXT NOT NULL,
    username TEXT NOT NULL,
    password TEXT NOT NULL,
    profile_json TEXT,
    is_encrypted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_credentials_platform_company ON credentials(platform, company);
"""

MIGRATION_ADD_IS_ENCRYPTED = """
ALTER TABLE credentials ADD COLUMN is_encrypted INTEGER NOT NULL DEFAULT 0;
"""


class CredentialStore:
    def __init__(
        self,
        db_path: Path,
        settings: Optional[Settings] = None,
        vault: Optional[CredentialVault] = None,
    ):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if vault is not None:
            self.vault = vault
        elif settings is not None:
            self.vault = CredentialVault(
                master_key=settings.credential_master_key,
                key_file=settings.credential_key_file,
            )
        else:
            self.vault = CredentialVault()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            # Backward-compat migration: add is_encrypted if it doesn't exist.
            try:
                conn.execute("SELECT is_encrypted FROM credentials LIMIT 1")
            except sqlite3.OperationalError:
                conn.executescript(MIGRATION_ADD_IS_ENCRYPTED)

    def _decrypt_value(self, value: Optional[str], is_encrypted: int) -> Optional[str]:
        if value is None:
            return None
        if is_encrypted:
            try:
                return self.vault.decrypt(value)
            except Exception as exc:
                logger.error(f"Failed to decrypt credential value: {exc}")
                raise
        return value

    def _row_to_account(self, row: sqlite3.Row) -> Account:
        return Account(
            id=row["id"],
            platform=row["platform"],
            company=row["company"],
            username=row["username"],
            password=self._decrypt_value(row["password"], row["is_encrypted"]),
            profile_json=self._decrypt_value(row["profile_json"], row["is_encrypted"]),
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
        encrypted_password = self.vault.encrypt(password)
        encrypted_profile = self.vault.encrypt(account.profile_json) if account.profile_json else None

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO credentials (id, platform, company, username, password, profile_json, is_encrypted, created_at, updated_at)
                VALUES (:id, :platform, :company, :username, :password, :profile_json, 1, :now, :now)
                ON CONFLICT(platform, company) DO UPDATE SET
                    username=excluded.username,
                    password=excluded.password,
                    profile_json=excluded.profile_json,
                    is_encrypted=1,
                    updated_at=excluded.updated_at
                """,
                {
                    "id": account.id,
                    "platform": platform,
                    "company": company,
                    "username": username,
                    "password": encrypted_password,
                    "profile_json": encrypted_profile,
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
