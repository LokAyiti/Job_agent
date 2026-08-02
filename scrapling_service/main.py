"""FastAPI service wrapping Scrapling fetchers and spiders."""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from scrapling.fetchers import DynamicFetcher, Fetcher, StealthyFetcher
from scrapling.parser import Selector

from scrapling_service.proxy import as_dicts, as_strings, parse_proxy_list, proxies_from_env
from scrapling_service.spiders import JobDiscoverySpider

# Import project settings so the adapter generator can load API keys.
try:
    from job_agent.config import Settings
    from job_agent.sites.adapter_generator import AdapterGenerator

    _settings = Settings(_env_file=None)
except Exception:
    _settings = None
    AdapterGenerator = None

app = FastAPI(title="Job Agent Scrapling Service", version="1.0.0")


ADAPTER_DRAFTS_DIR = Path(os.environ.get("ADAPTER_DRAFTS_DIR", "/app/data/adapter_drafts"))


class FetchRequest(BaseModel):
    url: str
    impersonate: str = Field(default="chrome")
    stealthy_headers: bool = Field(default=True)


class StealthFetchRequest(BaseModel):
    url: str
    headless: bool = Field(default=True)
    solve_cloudflare: bool = Field(default=True)
    network_idle: bool = Field(default=True)
    proxy: Optional[Dict[str, Any]] = None


class DynamicFetchRequest(BaseModel):
    url: str
    headless: bool = Field(default=True)
    disable_resources: bool = Field(default=False)
    network_idle: bool = Field(default=True)
    proxy: Optional[Dict[str, Any]] = None


class SpiderRunRequest(BaseModel):
    start_urls: List[str]
    link_patterns: Optional[List[str]] = None
    title_patterns: Optional[List[str]] = None
    max_depth: int = Field(default=1, ge=0)
    allowed_domains: Optional[List[str]] = None
    use_stealth: bool = Field(default=False)
    proxy_list: Optional[str] = None
    concurrent_requests: int = Field(default=4, ge=1, le=20)
    crawldir: Optional[str] = None


class SelectRequest(BaseModel):
    html: str
    url: Optional[str] = None
    selectors: Dict[str, str]
    auto_save: bool = Field(default=True)
    adaptive: bool = Field(default=True)


class ExtensionSnapshot(BaseModel):
    url: str
    html: str
    fields: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    platform_hint: Optional[str] = None


class SubmitCloudflareRequest(BaseModel):
    url: str
    proxy: Optional[Dict[str, Any]] = None


def _page_to_dict(page) -> Dict[str, Any]:
    """Convert a Scrapling response page to a serializable dict."""
    return {
        "url": getattr(page, "url", ""),
        "status": getattr(page, "status", 200),
        "html": getattr(page, "html", "") or "",
        "text": getattr(page, "text", "") or "",
        "title": _safe_title(page),
    }


def _safe_title(page) -> Optional[str]:
    try:
        title = page.css("title::text").get("")
        return title.strip() if title else None
    except Exception:
        return None


def _extract_form_fields(html_text: str) -> List[Dict[str, Any]]:
    """Extract visible form fields from HTML for the adapter generator."""
    fields = []
    try:
        selector = Selector(html_text)
        for idx, element in enumerate(selector.css("input, select, textarea")):
            tag = element.name or ""
            field_type = element.attrib.get("type", "") if tag == "input" else tag
            name = element.attrib.get("name", "") or element.attrib.get("id", "")
            placeholder = element.attrib.get("placeholder", "")
            aria_label = element.attrib.get("aria-label", "")
            label = _find_label(selector, name, element.attrib.get("id", ""))
            required = "required" in element.attrib or element.attrib.get("aria-required", "false").lower() == "true"
            options = []
            if tag == "select":
                options = [opt.text.strip() for opt in element.css("option") if opt.text]
            fields.append(
                {
                    "index": idx,
                    "tag": tag,
                    "type": field_type,
                    "name": name,
                    "id": element.attrib.get("id", ""),
                    "label": label or aria_label or placeholder,
                    "required": required,
                    "options": options,
                }
            )
    except Exception as exc:
        app.logger.warning(f"Form-field extraction failed: {exc}")
    return fields


def _find_label(selector, name: str, field_id: str) -> Optional[str]:
    if field_id:
        label = selector.css(f"label[for='{field_id}']::text").get("")
        if label:
            return label.strip()
    if name:
        label = selector.css(f"label[for='{name}']::text").get("")
        if label:
            return label.strip()
    return None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/fetch")
def fetch_endpoint(req: FetchRequest):
    try:
        page = Fetcher.fetch(
            req.url,
            impersonate=req.impersonate,
            stealthy_headers=req.stealthy_headers,
        )
        return _page_to_dict(page)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/stealth-fetch")
