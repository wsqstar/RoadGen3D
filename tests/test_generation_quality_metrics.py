from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
EVAL_ENGINE_EXT = SRC / "roadgen3d" / "eval_engine_ext"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(EVAL_ENGINE_EXT) not in sys.path:
    sys.path.insert(0, str(EVAL_ENGINE_EXT))

from road_metrics.core.config import EvalConfig  # noqa: E402
from road_metrics.core.engine import EvalEngine  # noqa: E402
from road_metrics.metrics.generation_quality import (  # noqa: E402
    evaluate_geometry_validity,
    evaluate_json_glb_consistency,
)
from road_metrics.metrics.walkability import compute_walkability  # noqa: E402
from roadgen3d.llm.design_workflow import DesignAssistantService  # noqa: E402
from roadgen3d.evaluation_report import evaluate_quality_batch, write_quality_report  # noqa: E402


trimesh = pytest.importorskip("trimesh")


def _lamp(instance_id: str, x: float) -> dict:
    return {
        "instance_id": instance_id,
        "category": "lamp",
        "position_xyz": [x, 0.0, 4.0],
        "bbox_xz": [x - 0.15, x + 0.15, 3.85, 4.15],
    }


def test_light_uniformity_requires_adequate_lamp_count():
    common = {
        "length_m": 80.0,
        "road_width_m": 8.0,
        "sidewalk_width_m": 3.0,
    }

    no_lamps = compute_walkability(placements=[], **common)
    one_lamp = compute_walkability(placements=[_lamp("inst_0001", 0.0)], **common)
    even_lamps = compute_walkability(
        placements=[_lamp("inst_0001", -20.0), _lamp("inst_0002", 0.0), _lamp("inst_0003", 20.0)],
        **common,
    )

    assert no_lamps.light_uni == 0.0
    assert one_lamp.light_uni == 0.3
    assert even_lamps.light_uni == 1.0


def test_furniture_density_split_and_top_contributors_are_explainable():
    bench = {
        "instance_id": "inst_0001",
        "category": "bench",
        "position_xyz": [0.0, 0.0, 4.0],
        "bbox_xz": [-0.5, 0.5, 3.5, 4.5],
    }

    result = compute_walkability(
        placements=[bench],
        length_m=10.0,
        road_width_m=4.0,
        sidewalk_width_m=3.0,
        left_clear_path_width_m=2.0,
        right_clear_path_width_m=2.0,
        left_furnishing_width_m=1.0,
        right_furnishing_width_m=1.0,
    )
    payload = result.to_dict()

    assert result.amenity_service_density_score > 0.0
    assert result.furniture_occupation_ratio > 0.0
    assert result.clear_path_conflict_penalty > 0.0
    assert result.furn_d < result.amenity_service_density_score
    assert "AMENITY_SERVICE_DENSITY" in payload["indicators"]
    assert any(item["polarity"] == "positive" for item in result.top_contributors)
    assert any(item["polarity"] == "negative" for item in result.top_contributors)


def test_local_segment_profile_reduces_transit_proximity_weight():
    common = {
        "placements": [],
        "length_m": 80.0,
        "road_width_m": 8.0,
        "sidewalk_width_m": 1.0,
        "left_clear_path_width_m": 0.0,
        "right_clear_path_width_m": 0.0,
        "mean_entrance_openness": 0.0,
        "bus_stop_points_xz": [[0.0, 7.0]],
        "land_use_summary": {},
    }
    local_config = EvalConfig.for_profile("local_segment_v1")
    network_config = EvalConfig.for_profile("network_v1")

    local = compute_walkability(config=local_config.walkability, **common)
    network = compute_walkability(config=network_config.walkability, **common)

    local_transit = next(
        item for item in local.top_contributors
        if item["feature"] == "TRANSIT_PROX" and item["polarity"] == "positive"
    )
    network_transit = next(
        item for item in network.top_contributors
        if item["feature"] == "TRANSIT_PROX" and item["polarity"] == "positive"
    )
    assert local_config.walkability.delight_component_weights["TRANSIT_PROX"] < network_config.walkability.delight_component_weights["TRANSIT_PROX"]
    assert local_transit["weight"] < network_transit["weight"]


