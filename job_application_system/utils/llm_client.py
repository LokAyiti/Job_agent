"""LLM client with headroom optimization and Databricks/OpenRouter fallback."""

import logging
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from urllib.parse import urljoin

import headroom
import requests
from openai import OpenAI

from config.settings import Settings

logger = logging.getLogger(__name__)


def _dict_to_namespace(data: dict) -> Any:
    """Recursively convert a dict to a SimpleNamespace for dot access."""
    if isinstance(data, dict):
        return SimpleNamespace(**{k: _dict_to_namespace(v) for k, v in data.items()})
    if isinstance(data, list):
        return [_dict_to_namespace(item) for item in data]
    return data


@dataclass
class FakeChoice:
    message: Any
    index: int = 0
    finish_reason: str = "stop"


@dataclass
class FakeCompletion:
    choices: list[FakeChoice]
    model: str = ""
    usage: Any = field(default_factory=lambda: SimpleNamespace())


class DatabricksChatClient:
    """Minimal OpenAI-compatible client for Databricks model serving endpoints."""

    def __init__(self, invocation_url: str, token: str) -> None:
        self.invocation_url = invocation_url
        self.token = token
        self.chat = self
        self.completions = self  # so client.chat.completions.create works

    def create(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 2000,
        **kwargs: Any,
    ) -> FakeCompletion:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        response = requests.post(
            self.invocation_url, headers=headers, json=payload, timeout=120
        )
        if not response.ok:
            raise RuntimeError(
                f"Databricks request failed: {response.status_code} - {response.text[:200]}"
            )
        data = response.json()
        choice = data["choices"][0]
        message = choice.get("message", {})
        return FakeCompletion(
            choices=[FakeChoice(message=_dict_to_namespace(message))],
            model=data.get("model", model),
            usage=_dict_to_namespace(data.get("usage", {})),
        )


class LLMClient:
    """Headroom-wrapped LLM client using OpenRouter as primary and Databricks as fallback."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model or Settings.PRIMARY_MODEL
        self.client, self.provider_name = self._build_primary_client()
        store_url = "sqlite://headroom.db"
        self.headroom_client = headroom.HeadroomClient(
            original_client=self.client,
            provider=headroom.OpenAIProvider(),
            store_url=store_url,
            default_mode="optimize",
        )

    def _build_primary_client(self) -> tuple[Any, str]:
        """Return the best available LLM client and a provider label."""
        # Prefer OpenRouter when available per user preference.
        if Settings.OPENROUTER_API_KEY:
            logger.info("Using OpenRouter primary")
            return self._build_openrouter_client(), "openrouter"

        if Settings.DATABRICKS_TOKEN and Settings.DATABRICKS_SONNET_ENDPOINT:
            try:
                client = self._build_databricks_client()
                logger.info("Using Databricks Sonnet fallback")
                return client, "databricks"
            except Exception as exc:
                logger.warning("Databricks client setup failed: %s", exc)

        raise RuntimeError(
            "No LLM provider configured. Set OPENROUTER_API_KEY or DATABRICKS_TOKEN + DATABRICKS_SONNET_ENDPOINT in .env"
        )

    def _build_openrouter_client(self) -> OpenAI:
        if not Settings.OPENROUTER_API_KEY:
            raise RuntimeError("OPENROUTER_API_KEY is not set in .env")
        return OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=Settings.OPENROUTER_API_KEY,
        )

    def _build_databricks_client(self) -> DatabricksChatClient:
        if not Settings.DATABRICKS_TOKEN:
            raise RuntimeError("DATABRICKS_TOKEN is not set in .env")
        if not Settings.DATABRICKS_SONNET_ENDPOINT:
            raise RuntimeError("DATABRICKS_SONNET_ENDPOINT is not set in .env")

        base_url = self._normalize_databricks_url(Settings.DATABRICKS_SONNET_ENDPOINT)
        logger.info("Databricks invocation URL: %s", base_url)

        return DatabricksChatClient(
            invocation_url=base_url,
            token=Settings.DATABRICKS_TOKEN,
        )

    @staticmethod
    def _normalize_databricks_url(url: str) -> str:
        """Ensure the Databricks endpoint URL uses the served-models invocation path."""
        url = url.rstrip("/")
        if "/served-models/" in url:
            return url

        # Extract endpoint name from the path, e.g. .../serving-endpoints/<name>/invocations
        match = re.search(r"/serving-endpoints/([^/]+)(?:/invocations)?$", url)
        if not match:
            raise ValueError(f"Cannot parse Databricks endpoint name from URL: {url}")

        endpoint_name = match.group(1)
        workspace_base = url[: match.start()]
        new_path = f"/serving-endpoints/{endpoint_name}/served-models/{endpoint_name}/invocations"
        return urljoin(workspace_base, new_path)

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 2000,
    ) -> str:
        """Send a chat completion request and return the text content."""
        try:
            response = self.headroom_client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = response.choices[0].message.content
            if not content:
                raise RuntimeError("LLM returned empty content")
            return content.strip()
        except Exception as exc:
            logger.error("LLM request failed (%s): %s", self.provider_name, exc)
            raise


llm_client = LLMClient()
