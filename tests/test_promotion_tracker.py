"""Tests for the adapter promotion tracker."""
from pathlib import Path

from job_agent.config import Settings
from job_agent.sites.promotion_tracker import PromotionTracker


def _tracker(tmp_path: Path, threshold: int = 3) -> PromotionTracker:
    settings = Settings(
        adapter_promotion_file=tmp_path / "promotion_status.json",
        adapter_promotion_threshold=threshold,
        _env_file=None,
    )
    return PromotionTracker(settings)


def test_promotion_after_consecutive_distinct_successes(tmp_path):
    tracker = _tracker(tmp_path, threshold=3)
    assert not tracker.is_promoted("greenhouse")
    assert not tracker.record_success("greenhouse", "job-1")
    assert not tracker.record_success("greenhouse", "job-2")
    assert tracker.record_success("greenhouse", "job-3")
    assert tracker.is_promoted("greenhouse")


def test_promotion_requires_distinct_job_ids(tmp_path):
    tracker = _tracker(tmp_path, threshold=3)
    tracker.record_success("greenhouse", "job-1")
    tracker.record_success("greenhouse", "job-1")
    tracker.record_success("greenhouse", "job-1")
    # Only one distinct success recorded, so not promoted.
    assert not tracker.is_promoted("greenhouse")


def test_failure_resets_streak(tmp_path):
    tracker = _tracker(tmp_path, threshold=3)
    tracker.record_success("greenhouse", "job-1")
    tracker.record_success("greenhouse", "job-2")
    tracker.record_failure("greenhouse", "timeout")
    tracker.record_success("greenhouse", "job-3")
    assert not tracker.is_promoted("greenhouse")
    tracker.record_success("greenhouse", "job-4")
    tracker.record_success("greenhouse", "job-5")
    assert tracker.is_promoted("greenhouse")


def test_promoted_platforms_list(tmp_path):
    tracker = _tracker(tmp_path, threshold=2)
    tracker.record_success("greenhouse", "a")
    tracker.record_success("greenhouse", "b")
    tracker.record_success("workday", "c")
    assert tracker.promoted_platforms() == ["greenhouse"]


def test_state_persists(tmp_path):
    tracker = _tracker(tmp_path, threshold=2)
    tracker.record_success("greenhouse", "a")
    tracker.record_success("greenhouse", "b")

    tracker2 = _tracker(tmp_path, threshold=2)
    assert tracker2.is_promoted("greenhouse")
