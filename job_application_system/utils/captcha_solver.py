"""Synchronous 2Captcha solver for reCAPTCHA / hCaptcha widgets.

Used by the Track A governmentjobs.com scraper, which is built on Playwright's
sync API. If the API key is missing or solving fails, the caller is expected
to fall back to human-in-the-loop handling.
"""
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests
from loguru import logger
from playwright.sync_api import Page


@dataclass
class CaptchaChallenge:
    kind: str  # 'recaptcha' or 'hcaptcha'
    sitekey: str
    widget_selector: Optional[str] = None


class CaptchaUnsolvableError(Exception):
    """Raised when 2Captcha cannot solve the challenge or the API key is missing."""


class CaptchaSolver:
    """Solve reCAPTCHA/hCaptcha challenges via 2Captcha.com and inject the token."""

    BASE_URL = "http://2captcha.com"

    def __init__(self, api_key: str, timeout: int = 120) -> None:
        self.api_key = api_key
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def solve_on_page(self, page: Page) -> bool:
        """Detect and solve any supported CAPTCHA on the current page.

        Returns True if no challenge was present or the challenge was solved.
        Raises CaptchaUnsolvableError if a challenge exists but cannot be solved.
        """
        challenge = self.detect(page)
        if challenge is None:
            logger.debug("No supported CAPTCHA widget detected")
            return True

        if not self.enabled:
            raise CaptchaUnsolvableError("2Captcha API key is not configured")

        logger.info(f"Solving {challenge.kind} challenge with sitekey {challenge.sitekey}")
        token = self.solve(challenge, page.url)
        if token is None:
            raise CaptchaUnsolvableError(f"2Captcha failed to solve {challenge.kind}")

        self.inject_token(page, challenge, token)
        return True

    def detect(self, page: Page) -> Optional[CaptchaChallenge]:
        """Return a CaptchaChallenge if a supported widget is found on the page."""
        challenge = self._detect_recaptcha(page)
        if challenge:
            return challenge
        return self._detect_hcaptcha(page)

    def _detect_recaptcha(self, page: Page) -> Optional[CaptchaChallenge]:
        try:
            widgets = page.locator(".g-recaptcha").evaluate_all(
                "els => els.map(e => ({sitekey: e.getAttribute('data-sitekey'), id: e.id}))"
            )
            for w in widgets:
                sitekey = w.get("sitekey")
                if sitekey:
                    selector = f"#{w['id']}" if w.get("id") else ".g-recaptcha"
                    return CaptchaChallenge(kind="recaptcha", sitekey=sitekey, widget_selector=selector)

            iframes = page.locator('iframe[src*="recaptcha"], iframe[src*="google.com/recaptcha"]').evaluate_all(
                "els => els.map(e => e.src)"
            )
            for src in iframes:
                sitekey = self._extract_query_param(src, "k")
                if sitekey:
                    return CaptchaChallenge(kind="recaptcha", sitekey=sitekey)
        except Exception as exc:
            logger.debug(f"reCAPTCHA detection error: {exc}")
        return None

    def _detect_hcaptcha(self, page: Page) -> Optional[CaptchaChallenge]:
        try:
            widgets = page.locator(".h-captcha").evaluate_all(
                "els => els.map(e => ({sitekey: e.getAttribute('data-sitekey'), id: e.id}))"
            )
            for w in widgets:
                sitekey = w.get("sitekey")
                if sitekey:
                    selector = f"#{w['id']}" if w.get("id") else ".h-captcha"
                    return CaptchaChallenge(kind="hcaptcha", sitekey=sitekey, widget_selector=selector)

            iframes = page.locator('iframe[src*="hcaptcha.com"]').evaluate_all(
                "els => els.map(e => e.src)"
            )
            for src in iframes:
                sitekey = self._extract_query_param(src, "sitekey")
                if sitekey:
                    return CaptchaChallenge(kind="hcaptcha", sitekey=sitekey)
        except Exception as exc:
            logger.debug(f"hCaptcha detection error: {exc}")
        return None

    def solve(self, challenge: CaptchaChallenge, page_url: str) -> Optional[str]:
        """Submit the challenge to 2Captcha and poll for the token."""
        if challenge.kind == "recaptcha":
            method = "userrecaptcha"
        elif challenge.kind == "hcaptcha":
            method = "hcaptcha"
        else:
            return None

        try:
            submit_resp = requests.post(
                f"{self.BASE_URL}/in.php",
                data={
                    "key": self.api_key,
                    "method": method,
                    "googlekey": challenge.sitekey,
                    "sitekey": challenge.sitekey,
                    "pageurl": page_url,
                    "json": 1,
                },
                timeout=30,
            )
            submit_resp.raise_for_status()
            submit_data = submit_resp.json()
            if submit_data.get("status") != 1:
                logger.warning(f"2Captcha submit error: {submit_data}")
                return None
            captcha_id = submit_data.get("request")
        except Exception as exc:
            logger.warning(f"2Captcha submit failed: {exc}")
            return None

        logger.info(f"2Captcha task id={captcha_id}, polling for result...")
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                poll_resp = requests.get(
                    f"{self.BASE_URL}/res.php",
                    params={
                        "key": self.api_key,
                        "action": "get",
                        "id": captcha_id,
                        "json": 1,
                    },
                    timeout=30,
                )
                poll_resp.raise_for_status()
                poll_data = poll_resp.json()
                if poll_data.get("status") == 1:
                    token = poll_data.get("request")
                    logger.info(f"2Captcha solved {challenge.kind} token (len={len(token) if token else 0})")
                    return token
                elif poll_data.get("request") == "CAPCHA_NOT_READY":
                    time.sleep(5)
                    continue
                else:
                    logger.warning(f"2Captcha poll error: {poll_data}")
                    return None
            except Exception as exc:
                logger.warning(f"2Captcha poll failed: {exc}")
                return None

        logger.warning("2Captcha solving timed out")
        return None

    def inject_token(self, page: Page, challenge: CaptchaChallenge, token: str) -> None:
        """Inject the solved token into the page's hidden response field."""
        response_field = (
            "#g-recaptcha-response" if challenge.kind == "recaptcha" else "#h-captcha-response"
        )
        try:
            page.locator(response_field).evaluate(
                "(el, value) => { el.style.display = 'block'; el.value = value; }",
                token,
            )
            page.evaluate(
                """
                ([token, kind]) => {
                    if (typeof grecaptcha !== 'undefined' && grecaptcha.getResponse) {
                        try { grecaptcha.getResponse = () => token; } catch (e) {}
                    }
                    if (typeof hcaptcha !== 'undefined' && hcaptcha.setResponse) {
                        try { hcaptcha.setResponse(token); } catch (e) {}
                    }
                    const selector = kind === 'recaptcha' ? '#g-recaptcha-response' : '#h-captcha-response';
                    const el = document.querySelector(selector);
                    if (el) {
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }
                """,
                [token, challenge.kind],
            )
            logger.info(f"Injected {challenge.kind} token into {response_field}")
        except Exception as exc:
            logger.warning(f"Token injection failed: {exc}")
            raise CaptchaUnsolvableError(f"Token injection failed: {exc}")

    @staticmethod
    def _extract_query_param(url: str, param: str) -> Optional[str]:
        parsed = urlparse(url)
        values = parse_qs(parsed.query).get(param)
        return values[0] if values else None
