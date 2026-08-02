"""Adapter Generator Agent — turns a DOM/form snapshot into a SiteAdapter draft.

The generator can use an LLM (OpenRouter) when an API key is available, or fall
back to deterministic keyword mapping. All generated drafts are saved for one
human approval cycle before being used for autonomous submissions.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import requests
from loguru import logger

from job_agent.config import Settings


# Map common label/name fragments to canonical field names.
FIELD_HINTS = [
    ("first", "first_name"),
    ("last", "last_name"),
    ("email", "email"),
    ("phone", "phone"),
    ("linkedin", "linkedin"),
    ("resume", "resume"),
    ("cv", "resume"),
    ("cover letter", "cover_letter"),
    ("submit", "submit"),
    ("password", "password"),
]


@dataclass
class FieldMapping:
    """A single inferred form field mapping."""

    label: str = ""
    name: str = ""
    selector: str = ""
    field_type: str = ""
    mapped_to: str = ""
    required: bool = False


@dataclass
class AdapterSpec:
    """Specification produced from a DOM snapshot."""

    platform: str = ""
    company: str = ""
    first_name_selector: str = ""
    last_name_selector: str = ""
    email_selector: str = ""
    phone_selector: str = ""
    linkedin_selector: str = ""
    resume_selector: str = ""
    submit_selector: str = ""
    login_required: bool = False
    login_email_selector: str = ""
    login_password_selector: str = ""
    login_submit_selector: str = ""
    create_account_selector: str = ""
    notes: str = ""
    fields: list[FieldMapping] = field(default_factory=list)


class AdapterGenerator:
    """Generate SiteAdapter Python code from a captured page snapshot."""

    ADAPTER_TEMPLATE = '''"""Auto-generated adapter for {platform}.

