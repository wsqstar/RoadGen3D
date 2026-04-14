"""Tests for diff_engine layout comparison utilities."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.diff_engine import compute_scene_diff


def _make_layout(
    *,
    config: dict | None = None,
    summary: dict | None = None,
    placements: list[dict] | None = None,
) -> dict:
    return {
        "config": dict(config or {}),
        "summary": dict(summary or {}),
        "placements": list(placements or []),
    }


class TestIdenticalLayouts:
    def test_all_diffs_are_empty(self):
        layout = _make_layout(
            config={"lane_count": 2, "density": 0.8},
            summary={"instance_count": 5, "overlap_rate": 0.1},
            placements=[
                {
                    "instance_id": "t1",
                    "asset_id": "tree_01",
                    "category": "tree",
                    "position_xyz": [1.0, 0.0, 2.0],
                }
            ],
        )
        result = compute_scene_diff(layout, layout)

        assert result["config_diff"]["added"] == {}
        assert result["config_diff"]["removed"] == {}
        assert result["config_diff"]["changed"] == {}

        metrics = result["metrics_diff"]["metrics"]
        for m in metrics:
            assert m["delta"] == 0.0, m["key"]

        pd = result["placements_diff"]
        assert pd["total_delta"] == 0
        assert pd["added_instances"] == []
        assert pd["deleted_instances"] == []
        assert pd["moved_instances"] == []
        for stat in pd["category_stats"]:
            assert stat["delta"] == 0
            assert stat["added"] == 0
            assert stat["deleted"] == 0
            assert stat["moved"] == 0


class TestConfigDiff:
    def test_field_change_captured(self):
        a = _make_layout(config={"lane_count": 2, "density": 0.8})
        b = _make_layout(config={"lane_count": 4, "density": 0.8})
        result = compute_scene_diff(a, b)

        changed = result["config_diff"]["changed"]
        assert "lane_count" in changed
        assert changed["lane_count"] == {"old": 2, "new": 4}

    def test_added_and_removed_fields(self):
        a = _make_layout(config={"old_key": 1})
        b = _make_layout(config={"new_key": 2})
        result = compute_scene_diff(a, b)

        assert result["config_diff"]["removed"] == {"old_key": 1}
        assert result["config_diff"]["added"] == {"new_key": 2}


class TestMetricsDiff:
    def test_numeric_delta(self):
        a = _make_layout(summary={"instance_count": 10, "overlap_rate": 0.2})
        b = _make_layout(summary={"instance_count": 12, "overlap_rate": 0.2})
        result = compute_scene_diff(a, b)

        metrics = {m["key"]: m for m in result["metrics_diff"]["metrics"]}
        assert metrics["instance_count"]["delta"] == 2.0
        assert metrics["instance_count"]["old"] == 10
        assert metrics["instance_count"]["new"] == 12

        overlap = metrics.get("overlap_rate")
        if overlap is not None:
            assert overlap["delta"] == 0.0


class TestPlacementsDiff:
    def test_move_detected_when_distance_exceeds_threshold(self):
        a = _make_layout(
            placements=[
                {
                    "instance_id": "b1",
                    "asset_id": "bench_01",
                    "category": "bench",
                    "position_xyz": [0.0, 0.0, 0.0],
                }
            ]
        )
        b = _make_layout(
            placements=[
                {
                    "instance_id": "b1",
                    "asset_id": "bench_01",
                    "category": "bench",
                    "position_xyz": [0.5, 0.0, 0.0],
                }
            ]
        )
        result = compute_scene_diff(a, b)

        pd = result["placements_diff"]
        assert pd["total_delta"] == 0
        assert len(pd["moved_instances"]) == 1
        assert pd["moved_instances"][0]["distance_m"] == 0.5
        assert pd["moved_instances"][0]["category"] == "bench"

        stat = pd["category_stats"][0]
        assert stat["moved"] == 1
        assert stat["matched"] == 1

    def test_small_move_not_counted_as_moved(self):
        a = _make_layout(
            placements=[
                {
                    "instance_id": "b1",
                    "asset_id": "bench_01",
                    "category": "bench",
                    "position_xyz": [0.0, 0.0, 0.0],
                }
            ]
        )
        b = _make_layout(
            placements=[
                {
                    "instance_id": "b1",
                    "asset_id": "bench_01",
                    "category": "bench",
                    "position_xyz": [0.1, 0.0, 0.1],
                }
            ]
        )
        result = compute_scene_diff(a, b)

        pd = result["placements_diff"]
        assert pd["moved_instances"] == []
        assert pd["category_stats"][0]["moved"] == 0

    def test_addition_and_deletion(self):
        a = _make_layout(
            placements=[
                {
                    "instance_id": "t1",
                    "asset_id": "tree_01",
                    "category": "tree",
                    "position_xyz": [1.0, 0.0, 1.0],
                }
            ]
        )
        b = _make_layout(
            placements=[
                {
                    "instance_id": "t1",
                    "asset_id": "tree_01",
                    "category": "tree",
                    "position_xyz": [1.0, 0.0, 1.0],
                },
                {
                    "instance_id": "l1",
                    "asset_id": "lamp_01",
                    "category": "lamp",
                    "position_xyz": [2.0, 0.0, 2.0],
                },
            ]
        )
        result = compute_scene_diff(a, b)

        pd = result["placements_diff"]
        assert pd["total_delta"] == 1
        assert len(pd["added_instances"]) == 1
        assert pd["added_instances"][0]["category"] == "lamp"
        assert pd["deleted_instances"] == []

        tree_stat = next(s for s in pd["category_stats"] if s["category"] == "tree")
        lamp_stat = next(s for s in pd["category_stats"] if s["category"] == "lamp")
        assert tree_stat["delta"] == 0
        assert lamp_stat["delta"] == 1
        assert lamp_stat["added"] == 1

    def test_deletion_reported_from_a(self):
        a = _make_layout(
            placements=[
                {
                    "instance_id": "h1",
                    "asset_id": "hydrant_01",
                    "category": "hydrant",
                    "position_xyz": [3.0, 0.0, 3.0],
                }
            ]
        )
        b = _make_layout(placements=[])
        result = compute_scene_diff(a, b)

        pd = result["placements_diff"]
        assert pd["total_delta"] == -1
        assert len(pd["deleted_instances"]) == 1
        assert pd["deleted_instances"][0]["category"] == "hydrant"
        assert pd["added_instances"] == []

        stat = pd["category_stats"][0]
        assert stat["deleted"] == 1
