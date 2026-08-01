"""Approval registry for auto-generated site adapters.

The adapter generator writes draft SiteAdapter classes to
`job_agent/sites/generated_drafts/`. This registry tracks which drafts have
been reviewed/approved and can move an approved adapter into the live runtime.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from job_agent.config import Settings


class ApprovalRegistry:
    """Track adapter drafts and promote approved ones to the live registry."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        drafts_dir: Optional[Path] = None,
        generated_dir: Optional[Path] = None,
        registry_file: Optional[Path] = None,
    ):
        self.settings = settings or Settings(_env_file=None)
        self.drafts_dir = drafts_dir or self.settings.adapter_drafts_dir
        self.generated_dir = generated_dir or Path(__file__).parent / "generated_drafts"
        self.registry_file = registry_file or self.settings.adapter_registry_file

        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self.generated_dir.mkdir(parents=True, exist_ok=True)
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)

    def _load_registry(self) -> dict[str, Any]:
        if not self.registry_file.exists():
            return {"drafts": [], "approved": []}
        try:
            with open(self.registry_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning(f"Could not read adapter registry {self.registry_file}: {exc}")
            return {"drafts": [], "approved": []}

    def _save_registry(self, data: dict[str, Any]) -> None:
        with open(self.registry_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def add_draft(self, platform: str, code: str, snapshot_path: Optional[str] = None) -> Path:
        """Save a generated adapter as a draft and record it for review."""
        platform = self._sanitize(platform)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        draft_path = self.drafts_dir / f"{platform}_adapter_{timestamp}.py"
        draft_path.write_text(code, encoding="utf-8")

        registry = self._load_registry()
        registry["drafts"].append(
            {
                "platform": platform,
                "draft_path": str(draft_path),
                "snapshot_path": snapshot_path,
                "status": "draft",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._save_registry(registry)
        logger.info(f"Saved adapter draft for {platform} at {draft_path}")
        return draft_path

    def approve(self, platform: str) -> Path:
        """Promote the most recent draft for a platform to the live registry."""
        platform = self._sanitize(platform)
        registry = self._load_registry()

        drafts = [d for d in registry.get("drafts", []) if d["platform"] == platform]
        if not drafts:
            raise ValueError(f"No draft adapter found for platform: {platform}")

        latest = max(drafts, key=lambda d: d.get("created_at", ""))
        draft_path = Path(latest["draft_path"])
        if not draft_path.exists():
            raise FileNotFoundError(f"Draft file not found: {draft_path}")

        approved_path = self.generated_dir / f"{platform}_adapter.py"
        shutil.copy(draft_path, approved_path)

        latest["status"] = "approved"
        approved_list = registry.setdefault("approved", [])
        if platform not in approved_list:
            approved_list.append(platform)
        self._save_registry(registry)
        logger.info(f"Approved adapter for {platform}; copied to {approved_path}")
        return approved_path

    def reject(self, platform: str) -> None:
        """Mark a draft as rejected."""
        platform = self._sanitize(platform)
        registry = self._load_registry()
        for d in registry.get("drafts", []):
            if d["platform"] == platform and d.get("status") == "draft":
                d["status"] = "rejected"
        approved = registry.get("approved", [])
        if platform in approved:
            approved.remove(platform)
        self._save_registry(registry)
        logger.info(f"Rejected adapter drafts for {platform}")

    def approved_platforms(self) -> list[str]:
        return self._load_registry().get("approved", [])

    def list_drafts(self, platform: Optional[str] = None) -> list[dict[str, Any]]:
        drafts = self._load_registry().get("drafts", [])
        if platform:
            platform = self._sanitize(platform)
            drafts = [d for d in drafts if d["platform"] == platform]
        return drafts

    @staticmethod
    def _sanitize(value: str) -> str:
        return "".join(c for c in value.lower() if c.isalnum() or c in "_-").strip()[:50]


def build_approval_registry(settings: Optional[Settings] = None) -> ApprovalRegistry:
    return ApprovalRegistry(settings)
