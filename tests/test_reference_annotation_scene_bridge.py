from __future__ import annotations

import json
import sys
from collections import defaultdict
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


def _assert_cross_corner_ribbon_patches_are_valid(junction_geometry):
    expected = {
        "nearroad_corner_patches": ("nearroad_furnishing", "furnishing"),
        "sidewalk_corner_patches": ("clear_sidewalk", "sidewalk"),
        "frontage_corner_patches": ("frontage_reserve", "context_ground"),
    }
    grouped = defaultdict(list)
    for bucket_name, (strip_kind, surface_role) in expected.items():
        patches = junction_geometry.get(bucket_name, [])
        assert len(patches) == 4
        for patch in patches:
            assert patch["patch_id"]
            assert patch["quadrant_id"]
            assert patch["strip_kind"] == strip_kind
            assert patch.get("strip_id_a") or patch.get("from_strip_id")
            assert patch.get("strip_id_b") or patch.get("to_strip_id")
            if patch.get("generation_mode") == "roadpen_style_lane_connector":
                assert patch["from_centerline_id"]
                assert patch["to_centerline_id"]
            assert patch["surface_role"] == surface_role
            geometry = patch["geometry"]
            assert geometry.is_valid
            assert geometry.area > 1.0
            assert _max_polygon_ring_size(geometry) > 6
            grouped[patch["quadrant_id"]].append(patch)
    assert len(grouped) == 4
    assert all(len(items) == 3 for items in grouped.values())
    for patches in grouped.values():
        for index, patch_a in enumerate(patches):
            for patch_b in patches[index + 1:]:
                assert patch_a["geometry"].intersection(patch_b["geometry"]).area < 1e-4


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
        "building_regions": [
            {
                "id": "building_region_01",
                "label": "North Plaza",
                "center_px": {"x": 300, "y": 240},
                "width_px": 180,
                "height_px": 120,
                "yaw_deg": 20,
            },
            {
                "id": "building_region_02",
                "label": "South Plaza",
                "center_px": {"x": 760, "y": 560},
                "width_px": 220,
                "height_px": 160,
                "yaw_deg": -35,
            },
        ],
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
    assert len(bridge.placement_context.building_regions) == 2
    assert bridge.placement_context.building_regions[0]["region_id"] == "building_region_01"
    assert bridge.placement_context.building_regions[0]["yaw_deg"] == pytest.approx(20.0)
    assert len(bridge.placement_context.building_regions[0]["polygon_xz"]) == 5
    assert bridge.summary_metadata["building_region_count"] == 2


def test_reference_annotation_scene_bridge_builds_surface_annotation_patches():
    pytest.importorskip("shapely")

    payload = _sample_annotation_payload()
    payload["surface_annotations"] = [
        {
            "id": "surface_bus_01",
            "label": "Temporary Bus Lane",
            "kind": "bus_lane_widening",
            "surface_role": "bus_lane",
            "centerline_id": "main_axis",
            "station_start_m": 10.0,
            "station_end_m": 30.0,
            "lateral_start_m": 3.0,
            "lateral_end_m": 6.5,
            "material": {"preset": "bus_lane_green"},
        },
        {
            "id": "surface_island_01",
            "label": "Safety Island",
            "kind": "safety_island",
            "surface_role": "safety_island",
            "centerline_id": "main_axis",
            "station_start_m": 36.0,
            "station_end_m": 46.0,
            "lateral_start_m": -0.8,
            "lateral_end_m": 0.8,
            "material": {"preset": "safety_island_concrete"},
        },
    ]

    bridge = build_reference_annotation_scene_bridge(
        payload,
        compose_config=build_reference_annotation_compose_config({"segment_length_m": 9.0, "road_width_m": 13.2}),
    )

    patches = bridge.placement_context.surface_annotations
    assert bridge.summary_metadata["surface_annotation_count"] == 2
    assert {patch["surface_role"] for patch in patches} == {"bus_lane", "safety_island"}
    bus_patch = next(patch for patch in patches if patch["surface_id"] == "surface_bus_01")
    assert bus_patch["geometry"].is_valid
    assert bus_patch["geometry"].area == pytest.approx(70.0, rel=0.08)

    serialized = _serialize_osm_geometry(bridge.placement_context)
    assert len(serialized["surface_annotations"]) == 2
    assert serialized["surface_annotations"][0]["rings"]
    assert serialized["surface_annotations"][0]["material"]["preset"] == "bus_lane_green"


