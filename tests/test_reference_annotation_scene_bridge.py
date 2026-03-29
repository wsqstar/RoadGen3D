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

from roadgen3d.reference_annotation import ANNOTATION_SCHEMA_VERSION, build_reference_annotation_compose_config
from roadgen3d.reference_annotation_scene_bridge import build_reference_annotation_scene_bridge
from roadgen3d.street_layout import _build_osm_base_scene, _serialize_osm_geometry


def _sample_annotation_payload():
    return {
        "version": ANNOTATION_SCHEMA_VERSION,
        "plan_id": "hkust_gz_gate",
        "image_path": "/tmp/hkust-gz.png",
        "image_width_px": 1200,
        "image_height_px": 800,
        "pixels_per_meter": 10.0,
        "centerlines": [
            {
                "id": "main_axis",
                "label": "Main Axis",
                "road_width_m": 25.2,
                "reference_width_px": 218.0,
                "forward_drive_lane_count": 1,
                "reverse_drive_lane_count": 1,
                "bike_lane_count": 0,
                "bus_lane_count": 1,
                "parking_lane_count": 1,
                "cross_section_mode": "detailed",
                "cross_section_strips": [
                    {"strip_id": "left_furnishing", "zone": "left", "kind": "nearroad_furnishing", "width_m": 1.5, "direction": "none", "order_index": 0},
                    {"strip_id": "left_sidewalk", "zone": "left", "kind": "clear_sidewalk", "width_m": 2.5, "direction": "none", "order_index": 1},
                    {"strip_id": "left_frontage", "zone": "left", "kind": "frontage_reserve", "width_m": 2.0, "direction": "none", "order_index": 2},
                    {"strip_id": "rev_park", "zone": "center", "kind": "parking_lane", "width_m": 2.2, "direction": "reverse", "order_index": 0},
                    {"strip_id": "rev_drive", "zone": "center", "kind": "drive_lane", "width_m": 3.2, "direction": "reverse", "order_index": 1},
                    {"strip_id": "median_01", "zone": "center", "kind": "median", "width_m": 0.3, "direction": "none", "order_index": 2},
                    {"strip_id": "fwd_drive", "zone": "center", "kind": "drive_lane", "width_m": 3.2, "direction": "forward", "order_index": 3},
                    {"strip_id": "fwd_bus", "zone": "center", "kind": "bus_lane", "width_m": 3.4, "direction": "forward", "order_index": 4},
                    {"strip_id": "right_furnishing", "zone": "right", "kind": "nearroad_furnishing", "width_m": 1.5, "direction": "none", "order_index": 0},
                    {"strip_id": "right_sidewalk", "zone": "right", "kind": "clear_sidewalk", "width_m": 2.5, "direction": "none", "order_index": 1},
                    {"strip_id": "right_frontage", "zone": "right", "kind": "frontage_reserve", "width_m": 2.0, "direction": "none", "order_index": 2},
                ],
                "points": [
                    {"x": 120, "y": 400},
                    {"x": 520, "y": 400},
                    {"x": 980, "y": 400},
                ],
            },
            {
                "id": "north_branch",
                "label": "North Branch",
                "road_width_m": 9.0,
                "forward_drive_lane_count": 1,
                "reverse_drive_lane_count": 1,
                "points": [
                    {"x": 520, "y": 400},
                    {"x": 520, "y": 140},
                ],
            },
        ],
        "junctions": [],
        "roundabouts": [],
        "control_points": [],
    }


def test_reference_annotation_scene_bridge_builds_junction_geometry():
    pytest.importorskip("shapely")
    from shapely.geometry import Point

    bridge = build_reference_annotation_scene_bridge(
        _sample_annotation_payload(),
        compose_config=build_reference_annotation_compose_config({"segment_length_m": 9.0, "road_width_m": 13.2}),
    )

    assert bridge.projected_features.roads
    assert bridge.summary_metadata["junction_geometry_count"] == 1
    assert bridge.summary_metadata["t_junction_count"] == 1
    assert bridge.placement_context.junction_geometries[0]["kind"] == "t_junction"
    assert not bridge.placement_context.junction_geometries[0]["junction_core_rect"].is_empty
    assert len(bridge.placement_context.junction_geometries[0]["approach_boundaries"]) == 3
    assert len(bridge.placement_context.junction_geometries[0]["crosswalk_patches"]) == 3
    assert len(bridge.placement_context.junction_geometries[0]["sidewalk_corner_patches"]) >= 1
    assert len(bridge.placement_context.junction_geometries[0]["nearroad_corner_patches"]) >= 1
    assert len(bridge.placement_context.junction_geometries[0]["frontage_corner_patches"]) >= 1
    anchor = bridge.placement_context.junction_geometries[0]["anchor_xy"]
    assert bridge.placement_context.junction_geometries[0]["junction_core_rect"].contains(Point(anchor[0], anchor[1]))
    assert not bridge.placement_context.carriageway.contains(Point(anchor[0], anchor[1]))


def test_osm_geometry_serialization_and_scene_include_junction_patches():
    pytest.importorskip("shapely")
    pytest.importorskip("trimesh")

    bridge = build_reference_annotation_scene_bridge(
        _sample_annotation_payload(),
        compose_config=build_reference_annotation_compose_config({"segment_length_m": 9.0, "road_width_m": 13.2}),
    )

    serialized = _serialize_osm_geometry(bridge.placement_context)
    assert len(serialized["junction_geometries"]) == 1
    assert serialized["junction_geometries"][0]["kind"] == "t_junction"
    assert serialized["junction_geometries"][0]["junction_core_rect_rings"]
    assert len(serialized["junction_geometries"][0]["approach_boundaries"]) == 3
    assert len(serialized["junction_geometries"][0]["crosswalk_patches"]) == 3
    assert len(serialized["junction_geometries"][0]["nearroad_corner_patches"]) >= 1
    assert serialized["junction_geometries"][0]["carriageway_core_rings"]

    scene = _build_osm_base_scene(bridge.placement_context)
    node_names = set(scene.graph.nodes_geometry)
    assert any(name.startswith("junction_crosswalk_") for name in node_names)
    assert any(name.startswith("junction_sidewalk_corner_") for name in node_names)
    assert any(name.startswith("junction_nearroad_corner_") for name in node_names)
