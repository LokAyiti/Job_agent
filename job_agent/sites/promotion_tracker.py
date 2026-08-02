"""Track adapter promotion eligibility from consecutive dry-run successes.

Promotion criteria:
  - N consecutive successful dry-runs
  - across N distinct job postings
  - where N is configurable via ``adapter_promotion_threshold`` (default 3).

Once promoted, a platform is considered trusted for real submissions until a
failure resets its streak.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from job_agent.config import Settings, get_settings


class PromotionTracker:
    """Record dry-run outcomes and decide when a platform can be trusted."""

    DEFAULT_THRESHOLD = 3

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.state_file = getattr(
            self.settings, "adapter_promotion_file", Path("data/promotion_status.json")
        )
        if not self.state_file.is_absolute():
            self.state_file = Path(__file__).resolve().parent.parent.parent / self.state_file
        self.threshold = int(
            getattr(self.settings, "adapter_promotion_threshold", self.DEFAULT_THRESHOLD)
        )
        self._state: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {"platforms": {}}
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"Could not load promotion state from {self.state_file}: {exc}")
            return {"platforms": {}}

    def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self._state, indent=2, default=str), encoding="utf-8")

    def _platform_state(self, platform: str) -> dict[str, Any]:
        return self._state["platforms"].setdefault(
            platform,
            {"streak": [], "promoted": False, "last_failure_reason": None},
        )

    def record_success(self, platform: str, job_id: str) -> bool:
        """Record a successful dry-run for ``platform``.

        Returns ``True`` if the platform was newly promoted by this success.
        """
        pstate = self._platform_state(platform)
        if job_id not in pstate["streak"]:
            pstate["streak"].append(job_id)

        newly_promoted = False
        if len(pstate["streak"]) >= self.threshold and not pstate["promoted"]:
            pstate["promoted"] = True
            newly_promoted = True
            logger.info(
                f"Platform '{platform}' promoted after {len(pstate['streak'])} "
                f"consecutive dry-run successes across distinct postings"
            )

        self._save()
        return newly_promoted

    def record_failure(self, platform: str, reason: str) -> None:
        """Record a dry-run failure and reset the platform's success streak."""
        pstate = self._platform_state(platform)
        if pstate["streak"]:
            logger.info(
                f"Resetting promotion streak for '{platform}' after failure: {reason}"
            )
        pstate["streak"] = []
        pstate["last_failure_reason"] = reason
        # We do NOT demote an already-promoted platform here; that requires
        # explicit human action. A failure simply stops further auto-promotion.
        self._save()

    def is_promoted(self, platform: str) -> bool:
        return bool(self._state["platforms"].get(platform, {}).get("promoted", False))

    def promoted_platforms(self) -> list[str]:
        return [
            platform
            for platform, pstate in self._state["platforms"].items()
            if pstate.get("promoted", False)
        ]

    def streak_for(self, platform: str) -> list[str]:
        return list(self._platform_state(platform).get("streak", []))