def test_reference_annotation_scene_bridge_builds_cross_corner_polylines_for_derived_cross():
    pytest.importorskip("shapely")
    from shapely.geometry import Point

    bridge = build_reference_annotation_scene_bridge(
        _derived_cross_payload(),
        compose_config=build_reference_annotation_compose_config({"segment_length_m": 9.0, "road_width_m": 13.2}),
    )

    assert bridge.projected_features.roads
    assert bridge.summary_metadata["junction_geometry_count"] == 1
    junction_geometry = bridge.placement_context.junction_geometries[0]
    assert junction_geometry["kind"] == "cross_junction"
    assert junction_geometry["carriageway_core"].area > junction_geometry["junction_core_rect"].area
    assert all(
        junction_geometry["carriageway_core"].buffer(1e-3).covers(Point(*boundary["center_xy"]))
        for boundary in junction_geometry["approach_boundaries"]
    )
    assert len(junction_geometry["approach_boundaries"]) == 4
    assert len(junction_geometry["crosswalk_patches"]) == 4
    assert len(junction_geometry.get("sidewalk_corner_patches", [])) == 4
    assert len(junction_geometry.get("nearroad_corner_patches", [])) == 4
    assert len(junction_geometry.get("frontage_corner_patches", [])) == 4
    assert len(junction_geometry["quadrant_corner_kernels"]) == 4
    assert all(kernel["kernel_id"] for kernel in junction_geometry["quadrant_corner_kernels"])
    assert all(kernel["quadrant_id"] for kernel in junction_geometry["quadrant_corner_kernels"])
    assert all(kernel["kernel_kind"] == "circular_arc" for kernel in junction_geometry["quadrant_corner_kernels"])
    assert all(kernel["radius_m"] > 1.0 for kernel in junction_geometry["quadrant_corner_kernels"])
    assert len(junction_geometry["sidewalk_corner_polylines"]) == 4
    assert len(junction_geometry["nearroad_corner_polylines"]) == 4
    assert len(junction_geometry["frontage_corner_polylines"]) == 4
    assert all(len(polyline["points_xy"]) > 3 for polyline in junction_geometry["sidewalk_corner_polylines"])
    assert all(len(polyline["points_xy"]) > 3 for polyline in junction_geometry["nearroad_corner_polylines"])
    assert all(len(polyline["points_xy"]) > 3 for polyline in junction_geometry["frontage_corner_polylines"])
    assert all(polyline["quadrant_id"] for polyline in junction_geometry["sidewalk_corner_polylines"])
    assert all(polyline["kernel_id"] for polyline in junction_geometry["sidewalk_corner_polylines"])
    assert {polyline["kernel_id"] for polyline in junction_geometry["sidewalk_corner_polylines"]} == {
        kernel["kernel_id"] for kernel in junction_geometry["quadrant_corner_kernels"]
    }
    assert len(junction_geometry.get("arm_skeletons", [])) == 4
    assert all(skeleton["split_center_xy"] for skeleton in junction_geometry["arm_skeletons"])
    assert junction_geometry.get("turn_lane_patches", []) == []
    _assert_cross_corner_ribbon_patches_are_valid(junction_geometry)


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
    assert serialized["junction_geometries"][0]["normalized_surface_patches"]
    assert {patch["surface_role"] for patch in serialized["junction_geometries"][0]["normalized_surface_patches"]} >= {
        "carriageway",
        "crossing",
    }

    scene = _build_osm_base_scene(bridge.placement_context)
    node_names = set(scene.graph.nodes_geometry)
    assert any(name.startswith("junction_normalized_surface_") for name in node_names)
    assert not any(name.startswith("junction_crosswalk_") for name in node_names)
    assert not any(name.startswith("junction_turn_lane_") for name in node_names)


