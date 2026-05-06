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

from roadgen3d.reference_annotation import (  # noqa: E402
    ANNOTATION_SCHEMA_VERSION,
    build_reference_annotation_compose_config,
    build_reference_annotation_graph_payload,
    build_segment_graph_from_annotation,
    parse_reference_annotation,
)


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
                    {"strip_id": "median_01", "zone": "center", "kind": "median", "width_m": 1.2, "direction": "none", "order_index": 2},
                    {"strip_id": "fwd_drive", "zone": "center", "kind": "drive_lane", "width_m": 3.2, "direction": "forward", "order_index": 3},
                    {"strip_id": "fwd_bus", "zone": "center", "kind": "bus_lane", "width_m": 3.4, "direction": "forward", "order_index": 4},
                    {"strip_id": "right_furnishing", "zone": "right", "kind": "nearroad_furnishing", "width_m": 1.5, "direction": "none", "order_index": 0},
                    {"strip_id": "right_sidewalk", "zone": "right", "kind": "clear_sidewalk", "width_m": 2.5, "direction": "none", "order_index": 1},
                    {"strip_id": "right_frontage", "zone": "right", "kind": "frontage_reserve", "width_m": 2.0, "direction": "none", "order_index": 2},
                ],
                "street_furniture_instances": [
                    {"instance_id": "bench_01", "centerline_id": "main_axis", "strip_id": "left_furnishing", "kind": "bench", "station_m": 7.5, "lateral_offset_m": -8.1},
                    {"instance_id": "lamp_01", "centerline_id": "main_axis", "strip_id": "right_frontage", "kind": "lamp", "station_m": 22.0, "lateral_offset_m": 10.1, "yaw_deg": 90.0},
                ],
                "points": [
                    {"x": 120, "y": 400},
                    {"x": 520, "y": 400},
                    {"x": 980, "y": 360},
                ],
            },
            {
                "id": "north_branch",
                "label": "North Branch",
                "road_width_m": 9.0,
                "reference_width_px": 86.0,
                "forward_drive_lane_count": 1,
                "reverse_drive_lane_count": 1,
                "bike_lane_count": 0,
                "bus_lane_count": 1,
                "parking_lane_count": 0,
                "points": [
                    {"x": 520, "y": 400},
                    {"x": 520, "y": 140},
                ],
            },
        ],
        "junctions": [
            {"id": "junction_01", "kind": "intersection", "x": 520, "y": 400},
        ],
        "roundabouts": [
            {"id": "roundabout_01", "x": 980, "y": 360, "radius_px": 52},
        ],
        "control_points": [
            {"id": "gate_01", "kind": "gateway", "x": 150, "y": 400},
            {"id": "entry_01", "kind": "entry", "x": 520, "y": 140},
        ],
        "building_regions": [
            {
                "id": "building_region_01",
                "label": "North Court",
                "center_px": {"x": 320, "y": 250},
                "width_px": 180,
                "height_px": 120,
                "yaw_deg": 30,
            },
            {
                "id": "building_region_02",
                "label": "South Court",
                "center_px": {"x": 760, "y": 560},
                "width_px": 220,
                "height_px": 140,
                "yaw_deg": -15,
            },
        ],
    }


