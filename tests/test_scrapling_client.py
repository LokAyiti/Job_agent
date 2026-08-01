"""Tests for the Scrapling client and service abstraction."""
from unittest.mock import MagicMock, patch

import pytest

from job_agent.scrapling_client import ScraplingClient, ScraplingResponse, ScraplingServiceError


def test_client_fallback_uses_requests_get():
    from job_agent.config import Settings

    client = ScraplingClient(Settings(scrapling_use_service=False, _env_file=None))
    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock()
        mock_get.return_value.status_code = 200
        mock_get.return_value.text = "<html></html>"
        mock_get.return_value.url = "https://example.com"
        mock_get.return_value.headers = {}
        mock_get.return_value.cookies = {}

        response = client.fetch("https://example.com")
        assert response.status == 200
        assert response.text == "<html></html>"
        mock_get.assert_called_once()


def test_client_service_disabled_run_spider_returns_empty():
    from job_agent.config import Settings

    client = ScraplingClient(Settings(scrapling_use_service=False, _env_file=None))
    assert client.run_spider({"start_urls": ["https://example.com"]}) == []


def test_client_response_prefers_raw_json():
    raw = MagicMock()
    raw.json.return_value = {"jobs": [{"title": "Dev"}]}
    response = ScraplingResponse(
        url="https://example.com",
        status=200,
        text="ignored",
        html="ignored",
        _raw_response=raw,
    )
    assert response.json() == {"jobs": [{"title": "Dev"}]}


def test_client_response_raise_for_status_uses_raw_response():
    raw = MagicMock()
    raw.raise_for_status = MagicMock()
    response = ScraplingResponse(
        url="https://example.com",
        status=999,  # ignored because raw is present
        text="",
        html="",
        _raw_response=raw,
    )
    response.raise_for_status()
    raw.raise_for_status.assert_called_once()


def test_client_service_unreachable_raises():
    from job_agent.config import Settings

    client = ScraplingClient(Settings(scrapling_use_service=True, scrapling_service_url="http://localhost:1", _env_file=None))
    with pytest.raises(ScraplingServiceError):
        client.fetch("https://example.com")