def test_cross_junction_serialization_and_scene_include_corner_polylines():
    pytest.importorskip("shapely")
    pytest.importorskip("trimesh")
    from shapely.geometry import Polygon

    bridge = build_reference_annotation_scene_bridge(
        _explicit_cross_junction_payload(),
        compose_config=build_reference_annotation_compose_config({"segment_length_m": 9.0, "road_width_m": 13.2}),
    )

    junction_geometry = bridge.placement_context.junction_geometries[0]
    assert junction_geometry["kind"] == "cross_junction"
    assert junction_geometry["carriageway_core"].area > junction_geometry["junction_core_rect"].area
    assert len(junction_geometry.get("sidewalk_corner_patches", [])) == 4
    assert len(junction_geometry.get("nearroad_corner_patches", [])) == 4
    assert len(junction_geometry.get("frontage_corner_patches", [])) == 4
    assert len(junction_geometry["quadrant_corner_kernels"]) == 4
    assert len(junction_geometry["sidewalk_corner_polylines"]) == 4
    assert len(junction_geometry["nearroad_corner_polylines"]) == 4
    assert len(junction_geometry["frontage_corner_polylines"]) == 4
    assert all(kernel["kernel_kind"] == "circular_arc" for kernel in junction_geometry["quadrant_corner_kernels"])
    assert all(kernel["radius_m"] > 1.0 for kernel in junction_geometry["quadrant_corner_kernels"])
    assert all(len(polyline["points_xy"]) > 3 for polyline in junction_geometry["sidewalk_corner_polylines"])
    assert all(polyline["quadrant_id"] for polyline in junction_geometry["frontage_corner_polylines"])
    assert all(polyline["kernel_id"] for polyline in junction_geometry["frontage_corner_polylines"])
    assert len(junction_geometry.get("arm_skeletons", [])) == 4
    assert all(skeleton["corner_facing_sides"] for skeleton in junction_geometry["arm_skeletons"])
    assert junction_geometry.get("turn_lane_patches", []) == []
    _assert_cross_corner_ribbon_patches_are_valid(junction_geometry)

    serialized = _serialize_osm_geometry(bridge.placement_context)
    assert len(serialized["junction_geometries"]) == 1
    serialized_junction = serialized["junction_geometries"][0]
    assert serialized_junction["kind"] == "cross_junction"
    core_area = Polygon(serialized_junction["junction_core_rect_rings"][0]).area
    carriageway_core_area = Polygon(serialized_junction["carriageway_core_rings"][0]).area
    assert carriageway_core_area > core_area
    assert len(serialized_junction.get("sidewalk_corner_patches", [])) == 4
    assert len(serialized_junction.get("nearroad_corner_patches", [])) == 4
    assert len(serialized_junction.get("frontage_corner_patches", [])) == 4
    assert len(serialized_junction["quadrant_corner_kernels"]) == 4
    assert len(serialized_junction["sidewalk_corner_polylines"]) == 4
    assert len(serialized_junction["nearroad_corner_polylines"]) == 4
    assert len(serialized_junction["frontage_corner_polylines"]) == 4
    assert all(kernel["kernel_kind"] == "circular_arc" for kernel in serialized_junction["quadrant_corner_kernels"])
    assert all(len(polyline["points_xy"]) > 3 for polyline in serialized_junction["frontage_corner_polylines"])
    assert all(polyline["quadrant_id"] for polyline in serialized_junction["sidewalk_corner_polylines"])
    assert all(polyline["kernel_id"] for polyline in serialized_junction["sidewalk_corner_polylines"])
    assert serialized_junction["turn_lane_patches"] == []
    assert serialized_junction["normalized_surface_patches"]
    assert {patch["surface_role"] for patch in serialized_junction["normalized_surface_patches"]} >= {
        "carriageway",
        "crossing",
        "sidewalk",
        "furnishing",
        "context_ground",
    }
    for bucket_name, strip_kind, surface_role in (
        ("nearroad_corner_patches", "nearroad_furnishing", "furnishing"),
        ("sidewalk_corner_patches", "clear_sidewalk", "sidewalk"),
        ("frontage_corner_patches", "frontage_reserve", "context_ground"),
    ):
        assert all(patch["rings"] for patch in serialized_junction[bucket_name])
        assert {patch["strip_kind"] for patch in serialized_junction[bucket_name]} == {strip_kind}
        assert {patch["surface_role"] for patch in serialized_junction[bucket_name]} == {surface_role}
        assert {patch["quadrant_id"] for patch in serialized_junction[bucket_name]} == {
            kernel["quadrant_id"] for kernel in serialized_junction["quadrant_corner_kernels"]
        }
    assert len(serialized_junction["arm_skeletons"]) == 4
    assert all(skeleton["split_center_xy"] for skeleton in serialized_junction["arm_skeletons"])

    scene = _build_osm_base_scene(bridge.placement_context)
    node_names = set(scene.graph.nodes_geometry)
    assert any(name.startswith("junction_normalized_surface_") for name in node_names)
    assert not any(name.startswith("junction_turn_lane_") for name in node_names)
    assert not any(name.startswith("junction_sidewalk_corner_") for name in node_names)
    assert not any(name.startswith("junction_nearroad_corner_") for name in node_names)
    assert not any(name.startswith("junction_frontage_corner_") for name in node_names)
    assert not any(name.startswith("junction_sidewalk_corner_apron_") for name in node_names)