Generated from snapshot at {url}.
Review this draft before approving it for autonomous submissions.
"""
from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import Page

from job_agent.captcha import CaptchaSolver
from job_agent.models import Account, JobApplication
from job_agent.sites.base import FormChallenge, SiteAdapter
from job_agent.sites.form_utils import (
    build_form_schema,
    extract_fields,
    get_profile_values,
)


class {class_name}(SiteAdapter):
    platform = "{platform}"

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return "{hostname_fragment}" in url.lower()

    def name(self) -> str:
        return "{platform}"

    def platform_name(self) -> str:
        return self.platform

    async def is_login_required(self, page: Page) -> bool:
        {login_logic}

    async def authenticate(self, page: Page, account: Account, create_account: bool = False) -> bool:
        {auth_logic}

    async def detect_challenges(self, page: Page, dry_run: bool = False) -> None:
        # Add platform-specific challenge detection here (e.g., unsupported flows).
        pass

    async def parse_form(self, page: Page) -> dict[str, Any]:
        """Inspect the live DOM and return a structured field schema."""
        fields = await extract_fields(page)
        known_selectors = {{
            "first_name": '{first_name_selector}',
            "last_name": '{last_name_selector}',
            "email": '{email_selector}',
            "phone": '{phone_selector}',
            "linkedin": '{linkedin_selector}',
            "resume": '{resume_selector}',
            "submit": '{submit_selector}',
        }}
        # Only include selectors that were inferred from the snapshot.
        known_selectors = {{k: v for k, v in known_selectors.items() if v}}
        return build_form_schema(fields, self.platform, page.url, known_selectors)

    async def fill_application(
        self,
        page: Page,
        job: JobApplication,
        resume_path: str,
        profile: dict[str, str],
        dry_run: bool = False,
    ) -> None:
        values = get_profile_values(profile)

        await self._fill(page, '{first_name_selector}', values["first_name"])
        await self._fill(page, '{last_name_selector}', values["last_name"])
        await self._fill(page, '{email_selector}', values["email"])
        await self._fill(page, '{phone_selector}', values["phone"])
        await self._fill(page, '{linkedin_selector}', values["linkedin"])

        resume_file = Path(resume_path)
        if resume_file.exists() and '{resume_selector}':
            try:
                await page.locator('{resume_selector}').set_input_files(str(resume_file.resolve()))
                logger.info(f"Uploaded resume {{resume_file.name}}")
            except Exception as exc:
                logger.warning(f"Resume upload failed on {{self.platform}}: {{exc}}")

    async def submit(self, page: Page, dry_run: bool) -> bool:
        if not '{submit_selector}':
            return False
        if dry_run:
            logger.info("Dry-run mode: stopping before final submit")
            return True
        try:
            await page.locator('{submit_selector}').click()
            await page.wait_for_timeout(2000)
            return True
        except Exception as exc:
            logger.warning(f"Submit failed on {{self.platform}}: {{exc}}")
            return False

    async def _fill(self, page: Page, selector: str, value: str) -> None:
        if not selector or not value:
            return
        try:
            locator = page.locator(selector).first
            if await locator.count() > 0:
                await locator.fill(value)
        except Exception as exc:
            logger.debug(f"Could not fill {{selector}} on {{self.platform}}: {{exc}}")
'''

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or Settings(_env_file=None)

    def generate_from_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Generate a SiteAdapter draft from a Chrome-extension snapshot."""
        url = snapshot.get("url", "")
        platform = snapshot.get("platform") or self._detect_platform(url)
        fields = snapshot.get("fields", [])

        spec = self._infer_spec(platform, url, fields)
        code = self._generate_code(spec, url, fields)

        return {
            "platform": platform,
            "code": code,
            "spec": spec,
            "source_url": url,
        }

    def _infer_spec(self, platform: str, url: str, fields: list[dict[str, Any]]) -> AdapterSpec:
        spec = AdapterSpec(platform=platform, company=urlparse(url).netloc)

        for field in fields:
            label = (field.get("label") or "").lower()
            name = (field.get("name") or "").lower()
            field_id = (field.get("id") or "").lower()
            tag = (field.get("tag") or "").lower()
            ftype = (field.get("type") or "").lower()
            required = bool(field.get("required", False))
            selector = self._build_selector(field)

            mapping = FieldMapping(
                label=field.get("label", ""),
                name=field.get("name", ""),
                selector=selector,
                field_type=ftype or tag,
                required=required,
            )

            combined = f"{label} {name} {field_id} {ftype}"
            if "password" in combined:
                spec.login_required = True
                if not spec.login_password_selector:
                    spec.login_password_selector = selector
                continue
            if "sign in" in label or "login" in label or "log in" in label:
                spec.login_required = True
                if not spec.login_submit_selector:
                    spec.login_submit_selector = selector
                continue
            if "email" in combined and spec.login_required and not spec.login_email_selector:
                # Ambiguous: could be login or application email. Prefer application email first.
                if not spec.email_selector:
                    spec.email_selector = selector
                else:
                    spec.login_email_selector = selector
                continue

            mapped = self._map_field(combined)
            mapping.mapped_to = mapped
            if mapped == "first_name" and not spec.first_name_selector:
                spec.first_name_selector = selector
            elif mapped == "last_name" and not spec.last_name_selector:
                spec.last_name_selector = selector
            elif mapped == "email" and not spec.email_selector:
                spec.email_selector = selector
            elif mapped == "phone" and not spec.phone_selector:
                spec.phone_selector = selector
            elif mapped == "linkedin" and not spec.linkedin_selector:
                spec.linkedin_selector = selector
            elif mapped == "resume" and not spec.resume_selector:
                spec.resume_selector = selector
            elif mapped == "submit" and not spec.submit_selector:
                spec.submit_selector = selector

            spec.fields.append(mapping)

        # If we never found an application email but we saw a login email, reuse it.
        if not spec.email_selector and spec.login_email_selector:
            spec.email_selector = spec.login_email_selector
            spec.login_email_selector = ""

        return spec

    @staticmethod
    def _map_field(combined: str) -> str:
        for hint, target in FIELD_HINTS:
            if hint in combined:
                return target
        return ""

    @staticmethod
    def _build_selector(field: dict[str, Any]) -> str:
        ftype = (field.get("type") or "").lower()
        tag = (field.get("tag") or "").lower()
        name = field.get("name", "")
        field_id = field.get("id", "")
        if field_id:
            return f"#{field_id}"
        if name:
            return f'{tag}[name="{name}"]' if tag else f'[name="{name}"]'
        return f'{tag}' if tag else "input"

    @staticmethod
    def _detect_platform(url: str) -> str:
        lowered = url.lower()
        for fragment in [
            "oracle",
            "successfactors",
            "taleo",
            "workday",
            "icims",
            "greenhouse",
            "lever",
            "governmentjobs",
        ]:
            if fragment in lowered:
                return fragment
        return urlparse(url).netloc.replace("www.", "").split(".")[0]

    def _generate_code(self, spec: AdapterSpec, url: str, fields: list[dict[str, Any]]) -> str:
        """Try an LLM first; fall back to the deterministic template."""
        try:
            llm_code = self._llm_generate(spec, url, fields)
            if llm_code:
                return llm_code
        except Exception as exc:
            logger.warning(f"LLM adapter generation failed: {exc}; using heuristic template")

        return self._template_generate(spec, url)

    def _template_generate(self, spec: AdapterSpec, url: str) -> str:
        platform = spec.platform
        class_name = "".join(part.capitalize() for part in re.split(r"[-_]+", platform)) + "Adapter"
        hostname = urlparse(url).netloc
        hostname_fragment = hostname.replace("www.", "")

        login_logic = "return False"
        auth_logic = "return True"
        if spec.login_required:
            login_logic = f"return bool(await page.locator('{spec.login_password_selector or ''}').count())" if spec.login_password_selector else "return False"
            auth_logic = (
                "try:\n"
                "            await page.fill(f'{self._sel(\"login_email\")}', account.username)\n"
                "            await page.fill(f'{self._sel(\"login_password\")}', account.password)\n"
                "            await page.locator(f'{self._sel(\"login_submit\")}').click()\n"
                "            await page.wait_for_timeout(3000)\n"
                "            return True\n"
                "        except Exception as exc:\n"
                "            logger.warning(f'Authentication failed on {self.platform}: {exc}')\n"
                "            return False"
            )

        return self.ADAPTER_TEMPLATE.format(
            platform=platform,
            class_name=class_name,
            url=url,
            hostname_fragment=hostname_fragment,
            first_name_selector=spec.first_name_selector or "",
            last_name_selector=spec.last_name_selector or "",
            email_selector=spec.email_selector or "",
            phone_selector=spec.phone_selector or "",
            linkedin_selector=spec.linkedin_selector or "",
            resume_selector=spec.resume_selector or "",
            submit_selector=spec.submit_selector or "",
            login_logic=login_logic,
            auth_logic=auth_logic,
        )

    def _llm_generate(self, spec: AdapterSpec, url: str, fields: list[dict[str, Any]]) -> Optional[str]:
        api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.debug("No LLM API key configured; skipping LLM adapter generation")
            return None

        prompt = f"""You are an expert web-scraping and browser-automation engineer.

Generate a Python class that implements the SiteAdapter protocol below for the platform {spec.platform}.
The class will be used with Playwright to fill job applications on {url}.

Protocol to implement:

class SiteAdapter(ABC):
    platform: str = ""

    @classmethod
    @abstractmethod
    def can_handle(cls, url: str) -> bool: ...
    @abstractmethod
    def name(self) -> str: ...
    @abstractmethod
    def platform_name(self) -> str: ...
    @abstractmethod
    async def is_login_required(self, page: Page) -> bool: ...
    @abstractmethod
    async def authenticate(self, page, account, create_account=False) -> bool: ...
    @abstractmethod
    async def parse_form(self, page: Page) -> dict[str, Any]: ...
    @abstractmethod
    async def fill_application(self, page, job, resume_path, profile, dry_run=False) -> None: ...
    @abstractmethod
    async def submit(self, page: Page, dry_run: bool) -> bool: ...
    async def detect_challenges(self, page: Page, dry_run: bool = False) -> None: ...
    async def handle_captcha(self, page: Page, solver: CaptchaSolver) -> bool: ...

Here is the captured form structure from the site:

{json.dumps(fields, indent=2)}

Use these CSS selectors as starting points (review them):
- first_name: {spec.first_name_selector}
- last_name: {spec.last_name_selector}
- email: {spec.email_selector}
- phone: {spec.phone_selector}
- linkedin: {spec.linkedin_selector}
- resume upload: {spec.resume_selector}
- submit: {spec.submit_selector}

Requirements:
- Return ONLY the Python class code, no markdown fences, no explanation.
- Import everything you need, including `from job_agent.sites.base import SiteAdapter, FormChallenge`.
- The class name should be {spec.platform.title().replace(' ', '')}Adapter.
- `can_handle` should check that the URL contains the platform fragment `{urlparse(url).netloc}`.
- `fill_application` should split profile['my_name'] into first/last name and fill the fields.
- `submit` should NOT click if dry_run=True; return True if a submit button is found.
- Add basic try/except around interactions and log warnings with `from loguru import logger`.
"""

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "openai/gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": "You write Python SiteAdapter classes for browser automation."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 2048,
            },
            timeout=120,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        # Strip markdown fences if present.
        content = content.strip()
        if content.startswith("```python"):
            content = content[9:]
        elif content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        return content.strip()


def generate_adapter(snapshot: dict[str, Any], settings: Optional[Settings] = None) -> dict[str, Any]:
    """Convenience function to generate an adapter from a snapshot."""
    generator = AdapterGenerator(settings)
    return generator.generate_from_snapshot(snapshot)
