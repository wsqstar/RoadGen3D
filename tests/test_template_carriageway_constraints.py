from __future__ import annotations

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
from roadgen3d.street_layout import _bbox_intrudes_carriageway, _evaluate_slot_candidate  # noqa: E402
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