def _box_node(
    scene,
    name: str,
    *,
    xyz: tuple[float, float, float],
    extents: tuple[float, float, float] = (2, 1, 2),
    metadata: dict | None = None,
) -> None:
    mesh = trimesh.creation.box(extents=extents)
    transform = trimesh.transformations.translation_matrix([float(xyz[0]), float(xyz[1]), float(xyz[2])])
    scene.add_geometry(mesh, node_name=name, transform=transform, metadata=metadata)


def _node_metadata(instance_id: str, category: str, asset_id: str) -> dict:
    return {
        "schema": "roadgen3d_instance_metadata_v1",
        "instance_id": instance_id,
        "category": category,
        "asset_id": asset_id,
        "source_bbox": [-1.0, 1.0, -1.0, 1.0],
    }


def _quality_payload(tmp_path: Path) -> dict:
    scene = trimesh.Scene()
    _box_node(scene, "inst_0001", xyz=(0.0, 0.5, 0.0), metadata=_node_metadata("inst_0001", "bench", "asset_bench"))
    _box_node(scene, "inst_0002_door_0", xyz=(0.5, 0.5, 0.0), metadata=_node_metadata("inst_0002", "lamp", "asset_lamp"))
    _box_node(scene, "inst_0003", xyz=(0.0, 2.0, 4.0), metadata=_node_metadata("inst_0003", "tree", "asset_tree"))
    _box_node(scene, "inst_9999", xyz=(20.0, 0.5, 0.0), metadata=_node_metadata("inst_9999", "bench", "asset_extra"))
    glb_path = tmp_path / "scene.glb"
    scene.export(glb_path)
    layout_path = tmp_path / "scene_layout.json"
    return {
        "summary": {
            "length_m": 40.0,
            "road_width_m": 4.0,
            "sidewalk_width_m": 3.0,
            "left_clear_path_width_m": 2.0,
            "right_clear_path_width_m": 2.0,
            "left_furnishing_width_m": 1.0,
            "right_furnishing_width_m": 1.0,
            "entrance_count": 1,
            "mean_entrance_openness": 0.8,
            "road_segment_graph_summary": {"segment_count": 4, "edge_count": 4, "graph_junction_count": 1},
            "osm_geometry": {
                "carriageway_rings": [[[-2, -2], [2, -2], [2, 2], [-2, 2]]],
                "sidewalk_rings": [[[-2, 2], [2, 2], [2, 5], [-2, 5]]],
                "junction_geometries": [
                    {
                        "junction_id": "junction_01",
                        "arm_count": 4,
                        "connected_road_ids": [1, 2, 3, 4],
                        "carriageway_core_rings": [[[-1, -1], [1, -1], [1, 1], [-1, 1]]],
                        "approach_boundaries": [{}, {}, {}, {}],
                        "crosswalk_patches": [
                            {"rings": [[[-1, 1], [1, 1], [1, 1.5], [-1, 1.5]]]},
                            {"rings": [[[-1, -1.5], [1, -1.5], [1, -1], [-1, -1]]]},
                            {"rings": [[[-1.5, -1], [-1, -1], [-1, 1], [-1.5, 1]]]},
                            {"rings": [[[1, -1], [1.5, -1], [1.5, 1], [1, 1]]]},
                        ],
                        "sidewalk_corner_patches": [
                            {"rings": [[[1, 1], [2, 1], [2, 2], [1, 2]]]},
                            {"rings": [[[-2, 1], [-1, 1], [-1, 2], [-2, 2]]]},
                            {"rings": [[[1, -2], [2, -2], [2, -1], [1, -1]]]},
                            {"rings": [[[-2, -2], [-1, -2], [-1, -1], [-2, -1]]]},
                        ],
                        "quadrant_corner_kernels": [
                            {"radius_m": 2.0, "sampled_points_xy": [[0, 0], [1, 1], [2, 0]], "start_heading_deg": 0, "end_heading_deg": 90},
                            {"radius_m": 2.0, "sampled_points_xy": [[0, 0], [-1, 1], [-2, 0]], "start_heading_deg": 90, "end_heading_deg": 180},
                        ],
                    }
                ],
            },
            "composition_report": {"presentation_score": 0.6, "visual_clutter": 0.2},
        },
        "config": {"lane_count": 2},
        "street_program": {
            "bands": [
                {"kind": "drive_lane", "width_m": 3.3},
                {"kind": "drive_lane", "width_m": 3.3},
            ]
        },
        "placements": [
            {"instance_id": "inst_0001", "asset_id": "asset_bench", "category": "bench", "position_xyz": [0.0, 0.0, 0.0], "bbox_xz": [-1.0, 1.0, -1.0, 1.0]},
            {"instance_id": "inst_0002", "asset_id": "asset_lamp", "category": "lamp", "position_xyz": [0.5, 0.0, 0.0], "bbox_xz": [0.0, 1.0, -0.5, 0.5]},
            {"instance_id": "inst_0003", "asset_id": "asset_tree", "category": "tree", "position_xyz": [0.0, 0.0, 4.0], "bbox_xz": [-1.0, 1.0, 3.5, 4.5]},
            {"instance_id": "inst_0004", "asset_id": "asset_missing", "category": "bench", "position_xyz": [5.0, 0.0, 4.0], "bbox_xz": [4.5, 5.5, 3.5, 4.5]},
        ],
        "outputs": {"scene_glb": str(glb_path), "scene_layout": str(layout_path)},
    }