def _explicit_junction_annotation_payload():
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
                "start_junction_id": "",
                "end_junction_id": "junction_01",
                "points": [
                    {"x": 120, "y": 400},
                    {"x": 520, "y": 400},
                ],
            },
            {
                "id": "east_arm",
                "label": "East Arm",
                "road_width_m": 25.2,
                "forward_drive_lane_count": 2,
                "reverse_drive_lane_count": 1,
                "start_junction_id": "junction_01",
                "end_junction_id": "",
                "points": [
                    {"x": 520, "y": 400},
                    {"x": 980, "y": 400},
                ],
            },
            {
                "id": "north_arm",
                "label": "North Arm",
                "road_width_m": 12.0,
                "forward_drive_lane_count": 1,
                "reverse_drive_lane_count": 1,
                "start_junction_id": "junction_01",
                "end_junction_id": "",
                "points": [
                    {"x": 520, "y": 400},
                    {"x": 520, "y": 140},
                ],
            },
        ],
        "junctions": [
            {
                "id": "junction_01",
                "label": "Junction 01",
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


def test_parse_reference_annotation_normalizes_payload():
    annotation = parse_reference_annotation(_sample_annotation_payload())

    assert annotation.plan_id == "hkust_gz_gate"
    assert annotation.image_width_px == 1200
    assert annotation.centerlines[0].feature_id == "main_axis"
    assert annotation.centerlines[0].reference_width_px == 218.0
    assert annotation.centerlines[0].resolved_cross_section_mode() == "detailed"
    assert len(annotation.centerlines[0].cross_section_strips) == 11
    assert len(annotation.centerlines[0].street_furniture_instances) == 2
    assert annotation.centerlines[0].lane_profile()["forward_drive_lane_count"] == 1
    assert annotation.centerlines[0].lane_profile()["bus_lane_count"] == 1
    assert annotation.centerlines[0].carriageway_width_m() == pytest.approx(13.2)
    assert annotation.centerlines[0].cross_section_width_m() == pytest.approx(25.2)
    assert annotation.junctions[0].kind == "intersection"
    assert annotation.roundabouts[0].radius_px == 52.0
    assert len(annotation.building_regions) == 2
    assert annotation.building_regions[0].feature_id == "building_region_01"
    assert annotation.building_regions[0].yaw_deg == pytest.approx(30.0)
    assert annotation.to_dict()["building_regions"][1]["label"] == "South Court"


def test_parse_reference_annotation_normalizes_surface_annotations():
    payload = _sample_annotation_payload()
    payload["surface_annotations"] = [
        {
            "id": "surface_bus_01",
            "label": "Temporary Bus Lane",
            "kind": "bus_lane_widening",
            "surface_role": "bus_lane",
            "centerline_id": "main_axis",
            "station_start_m": 5.0,
            "station_end_m": 20.0,
            "lateral_start_m": 3.2,
            "lateral_end_m": 6.7,
            "material": {"preset": "bus_lane_green", "color_hex": "#40945c"},
        }
    ]

    annotation = parse_reference_annotation(payload)

    assert len(annotation.surface_annotations) == 1
    surface = annotation.surface_annotations[0]
    assert surface.feature_id == "surface_bus_01"
    assert surface.kind == "bus_lane_widening"
    assert surface.surface_role == "bus_lane"
    assert surface.centerline_id == "main_axis"
    assert surface.material.preset == "bus_lane_green"
    assert annotation.to_dict()["surface_annotations"][0]["material"]["color_hex"] == "#40945c"


def test_parse_reference_annotation_rejects_surface_annotation_with_missing_centerline():
    payload = _sample_annotation_payload()
    payload["surface_annotations"] = [
        {
            "id": "surface_missing",
            "kind": "safety_island",
            "centerline_id": "missing_axis",
            "station_start_m": 1.0,
            "station_end_m": 4.0,
            "lateral_start_m": -0.5,
            "lateral_end_m": 0.5,
        }
    ]

    with pytest.raises(ValueError, match="missing centerline"):
        parse_reference_annotation(payload)


def test_parse_reference_annotation_rejects_surface_annotation_outside_centerline_station_range():
    payload = _sample_annotation_payload()
    payload["surface_annotations"] = [
        {
            "id": "surface_too_long",
            "kind": "colored_pavement",
            "centerline_id": "north_branch",
            "station_start_m": 1.0,
            "station_end_m": 999.0,
            "lateral_start_m": 0.0,
            "lateral_end_m": 2.0,
        }
    ]

    with pytest.raises(ValueError, match="exceeds centerline"):
        parse_reference_annotation(payload)


def test_build_segment_graph_from_annotation_builds_junctions_and_roundabout():
    annotation = parse_reference_annotation(_sample_annotation_payload())
    config = build_reference_annotation_compose_config({"segment_length_m": 10.0, "road_width_m": 11.0})
    graph = build_segment_graph_from_annotation(annotation, config=config)

    assert graph.mode == "annotation"
    assert len(graph.nodes) >= 8
    assert len(graph.edges) >= 8
    assert any(node.is_junction for node in graph.nodes)
    assert any("roundabout" in node.poi_types for node in graph.nodes)
    assert any("gateway" in node.poi_types for node in graph.nodes)
    main_axis_node = next(node for node in graph.nodes if node.road_id == 1)
    north_branch_node = next(node for node in graph.nodes if node.road_id == 2)
    assert main_axis_node.road_width_m == pytest.approx(13.2)
    assert main_axis_node.cross_section_width_m == pytest.approx(25.2)
    assert len(main_axis_node.cross_section_strips) == 11
    assert any(node.street_furniture_instances for node in graph.nodes if node.road_id == 1)
    assert any(hint.strip_kind == "frontage_reserve" for hint in main_axis_node.metaurban_asset_hints)
    assert any("Building" in hint.suggested_assets for hint in main_axis_node.metaurban_asset_hints)
    assert main_axis_node.lane_profile["forward_drive_lane_count"] == 1
    assert main_axis_node.lane_profile["bus_lane_count"] == 1
    assert north_branch_node.road_width_m == 9.0
    assert north_branch_node.lane_profile["bus_lane_count"] == 1
    assert any(hint.strip_kind == "clear_sidewalk" for hint in north_branch_node.metaurban_asset_hints)


def test_build_segment_graph_from_annotation_detects_shared_vertex_junction_without_explicit_marker():
    payload = _sample_annotation_payload()
    payload["junctions"] = []

    graph = build_segment_graph_from_annotation(
        parse_reference_annotation(payload),
        config=build_reference_annotation_compose_config({"segment_length_m": 10.0, "road_width_m": 11.0}),
    )

    assert graph.mode == "annotation"
    assert len(graph.edges) >= 8
    assert any(node.is_junction for node in graph.nodes)
    assert any("junction" in node.poi_types for node in graph.nodes)


def test_build_reference_annotation_graph_payload_returns_summary_and_graph():
    sample_payload = _sample_annotation_payload()
    sample_payload["surface_annotations"] = [
        {
            "id": "surface_paving_01",
            "kind": "colored_pavement",
            "centerline_id": "main_axis",
            "station_start_m": 2.0,
            "station_end_m": 14.0,
            "lateral_start_m": -6.0,
            "lateral_end_m": -3.0,
            "material": {"preset": "colored_pavement"},
        }
    ]
    payload = build_reference_annotation_graph_payload(
        sample_payload,
        config=build_reference_annotation_compose_config({"segment_length_m": 9.0}),
    )

    assert payload["annotation"]["plan_id"] == "hkust_gz_gate"
    assert payload["graph"]["mode"] == "annotation"
    assert payload["summary"]["surface_annotation_count"] == 1
    assert payload["surface_annotations"][0]["surface_role"] == "colored_pavement"
    assert len(payload["road_profiles"]) == 2
    assert len(payload["cross_section_profiles"]) == 2
    assert len(payload["street_furniture_instances"]) == 2
    assert len(payload["metaurban_asset_hints"]) >= 2
    assert payload["metaurban_asset_guide"]["download_command"].endswith("pull_asset.py --update")
    assert payload["road_profiles"][0]["annotation_id"] == "main_axis"
    assert payload["road_profiles"][0]["reference_width_px"] == 218.0
    assert payload["road_profiles"][0]["carriageway_width_m"] == pytest.approx(13.2)
    assert payload["cross_section_profiles"][0]["strip_count"] == 11
    assert any(
        item["annotation_id"] == "main_axis" and item["strip_id"] == "left_furnishing" and "Lamp_post" in item["suggested_assets"]
        for item in payload["metaurban_asset_hints"]
    )
    assert any(
        item["annotation_id"] == "north_branch" and item["source_mode"] == "seed" and item["strip_kind"] == "clear_sidewalk"
        for item in payload["metaurban_asset_hints"]
    )
    assert payload["road_profiles"][1]["bus_lane_count"] == 1
    assert payload["summary"]["centerline_count"] == 2
    assert payload["summary"]["building_region_count"] == 2
    assert payload["summary"]["annotation_road_count"] == 2
    assert payload["summary"]["road_profile_count"] == 2
    assert payload["summary"]["cross_section_profile_count"] == 2
    assert len(payload["annotation"]["building_regions"]) == 2
    assert payload["annotation"]["building_regions"][0]["yaw_deg"] == pytest.approx(30.0)
    assert payload["summary"]["street_furniture_instance_count"] == 2
    assert payload["summary"]["metaurban_asset_hint_count"] == len(payload["metaurban_asset_hints"])
    assert payload["summary"]["detailed_centerline_count"] == 1
    assert payload["summary"]["junction_count"] == 1
    assert payload["summary"]["derived_junction_count"] == 1
    assert payload["summary"]["topology_junction_count"] == 1
    assert payload["summary"]["t_junction_count"] == 1
    assert payload["summary"]["cross_junction_count"] == 0
    assert payload["summary"]["cross_section_strip_count"] == 11
    assert payload["summary"]["roundabout_count"] == 1
    assert payload["summary"]["segment_count"] > 0
    assert payload["summary"]["junction_segment_count"] > 0
    assert payload["summary"]["min_road_width_m"] == 9.0
    assert payload["summary"]["max_road_width_m"] == pytest.approx(13.2)
    assert payload["summary"]["max_cross_section_width_m"] == pytest.approx(25.2)
    assert payload["derived_junctions"][0]["kind"] == "t_junction"
    assert payload["derived_junctions"][0]["arm_count"] == 3


def test_parse_reference_annotation_accepts_legacy_coarse_payload():
    payload = {
        "version": ANNOTATION_SCHEMA_VERSION,
        "plan_id": "legacy_demo",
        "image_width_px": 640,
        "image_height_px": 480,
        "pixels_per_meter": 8.0,
        "centerlines": [
            {
                "id": "legacy_axis",
                "road_width_m": 10.0,
                "forward_drive_lane_count": 1,
                "reverse_drive_lane_count": 1,
                "points": [
                    {"x": 60, "y": 240},
                    {"x": 580, "y": 240},
                ],
            }
        ],
    }

    annotation = parse_reference_annotation(payload)

    assert annotation.centerlines[0].resolved_cross_section_mode() == "coarse"
    assert annotation.centerlines[0].cross_section_strips == ()
    assert annotation.centerlines[0].street_furniture_instances == ()


def test_parse_reference_annotation_rejects_furniture_on_non_compatible_strip():
    payload = _sample_annotation_payload()
    payload["centerlines"][0]["street_furniture_instances"][0]["strip_id"] = "left_sidewalk"

    with pytest.raises(ValueError, match="furniture-compatible"):
        parse_reference_annotation(payload)


def test_build_reference_annotation_graph_payload_detects_cross_junction_topology():
    payload = _sample_annotation_payload()
    payload["junctions"] = []
    payload["centerlines"].append(
        {
            "id": "south_branch",
            "label": "South Branch",
            "road_width_m": 9.0,
            "forward_drive_lane_count": 1,
            "reverse_drive_lane_count": 1,
            "points": [
                {"x": 520, "y": 400},
                {"x": 520, "y": 660},
            ],
        }
    )

    graph_payload = build_reference_annotation_graph_payload(
        payload,
        config=build_reference_annotation_compose_config({"segment_length_m": 9.0}),
    )

    assert graph_payload["summary"]["derived_junction_count"] == 1
    assert graph_payload["summary"]["t_junction_count"] == 0
    assert graph_payload["summary"]["cross_junction_count"] == 1
    assert graph_payload["derived_junctions"][0]["kind"] == "cross_junction"
    assert graph_payload["derived_junctions"][0]["arm_count"] == 4


def test_explicit_junction_payload_builds_graph_junction_metadata():
    payload = _explicit_junction_annotation_payload()

    annotation = parse_reference_annotation(payload)
    graph = build_segment_graph_from_annotation(
        annotation,
        config=build_reference_annotation_compose_config({"segment_length_m": 9.0}),
    )

    assert len(graph.junctions) == 1
    assert graph.junctions[0].junction_id == "junction_01"
    assert graph.junctions[0].kind == "t_junction"
    assert graph.junctions[0].source_mode == "explicit"
    assert tuple(graph.junctions[0].connected_centerline_ids) == ("west_arm", "east_arm", "north_arm")
    assert graph.summary()["graph_junction_count"] == 1
    assert graph.summary()["graph_t_junction_count"] == 1
    assert any(node.end_junction_id == "junction_01" for node in graph.nodes)
    assert any(node.start_junction_id == "junction_01" for node in graph.nodes)


def test_parse_reference_annotation_rejects_explicit_junction_without_matching_endpoint_metadata():
    payload = _explicit_junction_annotation_payload()
    payload["centerlines"][0]["end_junction_id"] = ""

    with pytest.raises(ValueError, match="missing matching end_junction_id"):
        parse_reference_annotation(payload)


def test_parse_reference_annotation_rejects_centerline_passing_through_explicit_junction():
    payload = {
        "version": ANNOTATION_SCHEMA_VERSION,
        "plan_id": "explicit_cross_invalid",
        "image_width_px": 1200,
        "image_height_px": 800,
        "pixels_per_meter": 10.0,
        "centerlines": [
            {
                "id": "main_axis",
                "label": "Main Axis",
                "road_width_m": 12.0,
                "forward_drive_lane_count": 1,
                "reverse_drive_lane_count": 1,
                "points": [
                    {"x": 120, "y": 400},
                    {"x": 520, "y": 400},
                    {"x": 980, "y": 400},
                ],
            },
            {
                "id": "north_arm",
                "label": "North Arm",
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
                "label": "Junction 01",
                "kind": "t_junction",
                "anchor": {"x": 520, "y": 400},
                "connected_centerline_ids": ["main_axis", "north_arm"],
                "crosswalk_depth_m": 3.0,
                "source_mode": "explicit",
            }
        ],
        "roundabouts": [],
        "control_points": [],
    }

    with pytest.raises(ValueError, match="does not terminate at that junction anchor"):
        parse_reference_annotation(payload)
