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
    _center_planting_tree_slot_plans,
    _curated_locked_row_for_category,
    _placement_surface_y_m,
    _point_side_matches_slot,
    _sample_pose_osm_for_segment,
    _strip_zone_candidate_keys,
    _validate_curated_locked_assets,
)
from roadgen3d.street_program import infer_street_program
from roadgen3d.types import StreetBand, StreetComposeConfig, StreetPlacement


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


def test_center_grass_belt_is_tree_placeable_target_strip():
    shapely = pytest.importorskip("shapely")
    from shapely.geometry import box

    carriageway = box(-8.0, -3.0, 8.0, 3.0)
    center_grass_belt = box(-8.0, -0.5, 8.0, 0.5)
    placement_ctx = SimpleNamespace(
        carriageway=carriageway,
        sidewalk_zone=box(-8.0, 3.0, 8.0, 8.0).union(box(-8.0, -8.0, 8.0, -3.0)),
        left_sidewalk_zone=box(-8.0, 3.0, 8.0, 8.0),
        right_sidewalk_zone=box(-8.0, -8.0, 8.0, -3.0),
        carriageway_width_m=6.0,
        strip_zones={"center_grass_belt": center_grass_belt},
        segment_strip_zones={"seg_0001": {"center_grass_belt": center_grass_belt}},
    )
    segment_node = SimpleNamespace(
        segment_id="seg_0001",
        start_xy=(-8.0, 0.0),
        end_xy=(8.0, 0.0),
        center_xy=(0.0, 0.0),
    )

    assert "center_grass_belt" in _strip_zone_candidate_keys("center", "center_grass_belt")
    assert _point_side_matches_slot(
        (0.0, 0.0),
        slot_side="center",
        placement_ctx=placement_ctx,
        segment_node=segment_node,
        band_name="center_grass_belt",
    ) == (True, True)
    assert _point_side_matches_slot(
        (0.0, 3.5),
        slot_side="center",
        placement_ctx=placement_ctx,
        segment_node=segment_node,
        band_name="center_grass_belt",
    ) == (False, False)

    pose = _sample_pose_osm_for_segment(
        "tree",
        placement_ctx,
        random.Random(11),
        segment_node=segment_node,
        slot_side="center",
        slot_band_name="center_grass_belt",
        band_width_m=1.0,
    )

    assert pose is not None
    point = shapely.Point(float(pose[0]), float(pose[1]))
    assert center_grass_belt.buffer(1e-6).contains(point)


def test_center_grass_belt_injects_tree_slots_from_strip_zones():
    pytest.importorskip("shapely")
    from shapely.geometry import box

    nodes = [
        SimpleNamespace(
            road_id=1,
            segment_id=f"seg_{idx:04d}",
            station_center_m=float(idx * 16.0),
            center_xy=(float(idx * 16.0), 0.0),
        )
        for idx in range(5)
    ]
    placement_ctx = SimpleNamespace(
        strip_zones={"center_grass_belt": box(-10.0, -0.5, 80.0, 0.5)},
        segment_strip_zones={
            node.segment_id: {"center_grass_belt": box(float(idx * 16.0) - 4.0, -0.5, float(idx * 16.0) + 4.0, 0.5)}
            for idx, node in enumerate(nodes)
        },
    )
    graph = SimpleNamespace(nodes=tuple(nodes))
    theme = SimpleNamespace(theme_id="theme_green", segment_ids=tuple(node.segment_id for node in nodes))

    slots, segment_lookup = _center_planting_tree_slot_plans(
        road_segment_graph=graph,
        theme_segments=(theme,),
        placement_ctx=placement_ctx,
        spacing_m=28.0,
    )

    assert slots
    assert all(slot.category == "tree" for slot in slots)
    assert all(slot.side == "center" for slot in slots)
    assert all(slot.band_name == "center_grass_belt" for slot in slots)
    assert all(slot.slot_id in segment_lookup for slot in slots)
    assert len(slots) == 3


def test_center_grass_belt_tree_uses_planting_soil_height():
    from roadgen3d import street_layout

    placement = StreetPlacement(
        instance_id="tree_center_001",
        asset_id="tree_lowpoly_001",
        category="tree",
        score=1.0,
        position_xyz=[0.0, 0.0, 0.0],
        yaw_deg=0.0,
        scale=1.0,
        bbox_xz=[-0.5, 0.5, -0.5, 0.5],
        selection_source="test",
        anchor_geom_id="center_grass_belt",
    )

    assert _placement_surface_y_m(placement) == pytest.approx(
        street_layout.CENTER_PLANTING_SOIL_TOP_Y_M
    )


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


