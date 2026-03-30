from __future__ import annotations

import random
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.graph_template_scene_bridge import build_graph_template_scene_bridge
from roadgen3d.metaurban_procedural import MetaUrbanProceduralConfig, build_metaurban_segment_graph
from roadgen3d.reference_annotation import build_reference_annotation_compose_config
from roadgen3d.street_band_semantics import resolve_band_by_alias
from roadgen3d.street_layout import (
    _curated_locked_row_for_category,
    _sample_pose_osm_for_segment,
    _validate_curated_locked_assets,
)
from roadgen3d.street_program import infer_street_program
from roadgen3d.types import StreetComposeConfig


def _build_config(*, layout_mode: str = "graph_template") -> StreetComposeConfig:
    return StreetComposeConfig(
        query="campus gateway boulevard",
        length_m=96.0,
        road_width_m=13.2,
        sidewalk_width_m=3.0,
        lane_count=3,
        density=1.0,
        seed=42,
        topk_per_category=20,
        max_trials_per_slot=20,
        layout_mode=layout_mode,
        constraint_mode="off",
        curated_street_assets_profile="fixed_hq_v1",
    )


def test_graph_template_bridge_populates_detailed_strip_profiles():
    pytest.importorskip("shapely")

    bridge = build_graph_template_scene_bridge(
        build_reference_annotation_compose_config({"segment_length_m": 9.0, "road_width_m": 13.2}),
        template_id="hkust_gz_gate",
    )

    assert bridge.placement_context.detailed_strip_profiles
    assert any(
        str(profile.get("kind", "")) == "nearroad_furnishing"
        for profile in bridge.placement_context.detailed_strip_profiles
    )
    assert "left_nearroad_furnishing" in bridge.placement_context.strip_zones
    assert bridge.placement_context.segment_strip_zones


def test_metaurban_segment_graph_populates_strip_metadata():
    graph = build_metaurban_segment_graph(
        MetaUrbanProceduralConfig(
            seed=17,
            block_sequence="S",
            lane_count=3,
            lane_width_m=3.3,
            segment_length_m=10.0,
        )
    )

    node = graph.nodes[0]
    assert node.road_width_m > 0.0
    assert node.cross_section_width_m > node.road_width_m
    assert any(strip.kind == "nearroad_furnishing" for strip in node.cross_section_strips)
    assert any(strip.kind == "clear_sidewalk" for strip in node.cross_section_strips)
    assert any(hint.strip_kind == "frontage_reserve" for hint in node.metaurban_asset_hints)
    assert any("Building" in hint.suggested_assets for hint in node.metaurban_asset_hints)


def test_infer_street_program_uses_detailed_strip_bands_for_corridor_graph():
    pytest.importorskip("shapely")

    bridge = build_graph_template_scene_bridge(
        build_reference_annotation_compose_config({"segment_length_m": 9.0, "road_width_m": 13.2}),
        template_id="hkust_gz_gate",
    )
    config = _build_config()

    program = infer_street_program(
        config,
        available_categories=("lamp", "trash", "hydrant", "tree", "mailbox", "bollard"),
        placement_context=bridge.placement_context,
    )

    assert resolve_band_by_alias(program.bands, band_name="nearroad_furnishing", side="left") is not None
    assert resolve_band_by_alias(program.bands, band_name="clear_sidewalk", side="right") is not None
    assert resolve_band_by_alias(program.bands, band_name="frontage_reserve", side="left") is not None


def test_sample_pose_osm_for_segment_stays_inside_target_strip():
    shapely = pytest.importorskip("shapely")
    from shapely.geometry import box

    carriageway = box(-8.0, -2.0, 8.0, 2.0)
    target_strip = box(-8.0, 3.5, 8.0, 5.0)
    placement_ctx = SimpleNamespace(
        carriageway=carriageway,
        sidewalk_zone=box(-8.0, 2.0, 8.0, 8.0),
        left_sidewalk_zone=box(-8.0, 2.0, 8.0, 8.0),
        right_sidewalk_zone=box(-8.0, -8.0, 8.0, -2.0),
        carriageway_width_m=4.0,
        strip_zones={"left_nearroad_furnishing": target_strip},
        segment_strip_zones={"seg_0001": {"left_nearroad_furnishing": target_strip}},
    )
    segment_node = SimpleNamespace(
        segment_id="seg_0001",
        start_xy=(-8.0, 0.0),
        end_xy=(8.0, 0.0),
        center_xy=(0.0, 0.0),
    )

    pose = _sample_pose_osm_for_segment(
        "lamp",
        placement_ctx,
        random.Random(7),
        segment_node=segment_node,
        slot_side="left",
        slot_band_name="nearroad_furnishing",
        band_width_m=1.5,
    )

    assert pose is not None
    point = shapely.Point(float(pose[0]), float(pose[1]))
    assert target_strip.buffer(1e-6).contains(point)
    assert not carriageway.buffer(1e-6).contains(point)


def test_curated_asset_lock_falls_back_when_locked_asset_is_missing():
    config = _build_config(layout_mode="osm")
    asset_by_id = {
        "lamp_pool_01": {
            "asset_id": "lamp_pool_01",
            "category": "lamp",
            "scene_eligible": True,
        }
    }

    usable = _validate_curated_locked_assets(
        asset_by_id=asset_by_id,
        profile="fixed_hq_v1",
    )

    assert "lamp" not in usable
    assert (
        _curated_locked_row_for_category(
            category="lamp",
            asset_by_id=asset_by_id,
            config=config,
        )
        is None
    )
