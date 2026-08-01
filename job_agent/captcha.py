"""2Captcha integration with human-in-the-loop fallback.

Implements reCAPTCHA, hCaptcha, and generic image-based CAPTCHA solving via
2Captcha.com. If the service fails or the API key is missing, the caller is
expected to fall back to human-in-the-loop handling.
"""
import base64
import io
import json
import time
from dataclasses import dataclass
from typing import Optional

import requests
from loguru import logger
from playwright.async_api import Page

from job_agent.config import Settings


class CaptchaUnsolvableError(Exception):
    """Raised when 2Captcha cannot solve the challenge or the API key is missing."""


@dataclass
class CaptchaChallenge:
    kind: str  # 'recaptcha', 'hcaptcha', or 'image'
    sitekey: Optional[str] = None
    widget_selector: Optional[str] = None
    image_selector: Optional[str] = None
    image_data: Optional[str] = None  # base64 PNG for image CAPTCHAs


class CaptchaSolver:
    """Solves CAPTCHA challenges using 2Captcha and injects the result into the page."""

    BASE_URL = "http://2captcha.com"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.api_key = settings.twocaptcha_api_key
        self.timeout = settings.captcha_timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.settings.twocaptcha_api_key)

    async def detect(self, page: Page) -> Optional[CaptchaChallenge]:
        """Return a CaptchaChallenge if a supported widget is found on the page."""
        recaptcha = await self._detect_recaptcha(page)
        if recaptcha:
            return recaptcha
        hcaptcha = await self._detect_hcaptcha(page)
        if hcaptcha:
            return hcaptcha
        image = await self._detect_image_captcha(page)
        if image:
            return image
        return None

    async def solve_on_page(self, page: Page) -> bool:
        """Detect and solve any CAPTCHA on the current page.

        Returns True if a challenge was solved (or no challenge was present).
        Raises CaptchaUnsolvableError if a challenge exists but cannot be solved.
        """
        challenge = await self.detect(page)
        if challenge is None:
            logger.debug("No supported CAPTCHA widget detected")
            return True

        if not self.enabled:
            logger.warning("CAPTCHA widget found but TWOCAPTCHA_API_KEY is not set")
            raise CaptchaUnsolvableError("2Captcha API key not configured")

        logger.info(f"Solving {challenge.kind} challenge")
        token = await self._solve(challenge, page.url)
        if token is None:
            raise CaptchaUnsolvableError(f"2Captcha failed to solve {challenge.kind}")

        await self._inject_token(page, challenge, token)
        return True

    async def _detect_recaptcha(self, page: Page) -> Optional[CaptchaChallenge]:
        try:
            # Standard reCAPTCHA v2 widget.
            widgets = await page.locator('.g-recaptcha').evaluate_all(
                "els => els.map(e => ({sitekey: e.getAttribute('data-sitekey'), id: e.id}))"
            )
            for w in widgets:
                sitekey = w.get("sitekey")
                if sitekey:
                    return CaptchaChallenge(kind="recaptcha", sitekey=sitekey, widget_selector=f"#{w['id']}" if w['id'] else '.g-recaptcha')

            # iframe-based detection.
            iframes = await page.locator('iframe[src*="recaptcha"], iframe[src*="google.com/recaptcha"]').evaluate_all(
                "els => els.map(e => e.src)"
            )
            for src in iframes:
                if "k=" in src:
                    sitekey = self._extract_query_param(src, "k")
                    if sitekey:
                        return CaptchaChallenge(kind="recaptcha", sitekey=sitekey)
        except Exception as exc:
            logger.debug(f"reCAPTCHA detection error: {exc}")
        return None

    async def _detect_hcaptcha(self, page: Page) -> Optional[CaptchaChallenge]:
        try:
            widgets = await page.locator('.h-captcha').evaluate_all(
                "els => els.map(e => ({sitekey: e.getAttribute('data-sitekey'), id: e.id}))"
            )
            for w in widgets:
                sitekey = w.get("sitekey")
                if sitekey:
                    return CaptchaChallenge(kind="hcaptcha", sitekey=sitekey, widget_selector=f"#{w['id']}" if w['id'] else '.h-captcha')

            iframes = await page.locator('iframe[src*="hcaptcha.com"]').evaluate_all(
                "els => els.map(e => e.src)"
            )
            for src in iframes:
                if "sitekey=" in src:
                    sitekey = self._extract_query_param(src, "sitekey")
                    if sitekey:
                        return CaptchaChallenge(kind="hcaptcha", sitekey=sitekey)
        except Exception as exc:
            logger.debug(f"hCaptcha detection error: {exc}")
        return None

    async def _detect_image_captcha(self, page: Page) -> Optional[CaptchaChallenge]:
        """Detect generic image CAPTCHA elements.

        Looks for images with common CAPTCHA-related selectors or attributes.
        This is best-effort; custom ATS image CAPTCHAs vary widely.
        """
        selectors = [
            "img[src*='captcha']",
            "img[id*='captcha']",
            "img[class*='captcha']",
            "img[alt*='captcha']",
            "img[src*='challenge']",
            "img[id*='challenge']",
            "img[class*='challenge']",
        ]
        for selector in selectors:
            try:
                count = await page.locator(selector).count()
                if count == 0:
                    continue
                # Use the first matching image.
                image_element = page.locator(selector).first
                # Screenshot just the element.
                screenshot_bytes = await image_element.screenshot()
                image_data = base64.b64encode(screenshot_bytes).decode("ascii")
                logger.info(f"Detected image CAPTCHA with selector {selector}")
                return CaptchaChallenge(
                    kind="image",
                    image_selector=selector,
                    image_data=image_data,
                )
            except Exception as exc:
                logger.debug(f"Image CAPTCHA detection error for {selector}: {exc}")
                continue
        return None

    async def _solve(self, challenge: CaptchaChallenge, page_url: str) -> Optional[str]:
        if challenge.kind == "recaptcha":
            return await self._solve_token_captcha(
                challenge,
                page_url,
                method="userrecaptcha",
                param_name="googlekey",
            )
        if challenge.kind == "hcaptcha":
            return await self._solve_token_captcha(
                challenge,
                page_url,
                method="hcaptcha",
                param_name="sitekey",
            )
        if challenge.kind == "image":
            return await self._solve_image_captcha(challenge)
        return None

    async def _solve_token_captcha(
        self,
        challenge: CaptchaChallenge,
        page_url: str,
        method: str,
        param_name: str,
    ) -> Optional[str]:
        if not challenge.sitekey:
            return None

        try:
            submit_resp = requests.post(
                f"{self.BASE_URL}/in.php",
                data={
                    "key": self.settings.twocaptcha_api_key,
                    "method": method,
                    param_name: challenge.sitekey,
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

        return await self._poll_for_result(captcha_id)

    async def _solve_image_captcha(self, challenge: CaptchaChallenge) -> Optional[str]:
        """Submit a base64-encoded image CAPTCHA and return the text answer."""
        if not challenge.image_data:
            return None

        try:
            submit_resp = requests.post(
                f"{self.BASE_URL}/in.php",
                data={
                    "key": self.settings.twocaptcha_api_key,
                    "method": "base64",
                    "body": challenge.image_data,
                    "json": 1,
                },
                timeout=30,
            )
            submit_resp.raise_for_status()
            submit_data = submit_resp.json()
            if submit_data.get("status") != 1:
                logger.warning(f"2Captcha image submit error: {submit_data}")
                return None
            captcha_id = submit_data.get("request")
        except Exception as exc:
            logger.warning(f"2Captcha image submit failed: {exc}")
            return None

        return await self._poll_for_result(captcha_id)

    async def _poll_for_result(self, captcha_id: str) -> Optional[str]:
        logger.info(f"2Captcha task id={captcha_id}, polling for result...")
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                poll_resp = requests.get(
                    f"{self.BASE_URL}/res.php",
                    params={
                        "key": self.settings.twocaptcha_api_key,
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
                    logger.info(f"2Captcha solved task {captcha_id} (len={len(token) if token else 0})")
                    return token
                elif poll_data.get("request") == "CAPCHA_NOT_READY":
                    await self._async_sleep(5)
                    continue
                else:
                    logger.warning(f"2Captcha poll error: {poll_data}")
                    return None
            except Exception as exc:
                logger.warning(f"2Captcha poll failed: {exc}")
                return None

        logger.warning("2Captcha solving timed out")
        return None

    async def _inject_token(self, page: Page, challenge: CaptchaChallenge, token: str) -> None:
        if challenge.kind == "recaptcha":
            response_field = "#g-recaptcha-response"
        elif challenge.kind == "hcaptcha":
            response_field = "#h-captcha-response"
        elif challenge.kind == "image":
            # For image CAPTCHAs, the answer is text; we need a platform-specific input.
            # Try common selectors, but if none match we leave it to the caller.
            await self._fill_image_answer(page, challenge, token)
            return
        else:
            return

        try:
            # Make the response field visible and set the token.
            await page.locator(response_field).evaluate(
                f"(el, value) => {{ el.style.display = 'block'; el.value = value; }}",
                token,
            )
            # Trigger common callback names if they exist.
            await page.evaluate(
                """
                ([token, kind]) => {
                    if (typeof grecaptcha !== 'undefined' && grecaptcha.getResponse) {
                        try { grecaptcha.getResponse = () => token; } catch (e) {}
                    }
                    if (typeof hcaptcha !== 'undefined' && hcaptcha.setResponse) {
                        try { hcaptcha.setResponse(token); } catch (e) {}
                    }
                    // Dispatch an input event so frameworks observe the change.
                    const el = document.querySelector(kind === 'recaptcha' ? '#g-recaptcha-response' : '#h-captcha-response');
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

    async def _fill_image_answer(
        self, page: Page, challenge: CaptchaChallenge, answer: str
    ) -> None:
        """Fill a text answer into a nearby input for image CAPTCHAs."""
        selectors = [
            "input[name*='captcha']",
            "input[id*='captcha']",
            "input[class*='captcha']",
            "input[placeholder*='captcha']",
            "input[aria-label*='captcha']",
            "input[name*='challenge']",
            "input[id*='challenge']",
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector)
                if await locator.count() > 0:
                    await locator.fill(answer)
                    logger.info(f"Filled image CAPTCHA answer into {selector}")
                    return
            except Exception as exc:
                logger.debug(f"Could not fill image CAPTCHA answer into {selector}: {exc}")
                continue

        # If no obvious input is found, try a generic input near the image.
        if challenge.image_selector:
            try:
                locator = page.locator(f"{challenge.image_selector} ~ input")
                if await locator.count() > 0:
                    await locator.first.fill(answer)
                    logger.info("Filled image CAPTCHA answer into sibling input")
                    return
            except Exception as exc:
                logger.debug(f"Could not fill sibling input: {exc}")

        raise CaptchaUnsolvableError(
            "Image CAPTCHA solved but no input field found to receive the answer"
        )

    @staticmethod
    def _extract_query_param(url: str, param: str) -> Optional[str]:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(url)
        values = parse_qs(parsed.query).get(param)
        return values[0] if values else None

    @staticmethod
    async def _async_sleep(seconds: float) -> None:
        import asyncio
        await asyncio.sleep(seconds)