def test_hkust_gate_cross_junctions_use_canonical_roadpen_surfaces_without_triangular_slivers():
    pytest.importorskip("shapely")

    annotation_path = ROOT / "assets" / "graph_templates" / "hkust_gz_gate" / "annotation.json"
    payload = json.loads(annotation_path.read_text())
    bridge = build_reference_annotation_scene_bridge(
        payload,
        compose_config=build_reference_annotation_compose_config({"segment_length_m": 9.0, "road_width_m": 13.2}),
    )

    cross_junctions = [
        geometry
        for geometry in bridge.placement_context.junction_geometries
        if geometry.get("kind") == "cross_junction"
    ]
    assert cross_junctions
    for geometry in cross_junctions:
        assert geometry.get("generation_mode") == "cross_strip_fusion_auto"
        assert geometry.get("debug_info", {}).get("generation_mode") == "roadpen_style_junction_fusion_v1"
        canonical_patches = geometry.get("canonical_surface_patches", [])
        assert len(canonical_patches) >= 41
        assert geometry["surface_normalization_debug"]["input_counts"]["canonical_surface_patch"] >= 41
        assert sum(
            1 for patch in canonical_patches
            if patch.get("source_kind") == "roadpen_style_carriageway_apron"
        ) >= 4
        assert sum(
            1 for patch in canonical_patches
            if patch.get("source_kind") == "roadpen_style_endpoint_fill"
        ) >= 24
        carriageway_surfaces = [
            patch for patch in geometry["normalized_surface_patches"]
            if patch["surface_role"] == "carriageway"
        ]
        assert carriageway_surfaces
        assert any(
            any("carriageway_apron" in source_id for source_id in patch["source_ids"])
            for patch in carriageway_surfaces
        )
        planar = [
            patch for patch in geometry["normalized_surface_patches"]
            if not patch.get("is_overlay")
        ]
        assert planar
        assert all(set(patch["source_kinds"]) == {"canonical_surface_patch"} for patch in planar)
        for patch in planar:
            role = patch["surface_role"]
            if role not in {"sidewalk", "furnishing", "context_ground"}:
                continue
            geom = patch["geometry"]
            if geom.geom_type == "Polygon":
                components = [geom]
            else:
                components = list(getattr(geom, "geoms", []) or [])
            assert components
            for component in components:
                assert component.area > 5.0
                assert len(list(component.exterior.coords)) >= 12


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
    assert "quadrant_corner_kernels" not in serialized["junction_geometries"][0]
