from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.reference_annotation import parse_reference_annotation
from roadgen3d.reference_annotation_scene_bridge import build_reference_annotation_scene_bridge


def _square_surface(surface_id: str, lane_id: str, provenance: str = "generated") -> dict:
    return {
        "surfaceId": surface_id,
        "laneId": lane_id,
        "armKey": "north",
        "flow": "inbound",
        "laneIndex": 0,
        "laneWidthM": 3.5,
        "skeletonId": "skeleton_north",
        "provenance": provenance,
        "nodes": [
            {"nodeId": f"{surface_id}_n1", "kind": "start_left", "point": {"x": 0.0, "y": 0.0}},
            {"nodeId": f"{surface_id}_n2", "kind": "start_right", "point": {"x": 4.0, "y": 0.0}},
            {"nodeId": f"{surface_id}_n3", "kind": "end_right", "point": {"x": 4.0, "y": 3.0}},
            {"nodeId": f"{surface_id}_n4", "kind": "end_left", "point": {"x": 0.0, "y": 3.0}},
        ],
        "edges": [
            {
                "edgeId": f"{surface_id}_e1",
                "startNodeId": f"{surface_id}_n1",
                "endNodeId": f"{surface_id}_n2",
                "kind": "line",
                "curve": {
                    "start": {"x": 0.0, "y": 0.0},
                    "control1": {"x": 1.0, "y": 0.0},
                    "control2": {"x": 3.0, "y": 0.0},
                    "end": {"x": 4.0, "y": 0.0},
                },
            },
            {
                "edgeId": f"{surface_id}_e2",
                "startNodeId": f"{surface_id}_n2",
                "endNodeId": f"{surface_id}_n3",
                "kind": "line",
                "curve": {
                    "start": {"x": 4.0, "y": 0.0},
                    "control1": {"x": 4.0, "y": 1.0},
                    "control2": {"x": 4.0, "y": 2.0},
                    "end": {"x": 4.0, "y": 3.0},
                },
            },
            {
                "edgeId": f"{surface_id}_e3",
                "startNodeId": f"{surface_id}_n3",
                "endNodeId": f"{surface_id}_n4",
                "kind": "line",
                "curve": {
                    "start": {"x": 4.0, "y": 3.0},
                    "control1": {"x": 3.0, "y": 3.0},
                    "control2": {"x": 1.0, "y": 3.0},
                    "end": {"x": 0.0, "y": 3.0},
                },
            },
            {
                "edgeId": f"{surface_id}_e4",
                "startNodeId": f"{surface_id}_n4",
                "endNodeId": f"{surface_id}_n1",
                "kind": "line",
                "curve": {
                    "start": {"x": 0.0, "y": 3.0},
                    "control1": {"x": 0.0, "y": 2.0},
                    "control2": {"x": 0.0, "y": 1.0},
                    "end": {"x": 0.0, "y": 0.0},
                },
            },
        ],
    }


def _reference_annotation_payload() -> dict:
    return {
        "version": "roadgen3d_reference_annotation_v2",
        "plan_id": "plan_surface_test",
        "image_path": "test.png",
        "image_width_px": 256,
        "image_height_px": 256,
        "pixels_per_meter": 4.0,
        "centerlines": [
            {
                "id": "centerline_01",
                "label": "Centerline 1",
                "points": [
                    {"x": 100.0, "y": 128.0},
                    {"x": 156.0, "y": 128.0},
                ],
                "road_width_m": 12.0,
                "reference_width_px": 48.0,
                "forward_drive_lane_count": 1,
                "reverse_drive_lane_count": 1,
                "bike_lane_count": 0,
                "bus_lane_count": 0,
                "parking_lane_count": 0,
                "highway_type": "annotated_centerline",
                "cross_section_mode": "coarse",
                "cross_section_strips": [],
                "street_furniture_instances": [],
                "start_junction_id": "",
                "end_junction_id": "",
            }
        ],
        "junctions": [],
        "roundabouts": [],
        "control_points": [],
        "building_regions": [],
        "functional_zones": [],
        "compositions": [
            {
                "junctionId": "junction_test",
                "kind": "cross_junction",
                "quadrants": [],
                "laneSurfaces": [
                    _square_surface("lane_surface_north_inbound_01", "lane_01"),
                ],
                "mergedSurfaces": [
                    _square_surface("turn_surface_01", "turn_lane_01", provenance="merged"),
                ],
            }
        ],
    }


def test_reference_annotation_parses_lane_and_merged_surfaces() -> None:
    annotation = parse_reference_annotation(_reference_annotation_payload())

    assert annotation.junction_compositions
    composition = annotation.junction_compositions[0]
    assert composition.junction_id == "junction_test"
    assert len(composition.lane_surfaces) == 1
    assert len(composition.merged_surfaces) == 1
    assert composition.lane_surfaces[0].surface_id == "lane_surface_north_inbound_01"
    assert composition.merged_surfaces[0].provenance == "merged"
    assert composition.to_dict()["lane_surfaces"][0]["nodes"][0]["node_id"] == "lane_surface_north_inbound_01_n1"


def test_reference_annotation_bridge_emits_surface_polygons() -> None:
    annotation = parse_reference_annotation(_reference_annotation_payload())
    bridge = build_reference_annotation_scene_bridge(annotation)

    geometry = next(item for item in bridge.placement_context.junction_geometries if item.get("junction_id") == "junction_test")
    assert geometry["lane_surface_patches"]
    assert geometry["merged_surface_patches"]
    assert geometry["lane_surface_patches"][0]["surface_id"] == "lane_surface_north_inbound_01"
    assert geometry["lane_surface_patches"][0]["geometry"].area > 0
    assert geometry["merged_surface_patches"][0]["geometry"].area > 0
