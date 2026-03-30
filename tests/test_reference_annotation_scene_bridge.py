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


def _max_polygon_ring_size(geometry) -> int:
    geom_type = getattr(geometry, "geom_type", "")
    if geom_type == "Polygon":
        return len(list(geometry.exterior.coords))
    if geom_type == "MultiPolygon":
        return max((len(list(poly.exterior.coords)) for poly in geometry.geoms), default=0)
    if geom_type == "GeometryCollection":
        return max((_max_polygon_ring_size(item) for item in geometry.geoms), default=0)
    return 0


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


def _detailed_cross_strips():
    return [
        {"strip_id": "left_furnishing", "zone": "left", "kind": "nearroad_furnishing", "width_m": 1.5, "direction": "none", "order_index": 0},
        {"strip_id": "left_sidewalk", "zone": "left", "kind": "clear_sidewalk", "width_m": 2.5, "direction": "none", "order_index": 1},
        {"strip_id": "left_frontage", "zone": "left", "kind": "frontage_reserve", "width_m": 2.0, "direction": "none", "order_index": 2},
        {"strip_id": "rev_drive", "zone": "center", "kind": "drive_lane", "width_m": 3.3, "direction": "reverse", "order_index": 0},
        {"strip_id": "median_01", "zone": "center", "kind": "median", "width_m": 0.3, "direction": "none", "order_index": 1},
        {"strip_id": "fwd_drive_01", "zone": "center", "kind": "drive_lane", "width_m": 3.3, "direction": "forward", "order_index": 2},
        {"strip_id": "fwd_drive_02", "zone": "center", "kind": "drive_lane", "width_m": 3.3, "direction": "forward", "order_index": 3},
        {"strip_id": "right_furnishing", "zone": "right", "kind": "nearroad_furnishing", "width_m": 1.5, "direction": "none", "order_index": 0},
        {"strip_id": "right_sidewalk", "zone": "right", "kind": "clear_sidewalk", "width_m": 2.5, "direction": "none", "order_index": 1},
        {"strip_id": "right_frontage", "zone": "right", "kind": "frontage_reserve", "width_m": 2.0, "direction": "none", "order_index": 2},
    ]


def _derived_cross_payload():
    return {
        "version": ANNOTATION_SCHEMA_VERSION,
        "plan_id": "hkust_gz_gate",
        "image_path": "/tmp/hkust-gz.png",
        "image_width_px": 1200,
        "image_height_px": 800,
        "pixels_per_meter": 10.0,
        "centerlines": [
            {
                "id": "west_arm",
                "label": "West Arm",
                "road_width_m": 25.2,
                "cross_section_mode": "detailed",
                "cross_section_strips": _detailed_cross_strips(),
                "points": [{"x": 120, "y": 400}, {"x": 520, "y": 400}],
            },
            {
                "id": "east_arm",
                "label": "East Arm",
                "road_width_m": 25.2,
                "cross_section_mode": "detailed",
                "cross_section_strips": _detailed_cross_strips(),
                "points": [{"x": 520, "y": 400}, {"x": 980, "y": 400}],
            },
            {
                "id": "north_arm",
                "label": "North Arm",
                "road_width_m": 25.2,
                "cross_section_mode": "detailed",
                "cross_section_strips": _detailed_cross_strips(),
                "points": [{"x": 520, "y": 400}, {"x": 520, "y": 140}],
            },
            {
                "id": "south_arm",
                "label": "South Arm",
                "road_width_m": 25.2,
                "cross_section_mode": "detailed",
                "cross_section_strips": _detailed_cross_strips(),
                "points": [{"x": 520, "y": 680}, {"x": 520, "y": 400}],
            },
        ],
        "junctions": [],
        "roundabouts": [],
        "control_points": [],
    }


