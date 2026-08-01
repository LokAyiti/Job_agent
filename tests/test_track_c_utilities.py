"""Tests for Track C reliability / anti-detection utilities."""
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from job_agent.utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, with_async_circuit_breaker, with_circuit_breaker
from job_agent.utils.encryption import CredentialVault
from job_agent.utils.humanizer import Humanizer, StealthInjector
from job_agent.utils.proxy_rotator import ProxyRotator
from job_agent.utils.structured_logging import configure_logging


class TestCredentialVault:
    def test_encrypt_decrypt_round_trip(self, tmp_path):
        vault = CredentialVault(project_root=tmp_path)
        plaintext = "my-super-secret-password"
        encrypted = vault.encrypt(plaintext)
        assert encrypted != plaintext
        assert encrypted.startswith("gAAAA")
        decrypted = vault.decrypt(encrypted)
        assert decrypted == plaintext

    def test_encrypt_none_returns_none(self, tmp_path):
        vault = CredentialVault(project_root=tmp_path)
        assert vault.encrypt(None) is None
        assert vault.decrypt(None) is None

    def test_provided_master_key(self, tmp_path):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        vault1 = CredentialVault(master_key=key, project_root=tmp_path)
        encrypted = vault1.encrypt("secret")

        vault2 = CredentialVault(master_key=key, project_root=tmp_path)
        assert vault2.decrypt(encrypted) == "secret"

    def test_key_file_created_when_no_key_provided(self, tmp_path):
        vault = CredentialVault(project_root=tmp_path)
        key_file = tmp_path / ".credential_key"
        assert key_file.exists()
        encrypted = vault.encrypt("secret")

        # New vault reading the same key file should decrypt.
        vault2 = CredentialVault(project_root=tmp_path)
        assert vault2.decrypt(encrypted) == "secret"


class TestProxyRotator:
    def test_empty_rotator_returns_none(self):
        rotator = ProxyRotator([])
        assert rotator.next() is None
        assert rotator.to_playwright_dict() is None

    def test_parses_proxy_list(self):
        rotator = ProxyRotator(["http://proxy1:8080", "user:pass@proxy2:8080"])
        assert rotator.has_proxies is True
        p1 = rotator.next()
        assert p1.server == "http://proxy1:8080"
        assert p1.username is None
        p2 = rotator.next()
        assert p2.server == "proxy2:8080"
        assert p2.username == "user"
        assert p2.password == "pass"

    def test_round_robin(self):
        rotator = ProxyRotator(["a", "b"])
        assert rotator.next().server == "a"
        assert rotator.next().server == "b"
        assert rotator.next().server == "a"

    def test_to_playwright_dict(self):
        rotator = ProxyRotator(["user:pass@proxy:8080"])
        d = rotator.to_playwright_dict()
        assert d == {"server": "proxy:8080", "username": "user", "password": "pass"}


class TestHumanizer:
    @pytest.mark.asyncio
    async def test_wait_is_within_range(self):
        h = Humanizer(min_delay=0.01, max_delay=0.02)
        # Should not raise and should complete quickly.
        await h.wait()

    @pytest.mark.asyncio
    async def test_type_like_human(self):
        h = Humanizer(typing_delay_min=0.001, typing_delay_max=0.002)
        page = MagicMock()
        locator = AsyncMock()
        page.locator.return_value = locator
        await h.type_like_human(page, "#input", "abc")
        page.locator.assert_called_with("#input")
        assert locator.type.call_count == 3

    @pytest.mark.asyncio
    async def test_move_mouse_randomly(self):
        h = Humanizer(min_delay=0.001, max_delay=0.002)
        page = MagicMock()
        page.evaluate = AsyncMock(return_value={"width": 800, "height": 600})
        page.mouse.move = AsyncMock()
        await h.move_mouse_randomly(page)
        page.mouse.move.assert_called_once()

    @pytest.mark.asyncio
    async def test_scroll_naturally(self):
        h = Humanizer(min_delay=0.001, max_delay=0.002)
        page = MagicMock()
        page.mouse.wheel = AsyncMock()
        await h.scroll_naturally(page, pixels=200)
        assert page.mouse.wheel.call_count >= 1