def test_json_glb_consistency_matches_instance_nodes_and_children(tmp_path: Path):
    payload = _quality_payload(tmp_path)

    result = evaluate_json_glb_consistency(payload)

    assert result["available"] is True
    assert result["matched_count"] == 3
    assert result["expected_count"] == 4
    assert result["object_recall"] == 0.75
    assert result["category_accuracy"] == 1.0
    assert result["asset_id_match_rate"] == 1.0
    assert result["metadata_matched_count"] == 3
    assert "inst_0004" in result["missing_instance_ids"]
    assert "inst_9999" in result["extra_glb_nodes"]
    assert result["mean_position_error_m"] == 0.0


def test_geometry_validity_reports_obstructions_and_floating_objects(tmp_path: Path):
    payload = _quality_payload(tmp_path)

    result = evaluate_geometry_validity(payload)

    assert result["available"] is True
    assert result["floating_instance_ids"] == ["inst_0003"]
    assert result["road_conflict_count"] >= 2
    assert result["blocked_clear_path_ratio"] > 0.0
    assert result["mesh_aabb_collision_count"] >= 1
    assert result["topology_continuity"]["junction_correctness_score"] == 1.0
    assert result["topology_continuity"]["lane_width_consistency_score"] == 1.0
    assert result["score"] < 1.0


def test_eval_engine_and_service_expose_quality_layers(tmp_path: Path):
    payload = _quality_payload(tmp_path)
    layout_path = Path(payload["outputs"]["scene_layout"])
    layout_path.write_text(json.dumps(payload), encoding="utf-8")

    engine = EvalEngine(EvalConfig(enable_llm_eval=False, enable_audio_profile=False))
    direct = engine.evaluate(payload)

    assert direct.quality_layers["json_glb_consistency"]["available"] is True
    assert direct.quality_layers["geometry_validity"]["available"] is True
    assert direct.quality_layers["visual_perception"]["available"] is False
    assert direct.generation_quality_score is None

    service = DesignAssistantService()
    service.eval_engine = engine
    response = service.evaluate_scene_unified(layout_path=str(layout_path))

    assert response["quality_layers"]["json_glb_consistency"]["available"] is True
    assert response["generation_quality_score"] is None
    assert "walkability_top_contributors" in response["indicators"]


def test_batch_quality_report_summarizes_layer_distributions(tmp_path: Path):
    payload = _quality_payload(tmp_path)
    layout_path = Path(payload["outputs"]["scene_layout"])
    layout_path.write_text(json.dumps(payload), encoding="utf-8")

    report, rows = evaluate_quality_batch([layout_path])

    assert report["scene_count"] == 1
    assert report["layer_availability"]["json_glb_consistency"]["available_count"] == 1
    assert rows[0]["mesh_aabb_collision_count"] >= 1

    outputs = write_quality_report(report, rows, tmp_path / "quality_report")
    assert Path(outputs["quality_report"]).exists()
    assert Path(outputs["quality_per_scene_csv"]).exists()
