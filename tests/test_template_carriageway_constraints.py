from __future__ import annotations

import random
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.entrance_analysis import PlacedAssetRegistry  # noqa: E402
from roadgen3d.street_layout import (  # noqa: E402
    _bbox_intrudes_carriageway,
    _coerce_compose_config_for_rebuild,
    _evaluate_slot_candidate,
    _sample_pose_for_slot,
)
from roadgen3d.types import StreetComposeConfig  # noqa: E402


class _EmptySpatialHash:
    def query_bbox(self, _bbox):
        return ()

    def query_radius(self, _point, _radius):
        return ()


def _template_config() -> StreetComposeConfig:
    return StreetComposeConfig(
        query="template street",
        length_m=80.0,
        road_width_m=8.0,
        sidewalk_width_m=2.4,
        lane_count=2,
        density=1.0,
        seed=42,
        topk_per_category=1,
        max_trials_per_slot=2,
    )


def test_bbox_intrudes_carriageway_uses_scene_centered_template_bounds():
    config = _template_config()

    assert _bbox_intrudes_carriageway(
        (-0.5, 0.5, -0.5, 0.5),
        placement_ctx=None,
        config=config,
    )
    assert not _bbox_intrudes_carriageway(
        (-0.5, 0.5, 4.8, 5.8),
        placement_ctx=None,
        config=config,
    )


def test_evaluate_slot_candidate_rejects_template_carriageway_intrusion():
    config = _template_config()
    slot = SimpleNamespace(side="left", x_center_m=0.0, z_center_m=4.8)
    entry = SimpleNamespace(half_x=0.5, half_z=0.5)
    scale_info = {
        "applied_scale": 1.0,
        "native_size_m": {},
        "canonical_target": {},
        "asset_scale_mode": "canonical_v1",
        "scale_fallback_used": False,
    }

    blocked_candidate, blocked_reason = _evaluate_slot_candidate(
        candidate={"point_xz": (0.0, 0.0), "yaw_deg": 0.0, "tier": "tier_optional_sampling", "anchor_distance_m": None},
        slot=slot,
        category="bench",
        band_width_m=1.0,
        entry=entry,
        scale_info=scale_info,
        placements=(),
        spatial_hash=_EmptySpatialHash(),
        existing_bboxes=(),
        placement_ctx=None,
        theme_segment=None,
        road_segment_graph=None,
        theme_poi_points={},
        poi_ctx=None,
        rule_set=None,
        config=config,
        entrance_registry=PlacedAssetRegistry(),
        carriageway_boundary=None,
        entrance_points_xz=(),
    )

    assert blocked_candidate is None
    assert blocked_reason == "intrudes_carriageway"

    resolved_candidate, resolved_reason = _evaluate_slot_candidate(
        candidate={"point_xz": (0.0, 5.5), "yaw_deg": 0.0, "tier": "tier_optional_sampling", "anchor_distance_m": None},
        slot=slot,
        category="bench",
        band_width_m=1.0,
        entry=entry,
        scale_info=scale_info,
        placements=(),
        spatial_hash=_EmptySpatialHash(),
        existing_bboxes=(),
        placement_ctx=None,
        theme_segment=None,
        road_segment_graph=None,
        theme_poi_points={},
        poi_ctx=None,
        rule_set=None,
        config=config,
        entrance_registry=PlacedAssetRegistry(),
        carriageway_boundary=None,
        entrance_points_xz=(),
    )

    assert resolved_reason is None
    assert resolved_candidate is not None


def test_sample_pose_for_slot_left_right_zero_z_center_falls_to_sidewalk_band():
    rng = random.Random(42)
    _, z_left, _ = _sample_pose_for_slot(
        slot_x_center=0.0,
        slot_z_center=0.0,
        slot_side="left",
        slot_spacing_m=1.2,
        band_width_m=1.0,
        road_width_m=8.0,
        sidewalk_width_m=2.4,
        length_m=80.0,
        rng=rng,
    )
    assert z_left > 4.9

    rng = random.Random(42)
    _, z_right, _ = _sample_pose_for_slot(
        slot_x_center=0.0,
        slot_z_center=0.0,
        slot_side="right",
        slot_spacing_m=1.2,
        band_width_m=1.0,
        road_width_m=8.0,
        sidewalk_width_m=2.4,
        length_m=80.0,
        rng=rng,
    )
    assert z_right < -4.9


def test_sample_pose_for_slot_side_constraints_pushes_near_center_slot_outside_carriageway():
    rng = random.Random(42)
    _, z_left, _ = _sample_pose_for_slot(
        slot_x_center=0.0,
        slot_z_center=3.5,
        slot_side="left",
        slot_spacing_m=1.2,
        band_width_m=1.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        length_m=80.0,
        rng=rng,
    )
    assert z_left > 4.9

    rng = random.Random(42)
    _, z_right, _ = _sample_pose_for_slot(
        slot_x_center=0.0,
        slot_z_center=-3.5,
        slot_side="right",
        slot_spacing_m=1.2,
        band_width_m=1.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        length_m=80.0,
        rng=rng,
    )
    assert z_right < -4.9


def test_coerce_compose_config_for_rebuild_reconstructs_from_minimal_payload():
    layout_payload = {
        "summary": {
            "query": "summary query",
            "layout_mode": "graph_template",
            "constraint_mode": "off",
            "spatial_context": {"length_m": 140.0, "road_half_width_m": 4.0},
        },
        "config": {
            "topk_per_category": "17",
            "lane_count": "4",
            "max_trials_per_slot": "5",
            "allow_solver_fallback": "false",
            "minimum_category_presence": "bench,mailbox",
        },
    }

    config = _coerce_compose_config_for_rebuild(layout_payload)

    assert config.query == "summary query"
    assert config.length_m == 140.0
    assert config.road_width_m == 8.0
    assert config.sidewalk_width_m == 2.4
    assert config.topk_per_category == 17
    assert config.lane_count == 4
    assert config.max_trials_per_slot == 5
    assert config.allow_solver_fallback is False
    assert config.minimum_category_presence == ("bench", "mailbox")
    assert config.layout_mode == "graph_template"
    assert config.constraint_mode == "off"
