"""Tests for the 2Captcha solver and credential store."""
import json
from pathlib import Path

import pytest

from job_agent.captcha import CaptchaChallenge, CaptchaSolver, CaptchaUnsolvableError
from job_agent.config import Settings
from job_agent.persistence.credentials import CredentialStore


class FakePage:
    """Minimal fake Playwright Page for CAPTCHA detection tests."""

    def __init__(self, html: str = "", url: str = "https://example.com/apply"):
        self.html = html
        self._url = url

    @property
    def url(self) -> str:
        return self._url

    def locator(self, selector: str):
        return FakeLocator(self.html, selector)


class FakeLocator:
    def __init__(self, html: str, selector: str):
        self.html = html
        self.selector = selector

    @property
    def first(self):
        return self

    async def count(self) -> int:
        # Very naive matching for the test HTML snippets.
        selector_key = self.selector.split("=")[-1].strip('"').strip("'")
        if selector_key and selector_key in self.html:
            return 1
        if "captcha" in self.selector.lower() and "captcha" in self.html.lower():
            return 1
        if "challenge" in self.selector.lower() and "challenge" in self.html.lower():
            return 1
        if self.selector in self.html:
            return 1
        return 0

    async def evaluate_all(self, expression: str, arg=None):
        if ".g-recaptcha" in self.selector:
            if 'data-sitekey="test-sitekey"' in self.html:
                return [{"sitekey": "test-sitekey", "id": "recaptcha-widget"}]
        elif 'iframe[src*="recaptcha"]' in self.selector:
            if "google.com/recaptcha" in self.html:
                return ["https://www.google.com/recaptcha/api2/anchor?k=iframe-sitekey"]
        elif ".h-captcha" in self.selector:
            if 'data-sitekey="hcap-sitekey"' in self.html:
                return [{"sitekey": "hcap-sitekey", "id": ""}]
        return []

    async def screenshot(self, **kwargs):
        return b"fake-image-bytes"

    async def fill(self, value: str):
        pass

    async def evaluate(self, expression: str, arg=None):
        pass


@pytest.fixture
def solver(temp_settings):
    return CaptchaSolver(temp_settings)


@pytest.mark.asyncio
async def test_detect_recaptcha_from_widget(solver):
    page = FakePage(html='<div class="g-recaptcha" data-sitekey="test-sitekey" id="recaptcha-widget"></div>')
    challenge = await solver.detect(page)
    assert challenge is not None
    assert challenge.kind == "recaptcha"
    assert challenge.sitekey == "test-sitekey"


@pytest.mark.asyncio
async def test_detect_recaptcha_from_iframe(solver):
    page = FakePage(
        html='<iframe src="https://www.google.com/recaptcha/api2/anchor?k=iframe-sitekey"></iframe>'
    )
    challenge = await solver.detect(page)
    assert challenge is not None
    assert challenge.sitekey == "iframe-sitekey"


@pytest.mark.asyncio
async def test_detect_no_captcha(solver):
    page = FakePage(html="<form><input id='first_name'></form>")
    challenge = await solver.detect(page)
    assert challenge is None


@pytest.mark.asyncio
async def test_solve_on_page_without_captcha(solver):
    page = FakePage(html="<form></form>")
    assert await solver.solve_on_page(page) is True


@pytest.mark.asyncio
async def test_solve_on_page_with_captcha_but_no_api_key(solver, temp_settings):
    temp_settings.twocaptcha_api_key = None
    page = FakePage(html='<div class="g-recaptcha" data-sitekey="test-sitekey"></div>')
    with pytest.raises(CaptchaUnsolvableError):
        await solver.solve_on_page(page)


