"""Credential encryption using Fernet.

The master key is read from the `CREDENTIAL_MASTER_KEY` env var (a base64 Fernet
key) or loaded from `CREDENTIAL_KEY_FILE`. If neither is provided, a key is
auto-generated and stored locally so the app keeps working, but you should back
it up and move it to a secure secret store for production.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger


def _generate_key() -> bytes:
    return Fernet.generate_key()


class CredentialVault:
    """Encrypt/decrypt credential values with a Fernet symmetric key."""

    ENV_KEY_VAR = "CREDENTIAL_MASTER_KEY"
    ENV_KEY_FILE_VAR = "CREDENTIAL_KEY_FILE"
    DEFAULT_KEY_FILE = ".credential_key"

    def __init__(
        self,
        master_key: Optional[str] = None,
        key_file: Optional[Path] = None,
        project_root: Optional[Path] = None,
    ):
        self.project_root = project_root or Path(__file__).resolve().parent.parent.parent
        self._provided_key = master_key
        self._provided_key_file = key_file
        self._fernet = self._load_or_create_fernet()

    def _load_or_create_fernet(self) -> Fernet:
        key = self._load_key()
        if key is None:
            key = _generate_key()
            self._store_key(key)
            logger.warning(
                "Generated a new credential master key and saved it locally. "
                "Set CREDENTIAL_MASTER_KEY in .env or a secret manager for production."
            )
        return Fernet(key)

    def _load_key(self) -> Optional[bytes]:
        # 1. Explicit constructor parameter.
        if self._provided_key:
            try:
                key = self._provided_key.encode("utf-8")
                Fernet(key)
                return key
            except Exception as exc:
                logger.warning(f"Provided master key is invalid: {exc}")

        # 2. Env var.
        env_key = os.environ.get(self.ENV_KEY_VAR)
        if env_key:
            try:
                key = env_key.encode("utf-8")
                Fernet(key)
                return key
            except Exception as exc:
                logger.warning(f"CREDENTIAL_MASTER_KEY is invalid: {exc}")

        # 3. Explicit key file or default key file.
        key_file = self._key_file_path()
        if key_file.exists():
            try:
                key = key_file.read_bytes().strip()
                Fernet(key)
                return key
            except Exception as exc:
                logger.warning(f"Could not load credential key from {key_file}: {exc}")

        return None

    def _key_file_path(self) -> Path:
        if self._provided_key_file:
            path = self._provided_key_file
            if not path.is_absolute():
                path = self.project_root / path
            return path

        env_file = os.environ.get(self.ENV_KEY_FILE_VAR)
        if env_file:
            path = Path(env_file)
            if not path.is_absolute():
                path = self.project_root / path
            return path
        return self.project_root / self.DEFAULT_KEY_FILE

    def _store_key(self, key: bytes) -> None:
        key_file = self._key_file_path()
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(key)
        try:
            os.chmod(key_file, 0o600)
        except Exception:
            pass

    def encrypt(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        try:
            return self._fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            logger.error(f"Credential decryption failed (wrong key or corrupt value): {exc}")
            raise

    def rotate_key(self, new_key: bytes) -> None:
        """Re-encrypt all credentials with a new key (called by credential store)."""
        Fernet(new_key)  # validate
        self._fernet = Fernet(new_key)
        self._store_key(new_key)
