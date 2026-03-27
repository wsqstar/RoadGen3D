from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.metaurban_scene_bridge import build_metaurban_scene_bridge
from roadgen3d.types import StreetComposeConfig


def _build_config() -> StreetComposeConfig:
    return StreetComposeConfig(
        query="campus gateway boulevard",
        length_m=96.0,
        road_width_m=10.5,
        sidewalk_width_m=3.0,
        lane_count=3,
        density=1.0,
        seed=42,
        topk_per_category=20,
        max_trials_per_slot=30,
    )


def test_build_metaurban_scene_bridge_builds_synthetic_corridor():
    pytest.importorskip("shapely")

    bridge = build_metaurban_scene_bridge(_build_config(), plan_id="hkust_gz_gate")

    assert bridge.projected_features.roads
    assert bridge.projected_features.bbox_m[0] < bridge.projected_features.bbox_m[2]
    assert bridge.projected_features.bbox_m[1] < bridge.projected_features.bbox_m[3]
    assert not bridge.placement_context.carriageway.is_empty
    assert not bridge.placement_context.sidewalk_zone.is_empty
    assert len(bridge.placement_context.road_references) == len(bridge.projected_features.roads)
    assert bridge.summary_metadata["reference_plan_id"] == "hkust_gz_gate"
    assert bridge.summary_metadata["total_network_length_m"] > 0.0
    assert bridge.summary_metadata["synthetic_road_count"] == len(bridge.projected_features.roads)