@pytest.mark.asyncio
async def test_solver_submits_and_polls_2captcha(monkeypatch, solver):
    """Mock the full 2Captcha submit + poll flow."""

    class FakeResponse:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    def fake_post(url, data=None, **kwargs):
        assert "in.php" in url
        return FakeResponse({"status": 1, "request": "12345"})

    def fake_get(url, params=None, **kwargs):
        assert "res.php" in url
        assert params["id"] == "12345"
        return FakeResponse({"status": 1, "request": "solved-token-abc"})

    monkeypatch.setattr("job_agent.captcha.requests.post", fake_post)
    monkeypatch.setattr("job_agent.captcha.requests.get", fake_get)

    solver.settings.twocaptcha_api_key = "test-api-key"

    class InjectablePage(FakePage):
        def __init__(self, html, url):
            super().__init__(html, url)
            self.injected = {}

        def locator(self, selector):
            return InjectableLocator(self, selector)

        async def evaluate(self, expression, arg=None):
            # No-op for the global evaluate call; injection is captured by the locator.
            pass

    class InjectableLocator(FakeLocator):
        def __init__(self, page, selector):
            super().__init__(page.html, selector)
            self.page = page

        async def count(self):
            return 1

        async def evaluate(self, expression, value=None):
            self.page.injected[self.selector] = value

    page = InjectablePage(
        html='<div class="g-recaptcha" data-sitekey="test-sitekey" id="recaptcha-widget"></div>',
        url="https://example.com/apply",
    )

    await solver.solve_on_page(page)
    assert page.injected.get("#g-recaptcha-response") == "solved-token-abc"


@pytest.mark.asyncio
async def test_credential_store_round_trip(temp_settings):
    db_path = temp_settings.sqlite_db.parent / "credentials_test.db"
    store = CredentialStore(db_path)
    account = store.save("workday", "Acme Corp", "loki@example.com", "secret123")
    assert account.platform == "workday"
    assert account.company == "Acme Corp"

    fetched = store.get("workday", "Acme Corp")
    assert fetched is not None
    assert fetched.username == "loki@example.com"
    assert fetched.password == "secret123"

    assert store.exists("workday", "Acme Corp") is True
    assert store.exists("workday", "Other Corp") is False

    store.save("workday", "Acme Corp", "loki@example.com", "newpass")
    updated = store.get("workday", "Acme Corp")
    assert updated.password == "newpass"

    all_accounts = store.list_all()
    assert len(all_accounts) == 1

    store.delete("workday", "Acme Corp")
    assert store.get("workday", "Acme Corp") is None


def test_settings_captcha_and_login_flags(temp_settings):
    assert temp_settings.captcha_enabled is False
    temp_settings.twocaptcha_api_key = "some-key"
    assert temp_settings.captcha_enabled is True
    assert temp_settings.has_login_credentials is True

    empty_settings = Settings()
    empty_settings.login_email = ""
    empty_settings.login_password = ""
    assert empty_settings.has_login_credentials is False


@pytest.mark.asyncio
async def test_detect_image_captcha(solver):
    page = FakePage(html='<img src="/captcha.png" id="captcha-image" />')
    challenge = await solver.detect(page)
    assert challenge is not None
    assert challenge.kind == "image"
    assert challenge.image_selector is not None


@pytest.mark.asyncio
async def test_detect_no_image_captcha_for_regular_images(solver):
    page = FakePage(html='<img src="/logo.png" />')
    challenge = await solver.detect(page)
    assert challenge is None


@pytest.mark.asyncio
async def test_solve_image_captcha_and_fill_input(monkeypatch, solver):
    class FakeResponse:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    def fake_post(url, data=None, **kwargs):
        assert "in.php" in url
        return FakeResponse({"status": 1, "request": "img-123"})

    def fake_get(url, params=None, **kwargs):
        assert "res.php" in url
        assert params["id"] == "img-123"
        return FakeResponse({"status": 1, "request": "ABCD"})

    monkeypatch.setattr("job_agent.captcha.requests.post", fake_post)
    monkeypatch.setattr("job_agent.captcha.requests.get", fake_get)
    solver.settings.twocaptcha_api_key = "test-api-key"

    class InjectablePage(FakePage):
        def __init__(self, html, url):
            super().__init__(html, url)
            self.filled = {}

        def locator(self, selector):
            return InjectableLocator(self, selector)

    class InjectableLocator(FakeLocator):
        def __init__(self, page, selector):
            super().__init__(page.html, selector)
            self.page = page

        async def count(self):
            # Support image selector and input selectors.
            if "img" in self.selector or "input" in self.selector:
                return 1
            return await super().count()

        async def fill(self, value: str):
            self.page.filled[self.selector] = value

    page = InjectablePage(
        html='<img src="/captcha.png" id="captcha-image" /><input name="captcha_answer" />',
        url="https://example.com/apply",
    )

    await solver.solve_on_page(page)
    assert any("captcha" in k and v == "ABCD" for k, v in page.filled.items())