def _explicit_junction_payload():
    return {
        "version": ANNOTATION_SCHEMA_VERSION,
        "plan_id": "hkust_gz_gate",
        "image_path": "/tmp/hkust-gz.png",
        "image_width_px": 1200,
        "image_height_px": 800,
        "pixels_per_meter": 10.0,
        "centerlines": [
            {
                "id": "west_arm",
                "road_width_m": 25.2,
                "cross_section_mode": "detailed",
                "cross_section_strips": [
                    {"strip_id": "left_furnishing", "zone": "left", "kind": "nearroad_furnishing", "width_m": 1.5, "direction": "none", "order_index": 0},
                    {"strip_id": "left_sidewalk", "zone": "left", "kind": "clear_sidewalk", "width_m": 2.5, "direction": "none", "order_index": 1},
                    {"strip_id": "left_frontage", "zone": "left", "kind": "frontage_reserve", "width_m": 2.0, "direction": "none", "order_index": 2},
                    {"strip_id": "rev_drive", "zone": "center", "kind": "drive_lane", "width_m": 3.3, "direction": "reverse", "order_index": 0},
                    {"strip_id": "median_01", "zone": "center", "kind": "median", "width_m": 0.3, "direction": "none", "order_index": 1},
                    {"strip_id": "fwd_drive_01", "zone": "center", "kind": "drive_lane", "width_m": 3.3, "direction": "forward", "order_index": 2},
                    {"strip_id": "fwd_drive_02", "zone": "center", "kind": "drive_lane", "width_m": 3.3, "direction": "forward", "order_index": 3},
                    {"strip_id": "right_furnishing", "zone": "right", "kind": "nearroad_furnishing", "width_m": 1.5, "direction": "none", "order_index": 0},
                    {"strip_id": "right_sidewalk", "zone": "right", "kind": "clear_sidewalk", "width_m": 2.5, "direction": "none", "order_index": 1},
                    {"strip_id": "right_frontage", "zone": "right", "kind": "frontage_reserve", "width_m": 2.0, "direction": "none", "order_index": 2},
                ],
                "end_junction_id": "junction_01",
                "points": [
                    {"x": 120, "y": 400},
                    {"x": 520, "y": 400},
                ],
            },
            {
                "id": "east_arm",
                "road_width_m": 25.2,
                "forward_drive_lane_count": 2,
                "reverse_drive_lane_count": 1,
                "start_junction_id": "junction_01",
                "points": [
                    {"x": 520, "y": 400},
                    {"x": 980, "y": 400},
                ],
            },
            {
                "id": "north_arm",
                "road_width_m": 12.0,
                "forward_drive_lane_count": 1,
                "reverse_drive_lane_count": 1,
                "start_junction_id": "junction_01",
                "points": [
                    {"x": 520, "y": 400},
                    {"x": 520, "y": 140},
                ],
            },
        ],
        "junctions": [
            {
                "id": "junction_01",
                "kind": "t_junction",
                "anchor": {"x": 520, "y": 400},
                "connected_centerline_ids": ["west_arm", "east_arm", "north_arm"],
                "crosswalk_depth_m": 3.0,
                "source_mode": "explicit",
            }
        ],
        "roundabouts": [],
        "control_points": [],
    }


