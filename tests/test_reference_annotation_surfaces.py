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

from roadgen3d.reference_annotation import parse_reference_annotation
from roadgen3d.junction_surface_normalization import (
    normalize_junction_surface_geometries,
    normalize_junction_surface_geometry,
)
from roadgen3d.reference_annotation_scene_bridge import build_reference_annotation_scene_bridge
from roadgen3d.street_layout import _build_osm_base_scene


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
    assert geometry["normalized_surface_patches"]
    carriageway_surfaces = [
        patch for patch in geometry["normalized_surface_patches"] if patch["surface_role"] == "carriageway"
    ]
    assert len(carriageway_surfaces) == 1
    assert set(carriageway_surfaces[0]["source_kinds"]) == {"lane_surface_patch", "merged_surface_patch"}
    assert geometry["surface_normalization_debug"]["generation_mode"] == "junction_surface_normalization_v1"
    assert geometry["surface_normalization_debug"]["overlap_removed_area_m2"] > 0

    scene = _build_osm_base_scene(bridge.placement_context)
    node_names = set(scene.graph.nodes_geometry)
    assert any(name.startswith("carriageway_") for name in node_names)
    assert not any(name.startswith("junction_normalized_surface_") for name in node_names)
    assert "context_ground_base" in node_names
    surface_qa = scene.metadata["surface_geometry_qa"]
    assert surface_qa["needle_top_face_count"] == 0
    assert surface_qa["short_boundary_edge_count"] == 0
    assert surface_qa["road_junction_seam_gap_area_m2"] <= 1e-4
    assert surface_qa["context_ground_exposure_inside_row_m2"] <= 1e-4
    assert surface_qa["rendered_surface_uncovered_area_m2"] <= 1e-4
    assert not any(name.startswith("junction_lane_surface_") for name in node_names)
    assert not any(name.startswith("junction_merged_surface_") for name in node_names)


def test_junction_surface_normalization_partitions_and_keeps_raw_debug() -> None:
    pytest.importorskip("shapely")
    from shapely.geometry import LineString, box

    normalized = normalize_junction_surface_geometry(
        {
            "junction_id": "junction_partition",
            "carriageway_core": box(0.0, 0.0, 10.0, 10.0),
            "turn_lane_patches": [
                {"patch_id": "bike_patch", "strip_kind": "bike_lane", "geometry": box(0.0, 0.0, 4.0, 10.0)},
                {"patch_id": "drive_patch", "surface_role": "carriageway", "geometry": box(2.0, 0.0, 10.0, 10.0)},
            ],
            "sidewalk_corner_patches": [
                {"patch_id": "sidewalk_patch", "surface_role": "sidewalk", "geometry": box(10.0, 0.0, 12.0, 10.0)},
                {"patch_id": "empty_sidewalk_patch", "surface_role": "sidewalk", "geometry": LineString([(0.0, 0.0), (1.0, 1.0)])},
            ],
            "crosswalk_patches": [
                {
                    "patch_id": "crosswalk_patch",
                    "horizontal_axes": [[1.0, 0.0], [0.0, 1.0]],
                    "geometry": box(1.0, 1.0, 5.0, 3.0),
                },
            ],
        }
    )

    surfaces = normalized["normalized_surface_patches"]
    assert {patch["surface_role"] for patch in surfaces} == {"bike_lane", "carriageway", "sidewalk", "crossing"}
    assert normalized["surface_normalization_debug"]["skipped_geometry_count"] == 1
    assert normalized["surface_normalization_debug"]["overlap_removed_area_m2"] > 0
    planar = [patch for patch in surfaces if not patch["is_overlay"]]
    for index, patch_a in enumerate(planar):
        for patch_b in planar[index + 1:]:
            assert patch_a["geometry"].intersection(patch_b["geometry"]).area < 1e-6
    crossing = next(patch for patch in surfaces if patch["surface_role"] == "crossing")
    assert crossing["is_overlay"] is True
    assert crossing["horizontal_axes"] == [[1.0, 0.0], [0.0, 1.0]]


def test_continuous_junction_qa_rejects_unresolved_sliver() -> None:
    pytest.importorskip("shapely")
    from shapely.geometry import box

    with pytest.raises(ValueError, match=r"Junction surface QA failed.*slivers=1"):
        normalize_junction_surface_geometries([
            {
                "junction_id": "junction_bad_sliver",
                "generation_mode": "continuous_junction_fusion_auto",
                "debug_info": {"precision_grid_m": 0.001},
                "canonical_surface_patches": [
                    {
                        "surface_id": "isolated_sliver",
                        "surface_role": "sidewalk",
                        "geometry": box(0.0, 0.0, 0.005, 0.2),
                    },
                ],
            },
        ])


def test_junction_surface_normalization_keeps_crosswalk_sources_separate() -> None:
    pytest.importorskip("shapely")
    from shapely.geometry import box

    normalized = normalize_junction_surface_geometry(
        {
            "junction_id": "junction_crossing_axes",
            "crosswalk_patches": [
                {
                    "patch_id": "east_west_crosswalk",
                    "horizontal_axes": [[1.0, 0.0], [0.0, 1.0]],
                    "geometry": box(-2.0, -1.0, 2.0, 1.0),
                },
                {
                    "patch_id": "north_south_crosswalk",
                    "horizontal_axes": [[0.0, 1.0], [1.0, 0.0]],
                    "geometry": box(-1.0, -2.0, 1.0, 2.0),
                },
            ],
        }
    )

    crossings = [
        patch
        for patch in normalized["normalized_surface_patches"]
        if patch["surface_role"] == "crossing"
    ]

    assert len(crossings) == 2
    assert {tuple(patch["source_ids"]) for patch in crossings} == {
        ("east_west_crosswalk",),
        ("north_south_crosswalk",),
    }
    assert [[1.0, 0.0], [0.0, 1.0]] in [patch["horizontal_axes"] for patch in crossings]
    assert [[0.0, 1.0], [1.0, 0.0]] in [patch["horizontal_axes"] for patch in crossings]