def test_default_band_order_includes_clear_path_bands_for_bench():
    """Fix 1: _default_band_order should return clear_sidewalk bands for bench/mailbox."""
    from roadgen3d.layout_solver import _default_band_order

    bands = (
        StreetBand(
            name="left_nearroad_furnishing",
            kind="furnishing",
            side="left",
            width_m=1.5,
            z_center_m=4.0,
            allowed_categories=("lamp", "trash", "hydrant", "bollard", "bus_stop", "tree"),
        ),
        StreetBand(
            name="left_clear_sidewalk",
            kind="clear_path",
            side="left",
            width_m=2.5,
            z_center_m=6.0,
            allowed_categories=("mailbox", "bench"),
        ),
        StreetBand(
            name="right_nearroad_furnishing",
            kind="furnishing",
            side="right",
            width_m=1.5,
            z_center_m=-4.0,
            allowed_categories=("lamp", "trash", "hydrant", "bollard", "bus_stop", "tree"),
        ),
        StreetBand(
            name="right_clear_sidewalk",
            kind="clear_path",
            side="right",
            width_m=2.5,
            z_center_m=-6.0,
            allowed_categories=("mailbox", "bench"),
        ),
    )

    lamp_bands = _default_band_order("lamp", bands)
    assert len(lamp_bands) >= 2
    assert all("nearroad_furnishing" in band.name for band in lamp_bands)

    bench_bands = _default_band_order("bench", bands)
    assert len(bench_bands) >= 2
    assert all("clear_sidewalk" in band.name for band in bench_bands)

    mailbox_bands = _default_band_order("mailbox", bands)
    assert len(mailbox_bands) >= 1
    assert all("clear_sidewalk" in band.name for band in mailbox_bands)


def test_default_band_order_prefers_center_grass_belt_for_trees():
    from roadgen3d.layout_solver import _balanced_band_sequence, _default_band_order
    from roadgen3d.street_band_semantics import coerce_band_rule_kinds

    bands = (
        StreetBand(
            name="center_grass_belt",
            kind="grass_belt",
            side="center",
            width_m=1.0,
            z_center_m=0.0,
            allowed_categories=("tree",),
        ),
        StreetBand(
            name="left_nearroad_furnishing",
            kind="furnishing",
            side="left",
            width_m=1.5,
            z_center_m=4.0,
            allowed_categories=("tree", "lamp"),
        ),
        StreetBand(
            name="right_nearroad_furnishing",
            kind="furnishing",
            side="right",
            width_m=1.5,
            z_center_m=-4.0,
            allowed_categories=("tree", "lamp"),
        ),
    )

    assert {"grass_belt", "furnishing"} <= set(coerce_band_rule_kinds("center_grass_belt", "grass_belt"))
    tree_bands = _default_band_order("tree", bands)
    assert tree_bands[0].name == "center_grass_belt"

    ordered = _balanced_band_sequence(
        category="tree",
        allowed_bands=tree_bands,
        remaining_count=6,
        bilateral_side_counts={},
    )
    assert ordered[0].name == "center_grass_belt"
    assert sum(1 for band in ordered if band.name == "center_grass_belt") >= 2


def test_globalize_theme_slot_plans_preserves_lateral_offset():
    """Fix 4: Non-anchor slots should keep lateral offset when globalized."""
    from roadgen3d.street_layout import _globalize_theme_slot_plans
    from roadgen3d.types import LayoutSlotPlan, ThemeSegment, RoadSegmentGraph, RoadSegmentNode

    node = RoadSegmentNode(
        segment_id="seg_001",
        road_id=1,
        start_xy=(0.0, 0.0),
        end_xy=(12.0, 0.0),
        center_xy=(6.0, 0.0),
        length_m=12.0,
    )
    graph = RoadSegmentGraph(nodes=(node,), edges=())
    theme_segment = ThemeSegment(
        theme_id="theme_000",
        theme_name="commercial",
        x_start_m=0.0,
        x_end_m=12.0,
        center_x_m=6.0,
        length_m=12.0,
        design_rule_profile="balanced_complete_street_v1",
        style_preset="civic_clean_v1",
        segment_ids=("seg_001",),
    )
    slot = LayoutSlotPlan(
        slot_id="lamp_000",
        category="lamp",
        band_name="left_nearroad_furnishing",
        x_center_m=0.0,
        z_center_m=5.0,
        spacing_m=12.0,
        side="left",
        priority=1.0,
        required=False,
    )

    result_slots, segment_lookup = _globalize_theme_slot_plans(
        [slot],
        theme_segment=theme_segment,
        road_segment_graph=graph,
    )

    assert len(result_slots) == 1
    placed = result_slots[0]
    assert abs(placed.x_center_m - 6.0) < 1.0
    assert abs(placed.z_center_m - 5.0) < 0.5