def _explicit_cross_junction_payload():
    return {
        "version": ANNOTATION_SCHEMA_VERSION,
        "plan_id": "hkust_gz_gate",
        "image_path": "/tmp/hkust-gz.png",
        "image_width_px": 1200,
        "image_height_px": 800,
        "pixels_per_meter": 10.0,
        "centerlines": [
            {
                "id": "west_arm",
                "label": "West Arm",
                "road_width_m": 25.2,
                "cross_section_mode": "detailed",
                "cross_section_strips": _detailed_cross_strips(),
                "end_junction_id": "junction_01",
                "points": [{"x": 120, "y": 400}, {"x": 520, "y": 400}],
            },
            {
                "id": "east_arm",
                "label": "East Arm",
                "road_width_m": 25.2,
                "cross_section_mode": "detailed",
                "cross_section_strips": _detailed_cross_strips(),
                "start_junction_id": "junction_01",
                "points": [{"x": 520, "y": 400}, {"x": 980, "y": 400}],
            },
            {
                "id": "north_arm",
                "label": "North Arm",
                "road_width_m": 25.2,
                "cross_section_mode": "detailed",
                "cross_section_strips": _detailed_cross_strips(),
                "start_junction_id": "junction_01",
                "points": [{"x": 520, "y": 400}, {"x": 520, "y": 140}],
            },
            {
                "id": "south_arm",
                "label": "South Arm",
                "road_width_m": 25.2,
                "cross_section_mode": "detailed",
                "cross_section_strips": _detailed_cross_strips(),
                "end_junction_id": "junction_01",
                "points": [{"x": 520, "y": 680}, {"x": 520, "y": 400}],
            },
        ],
        "junctions": [
            {
                "id": "junction_01",
                "label": "Junction 01",
                "kind": "cross_junction",
                "anchor": {"x": 520, "y": 400},
                "connected_centerline_ids": ["west_arm", "east_arm", "north_arm", "south_arm"],
                "crosswalk_depth_m": 3.0,
                "source_mode": "explicit",
            }
        ],
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
    assert all(
        _max_polygon_ring_size(patch["geometry"]) <= 8
        for patch in bridge.placement_context.junction_geometries[0]["frontage_corner_patches"]
        if not patch["geometry"].is_empty
    )
    anchor = bridge.placement_context.junction_geometries[0]["anchor_xy"]
    assert bridge.placement_context.junction_geometries[0]["junction_core_rect"].contains(Point(anchor[0], anchor[1]))
    assert not bridge.placement_context.carriageway.contains(Point(anchor[0], anchor[1]))


def test_reference_annotation_scene_bridge_builds_cross_corner_polylines_for_derived_cross():
    pytest.importorskip("shapely")

    bridge = build_reference_annotation_scene_bridge(
        _derived_cross_payload(),
        compose_config=build_reference_annotation_compose_config({"segment_length_m": 9.0, "road_width_m": 13.2}),
    )

    assert bridge.projected_features.roads
    assert bridge.summary_metadata["junction_geometry_count"] == 1
    junction_geometry = bridge.placement_context.junction_geometries[0]
    assert junction_geometry["kind"] == "cross_junction"
    assert len(junction_geometry["approach_boundaries"]) == 4
    assert len(junction_geometry["crosswalk_patches"]) == 4
    assert "sidewalk_corner_patches" not in junction_geometry
    assert "nearroad_corner_patches" not in junction_geometry
    assert "frontage_corner_patches" not in junction_geometry
    assert len(junction_geometry["sidewalk_corner_polylines"]) == 4
    assert len(junction_geometry["nearroad_corner_polylines"]) == 4
    assert len(junction_geometry["frontage_corner_polylines"]) == 4
    assert all(len(polyline["points_xy"]) == 3 for polyline in junction_geometry["sidewalk_corner_polylines"])
    assert all(len(polyline["points_xy"]) == 3 for polyline in junction_geometry["nearroad_corner_polylines"])
    assert all(len(polyline["points_xy"]) == 3 for polyline in junction_geometry["frontage_corner_polylines"])


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
    assert len(serialized["junction_geometries"][0]["frontage_corner_patches"]) >= 1
    assert all(
        max((len(ring) for ring in patch["rings"]), default=0) <= 8
        for patch in serialized["junction_geometries"][0]["frontage_corner_patches"]
    )
    assert serialized["junction_geometries"][0]["carriageway_core_rings"]

    scene = _build_osm_base_scene(bridge.placement_context)
    node_names = set(scene.graph.nodes_geometry)
    assert any(name.startswith("junction_crosswalk_") for name in node_names)
    assert any(name.startswith("junction_sidewalk_corner_") for name in node_names)
    assert any(name.startswith("junction_nearroad_corner_") for name in node_names)


def test_cross_junction_serialization_and_scene_include_corner_polylines():
    pytest.importorskip("shapely")
    pytest.importorskip("trimesh")

    bridge = build_reference_annotation_scene_bridge(
        _explicit_cross_junction_payload(),
        compose_config=build_reference_annotation_compose_config({"segment_length_m": 9.0, "road_width_m": 13.2}),
    )

    junction_geometry = bridge.placement_context.junction_geometries[0]
    assert junction_geometry["kind"] == "cross_junction"
    assert "sidewalk_corner_patches" not in junction_geometry
    assert len(junction_geometry["sidewalk_corner_polylines"]) == 4
    assert len(junction_geometry["nearroad_corner_polylines"]) == 4
    assert len(junction_geometry["frontage_corner_polylines"]) == 4
    assert all(len(polyline["points_xy"]) == 3 for polyline in junction_geometry["sidewalk_corner_polylines"])

    serialized = _serialize_osm_geometry(bridge.placement_context)
    assert len(serialized["junction_geometries"]) == 1
    serialized_junction = serialized["junction_geometries"][0]
    assert serialized_junction["kind"] == "cross_junction"
    assert "sidewalk_corner_patches" not in serialized_junction
    assert len(serialized_junction["sidewalk_corner_polylines"]) == 4
    assert len(serialized_junction["nearroad_corner_polylines"]) == 4
    assert len(serialized_junction["frontage_corner_polylines"]) == 4
    assert all(len(polyline["points_xy"]) == 3 for polyline in serialized_junction["frontage_corner_polylines"])

    scene = _build_osm_base_scene(bridge.placement_context)
    node_names = set(scene.graph.nodes_geometry)
    assert any(name.startswith("junction_sidewalk_corner_polyline_") for name in node_names)
    assert any(name.startswith("junction_nearroad_corner_polyline_") for name in node_names)
    assert any(name.startswith("junction_frontage_corner_polyline_") for name in node_names)


def test_explicit_junction_scene_bridge_serializes_split_lines_and_control_points():
    pytest.importorskip("shapely")

    bridge = build_reference_annotation_scene_bridge(
        _explicit_junction_payload(),
        compose_config=build_reference_annotation_compose_config({"segment_length_m": 9.0, "road_width_m": 13.2}),
    )

    junction_geometry = bridge.placement_context.junction_geometries[0]
    assert junction_geometry["kind"] == "t_junction"
    assert len(junction_geometry["approach_split_lines"]) == 3
    assert len(junction_geometry["skeleton_foot_points"]) == 3
    assert len(junction_geometry["sub_lane_control_points"]) > 0

    serialized = _serialize_osm_geometry(bridge.placement_context)
    assert len(serialized["junction_geometries"][0]["approach_split_lines"]) == 3
    assert len(serialized["junction_geometries"][0]["skeleton_foot_points"]) == 3
    assert len(serialized["junction_geometries"][0]["sub_lane_control_points"]) > 0