class TestStealthInjector:
    @pytest.mark.asyncio
    async def test_inject_adds_script(self):
        injector = StealthInjector()
        page = MagicMock()
        await injector.inject(page)
        page.add_init_script.assert_called_once()


class TestCircuitBreaker:
    def test_closes_after_success(self):
        breaker = CircuitBreaker("test", failure_threshold=2)
        assert breaker.state.value == "closed"
        result = breaker.call(lambda: 42)
        assert result == 42
        assert breaker.state.value == "closed"

    def test_opens_after_threshold(self):
        breaker = CircuitBreaker("test", failure_threshold=2)
        with pytest.raises(RuntimeError):
            breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(RuntimeError):
            breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(CircuitBreakerOpenError):
            breaker.call(lambda: 42)
        assert breaker.state.value == "open"

    @pytest.mark.asyncio
    async def test_async_opens_after_threshold(self):
        breaker = CircuitBreaker("test", failure_threshold=2)

        async def fail():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await breaker.async_call(fail)
        with pytest.raises(RuntimeError):
            await breaker.async_call(fail)
        with pytest.raises(CircuitBreakerOpenError):
            await breaker.async_call(lambda: 42)

    def test_with_circuit_breaker_decorator(self):
        breaker = CircuitBreaker("test", failure_threshold=1)

        @with_circuit_breaker(breaker)
        def fn(x):
            if x < 0:
                raise ValueError("negative")
            return x * 2

        assert fn(5) == 10
        with pytest.raises(ValueError):
            fn(-1)
        with pytest.raises(CircuitBreakerOpenError):
            fn(5)

    @pytest.mark.asyncio
    async def test_with_async_circuit_breaker_decorator(self):
        breaker = CircuitBreaker("test", failure_threshold=1)

        @with_async_circuit_breaker(breaker)
        async def fn(x):
            if x < 0:
                raise ValueError("negative")
            return x * 2

        assert await fn(5) == 10
        with pytest.raises(ValueError):
            await fn(-1)
        with pytest.raises(CircuitBreakerOpenError):
            await fn(5)


class TestStructuredLogging:
    def test_configure_logging_adds_sinks(self, tmp_path):
        log_file = tmp_path / "test.log"
        configure_logging(level="DEBUG", log_file=log_file, json_file=False)
        # loguru should have 2 sinks: stderr + file.
        from loguru import logger
        assert len(logger._core.handlers) >= 2

    def test_configure_logging_json_mode(self, tmp_path):
        log_file = tmp_path / "test.json.log"
        configure_logging(level="INFO", log_file=log_file, json_file=True)
        from loguru import logger

        logger.info("test message")
        content = log_file.read_text(encoding="utf-8")
        record = json.loads(content.strip())
        assert record["record"]["message"] == "test message"


class TestCredentialStoreEncryption:
    @pytest.mark.asyncio
    async def test_store_encrypts_and_decrypts_password(self, tmp_path):
        from job_agent.persistence.credentials import CredentialStore

        db = tmp_path / "creds.db"
        vault = CredentialVault(project_root=tmp_path)
        store = CredentialStore(db, vault=vault)
        store.save("workday", "Acme", "loki@example.com", "secret123")
        account = store.get("workday", "Acme")
        assert account is not None
        assert account.username == "loki@example.com"
        assert account.password == "secret123"

        # Raw DB value should be encrypted.
        import sqlite3

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM credentials WHERE platform = ? AND company = ?", ("workday", "Acme")).fetchone()
        assert row["is_encrypted"] == 1
        assert row["password"] != "secret123"
        assert row["password"].startswith("gAAAA")

    @pytest.mark.asyncio
    async def test_plaintext_migration_read_as_is(self, tmp_path):
        from job_agent.persistence.credentials import CredentialStore

        db = tmp_path / "creds.db"
        # Create table without encryption and insert plaintext row.
        import sqlite3

        conn = sqlite3.connect(db)
        conn.execute(
            """
            CREATE TABLE credentials (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                company TEXT NOT NULL,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                profile_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO credentials (id, platform, company, username, password, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("1", "workday", "Acme", "loki@example.com", "plaintext", "2024-01-01", "2024-01-01"),
        )
        conn.commit()
        conn.close()

        vault = CredentialVault(project_root=tmp_path)
        store = CredentialStore(db, vault=vault)
        account = store.get("workday", "Acme")
        assert account.password == "plaintext"
