from __future__ import annotations

import sys
from pathlib import Path

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
                "road_width_m": 11.0,
                "reference_width_px": 104.0,
                "forward_drive_lane_count": 2,
                "reverse_drive_lane_count": 1,
                "bike_lane_count": 1,
                "bus_lane_count": 0,
                "parking_lane_count": 1,
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
    }


def test_parse_reference_annotation_normalizes_payload():
    annotation = parse_reference_annotation(_sample_annotation_payload())

    assert annotation.plan_id == "hkust_gz_gate"
    assert annotation.image_width_px == 1200
    assert annotation.centerlines[0].feature_id == "main_axis"
    assert annotation.centerlines[0].reference_width_px == 104.0
    assert annotation.centerlines[0].forward_drive_lane_count == 2
    assert annotation.centerlines[0].bike_lane_count == 1
    assert annotation.junctions[0].kind == "intersection"
    assert annotation.roundabouts[0].radius_px == 52.0


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
    assert main_axis_node.road_width_m == 11.0
    assert main_axis_node.lane_profile["forward_drive_lane_count"] == 2
    assert north_branch_node.road_width_m == 9.0
    assert north_branch_node.lane_profile["bus_lane_count"] == 1


def test_build_reference_annotation_graph_payload_returns_summary_and_graph():
    payload = build_reference_annotation_graph_payload(
        _sample_annotation_payload(),
        config=build_reference_annotation_compose_config({"segment_length_m": 9.0}),
    )

    assert payload["annotation"]["plan_id"] == "hkust_gz_gate"
    assert payload["graph"]["mode"] == "annotation"
    assert len(payload["road_profiles"]) == 2
    assert payload["road_profiles"][0]["annotation_id"] == "main_axis"
    assert payload["road_profiles"][0]["reference_width_px"] == 104.0
    assert payload["road_profiles"][1]["bus_lane_count"] == 1
    assert payload["summary"]["centerline_count"] == 2
    assert payload["summary"]["annotation_road_count"] == 2
    assert payload["summary"]["road_profile_count"] == 2
    assert payload["summary"]["roundabout_count"] == 1
    assert payload["summary"]["segment_count"] > 0
    assert payload["summary"]["junction_segment_count"] > 0
    assert payload["summary"]["min_road_width_m"] == 9.0
    assert payload["summary"]["max_road_width_m"] == 11.0