def stealth_fetch_endpoint(req: StealthFetchRequest):
    try:
        page = StealthyFetcher.fetch(
            req.url,
            headless=req.headless,
            solve_cloudflare=req.solve_cloudflare,
            network_idle=req.network_idle,
            proxy=req.proxy,
        )
        result = _page_to_dict(page)
        result["form_fields"] = _extract_form_fields(result.get("html", "") or "")
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/dynamic-fetch")
def dynamic_fetch_endpoint(req: DynamicFetchRequest):
    try:
        page = DynamicFetcher.fetch(
            req.url,
            headless=req.headless,
            disable_resources=req.disable_resources,
            network_idle=req.network_idle,
            proxy=req.proxy,
        )
        result = _page_to_dict(page)
        result["form_fields"] = _extract_form_fields(result.get("html", "") or "")
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/spider/run")
def spider_run_endpoint(req: SpiderRunRequest):
    try:
        spider = JobDiscoverySpider(
            start_urls=req.start_urls,
            link_patterns=req.link_patterns,
            title_patterns=req.title_patterns,
            max_depth=req.max_depth,
            allowed_domains=req.allowed_domains,
            use_stealth=req.use_stealth,
            proxy_list=req.proxy_list,
            crawldir=req.crawldir,
        )
        spider.concurrent_requests = req.concurrent_requests
        result = spider.start()
        return {
            "items": result.items.to_dict() if hasattr(result.items, "to_dict") else list(result.items),
            "stats": getattr(result, "stats", {}),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/select")
def select_endpoint(req: SelectRequest):
    try:
        selector = Selector(req.html)
        output = {}
        for name, css_or_xpath in req.selectors.items():
            try:
                if css_or_xpath.startswith("xpath:"):
                    query = css_or_xpath[6:]
                    el = selector.xpath(query)
                else:
                    el = selector.css(css_or_xpath)
                output[name] = {
                    "get": el.get("") if hasattr(el, "get") else None,
                    "getall": el.getall() if hasattr(el, "getall") else list(el),
                }
            except Exception as exc:
                output[name] = {"error": str(exc)}
        return {"url": req.url, "results": output}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/submit/cloudflare")
def submit_cloudflare_endpoint(req: SubmitCloudflareRequest):
    """Return a stealth-rendered snapshot of a submission/ATS page."""
    try:
        page = StealthyFetcher.fetch(
            req.url,
            headless=True,
            solve_cloudflare=True,
            network_idle=True,
            proxy=req.proxy,
        )
        result = _page_to_dict(page)
        result["form_fields"] = _extract_form_fields(result.get("html", "") or "")
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/extension/snapshot")
def extension_snapshot_endpoint(req: ExtensionSnapshot):
    """Receive a DOM snapshot from the Chrome extension and persist it for review."""
    ADAPTER_DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    platform = _sanitize_platform(req.platform_hint or _detect_platform(req.url) or "unknown")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{platform}_{timestamp}.json"
    snapshot_path = ADAPTER_DRAFTS_DIR / filename

    payload = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "url": req.url,
        "platform": platform,
        "html": req.html,
        "fields": req.fields,
        "metadata": req.metadata,
    }
    snapshot_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"snapshot_path": str(snapshot_path), "platform": platform}


@app.post("/extension/generate-adapter")
def extension_generate_adapter_endpoint(req: ExtensionSnapshot):
    """Receive a DOM snapshot and immediately draft a SiteAdapter for review."""
    if AdapterGenerator is None or _settings is None:
        raise HTTPException(status_code=500, detail="Adapter generator is not available")

    ADAPTER_DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    platform = _sanitize_platform(req.platform_hint or _detect_platform(req.url) or "unknown")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    snapshot_filename = f"{platform}_{timestamp}.json"
    snapshot_path = ADAPTER_DRAFTS_DIR / snapshot_filename

    payload = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "url": req.url,
        "platform": platform,
        "html": req.html,
        "fields": req.fields,
        "metadata": req.metadata,
    }
    snapshot_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    generator = AdapterGenerator(_settings)
    result = generator.generate_from_snapshot(payload)
    code = result["code"]

    draft_filename = f"{platform}_adapter_{timestamp}.py"
    draft_path = ADAPTER_DRAFTS_DIR / draft_filename
    draft_path.write_text(code, encoding="utf-8")

    return {
        "snapshot_path": str(snapshot_path),
        "draft_path": str(draft_path),
        "platform": platform,
        "note": "Draft generated for manual review. Run `python -m job_agent.cli approve-adapter --platform <platform>` after a successful dry-run.",
    }


def _sanitize_platform(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "", value.lower())[:50]


def _detect_platform(url: str) -> Optional[str]:
    return JobDiscoverySpider._detect_platform(url)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8723)
