from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
import sys

from fastapi.testclient import TestClient
import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union
import trimesh

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.services import starter_scenes
from web.api.main import create_app


SCENE_ID = "guangzhou_complete_intersection_v3"
GEOMETRY_SCENE_ID = "guangzhou_road_skeleton_v2"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _glb_top_projection(scene: trimesh.Scene, node_prefix: str):
    flattened = scene.graph.to_flattened()
    triangles = []
    for node_name in scene.graph.nodes_geometry:
        if not str(node_name).startswith(node_prefix):
            continue
        item = flattened[node_name]
        mesh = scene.geometry[item["geometry"]].copy()
        mesh.apply_transform(np.asarray(item["transform"]))
        for triangle in mesh.triangles:
            if float(np.ptp(triangle[:, 1])) > 1e-6 or float(np.mean(triangle[:, 1])) <= 0.14:
                continue
            polygon = Polygon(triangle[:, [0, 2]])
            if polygon.is_valid and polygon.area > 1e-10:
                triangles.append(polygon)
    return unary_union(triangles)


def test_bundled_guangzhou_starter_is_offline_and_path_free() -> None:
    package = starter_scenes.load_starter_scene()

    assert package["id"] == SCENE_ID
    assert package["retrieval_bbox"] == [
        113.26616931271059,
        23.13367933500995,
        113.27325598728942,
        23.13728296499005,
    ]
    normalized = package["normalized_source"]
    annotation = normalized["annotation"]
    assert len(annotation["centerlines"]) == 6
    assert len(annotation["junctions"]) == 3
    assert len(annotation["regions"]) == 21
    assert normalized["source"]["producer"] == "osm"
    assert normalized["source"]["starter_scene"] is True
    assert package["focus_xz"] == [171.94, -84.95]
    assert package["focus_extent_m"] == 115.0
    assert package["category_counts"] == {
        "bench": 1,
        "bollard": 8,
        "building": 21,
        "lamp": 8,
        "trash": 2,
        "tree": 8,
    }
    assert normalized["osm_study"]["selection"]["hop_count"] == 1
    assert normalized["osm_study"]["selection"]["context_buffer_m"] == 100.0

    directory = starter_scenes.STARTER_ROOT / SCENE_ID
    scene_layout = json.loads((directory / "scene_layout.json").read_text(encoding="utf-8"))
    manifest = starter_scenes.starter_scene_manifest(SCENE_ID)
    placement_counts = Counter(
        str(item.get("category") or "unknown")
        for item in scene_layout["placements"]
    )
    assert dict(sorted(placement_counts.items())) == package["category_counts"]
    assert scene_layout["config"]["building_representation"] == "transparent_massing"
    assert scene_layout["config"]["junction_corner_radius_mode"] == "auto"
    assert scene_layout["config"]["junction_precision_grid_m"] == 0.001
    assert scene_layout["config"]["junction_curve_max_angle_deg"] == 2.0
    assert scene_layout["config"]["junction_curve_max_chord_m"] == 0.25
    assert scene_layout["config"]["junction_marking_setback_m"] == 0.5
    assert scene_layout["config"]["urban_lane_edge_mode"] == "explicit_only"
    assert scene_layout["config"]["curb_width_m"] == 0.12
    assert scene_layout["config"]["curb_reveal_m"] == 0.15
    osm_geometry = scene_layout["summary"]["osm_geometry"]
    surface_qa = osm_geometry["surface_geometry_qa"]
    assert surface_qa["ok"] is True
    assert surface_qa["curb_sidewalk_overlap_area_m2"] == 0.0
    assert surface_qa["curb_width_m"] == 0.12
    assert surface_qa["curb_reveal_m"] == 0.15
    assert surface_qa["curb_top_mode"] == "flush_with_sidewalk"
    assert surface_qa["mesh_boundary_clearance_m"] == 0.002
    assert surface_qa["final_surface_sliver_count"] == 0
    assert surface_qa["degenerate_top_face_count"] == 0
    assert surface_qa["minimum_top_triangle_angle_deg"] > 0.0
    marking_qa = osm_geometry["marking_geometry_qa"]
    assert marking_qa["ok"] is True
    assert marking_qa["urban_lane_edge_mode"] == "explicit_only"
    assert marking_qa["marking_junction_intrusion_area_m2"] == 0.0
    assert marking_qa["duplicate_marking_area_m2"] == 0.0
    assert marking_qa["unexpected_lane_edge_count"] == 0
    assert marking_qa["rendered_lane_edge_ribbon_count"] == 0
    junction_qa = [
        item["geometry_qa"]
        for item in osm_geometry["junction_geometries"]
        if item.get("geometry_qa")
    ]
    assert junction_qa
    assert all(item["ok"] for item in junction_qa)
    assert all(item["coplanar_overlap_area_m2"] <= 1e-4 for item in junction_qa)
    assert all(
        item["junction_uncovered_area_m2"] <= item["junction_uncovered_limit_m2"]
        for item in junction_qa
    )
    assert all(item["sliver_component_count"] == 0 for item in junction_qa)
    assert len(manifest["instances"]) == sum(package["category_counts"].values())
    assert manifest["layout_overlay"]["road_centerlines"]
    assert manifest["final_scene"]["glb_url"].endswith("/complete_scene.glb")
    assert manifest["starter_focus"] == {"center_xz": [171.94, -84.95], "extent_m": 115.0}

    for name in ("package.json", "normalized_source.json", "scene_layout.json", "viewer_manifest.json"):
        text = (directory / name).read_text(encoding="utf-8")
        assert "/Users/" not in text
        assert "artifacts/" not in text


def test_retired_starters_remain_addressable_for_existing_links() -> None:
    package = starter_scenes.load_starter_scene("guangzhou_road_skeleton_v1")

    assert package["id"] == "guangzhou_road_skeleton_v1"
    assert package["viewer_manifest_url"].endswith("/guangzhou_road_skeleton_v1/manifest")
    assert starter_scenes.load_starter_scene(GEOMETRY_SCENE_ID)["id"] == GEOMETRY_SCENE_ID


def test_v2_exported_glb_has_disjoint_curb_and_sidewalk_caps() -> None:
    scene = trimesh.load(
        starter_scenes.STARTER_ROOT / GEOMETRY_SCENE_ID / "road_base.glb",
        force="scene",
    )
    curb = _glb_top_projection(scene, "curb_")
    sidewalk = _glb_top_projection(scene, "sidewalk_")

    assert curb.area > 100.0
    assert sidewalk.area > 1000.0
    assert curb.intersection(sidewalk).area <= 1e-4

    node_names = [str(node_name) for node_name in scene.graph.nodes_geometry]
    assert not any(node_name.startswith("lane_edge_") for node_name in node_names)
    assert any(node_name.startswith("centerline_mark_") for node_name in node_names)
    face_areas = np.concatenate(
        [
            np.asarray(scene.geometry[scene.graph[node_name][1]].area_faces, dtype=float)
            for node_name in scene.graph.nodes_geometry
        ]
    )
    assert int(np.count_nonzero(face_areas <= 1e-10)) == 0


def test_starter_materialization_is_idempotent_and_never_mutates_bundle(tmp_path, monkeypatch) -> None:
    package = starter_scenes.load_starter_scene(SCENE_ID)
    source_glb = starter_scenes.STARTER_ROOT / SCENE_ID / package["scene_file"]
    bundled_before = _sha256(source_glb)
    monkeypatch.setattr(starter_scenes, "MATERIALIZED_ROOT", tmp_path.resolve())

    first = starter_scenes.materialize_starter_scene(SCENE_ID)
    layout = Path(first["layout_path"])
    first_layout_bytes = layout.read_bytes()
    first_mtime = layout.stat().st_mtime_ns
    second = starter_scenes.materialize_starter_scene(SCENE_ID)

    assert second["layout_path"] == first["layout_path"]
    assert second["source_fingerprint"] == first["source_fingerprint"]
    assert layout.read_bytes() == first_layout_bytes
    assert layout.stat().st_mtime_ns == first_mtime
    assert _sha256(source_glb) == bundled_before
    materialized = json.loads(layout.read_text(encoding="utf-8"))
    assert materialized["scene_edit"]["revision"] == 0
    assert materialized["scene_edit"]["starter_scene_id"] == SCENE_ID
    assert Path(materialized["outputs"]["scene_glb"]).is_file()


def test_starter_scene_api_serves_contract_manifest_and_glb() -> None:
    client = TestClient(create_app())

    contract_response = client.get("/api/starter-scenes/default")
    assert contract_response.status_code == 200
    contract = contract_response.json()
    assert contract["id"] == SCENE_ID
    assert contract["viewer_manifest_url"] == f"/api/starter-scenes/{SCENE_ID}/manifest"

    manifest_response = client.get(contract["viewer_manifest_url"])
    assert manifest_response.status_code == 200
    manifest = manifest_response.json()
    assert len(manifest["instances"]) == 48
    assert manifest["final_scene"]["glb_url"] == f"/api/starter-scenes/{SCENE_ID}/files/complete_scene.glb"

    glb_response = client.get(manifest["final_scene"]["glb_url"])
    assert glb_response.status_code == 200
    assert glb_response.headers["content-type"].startswith("model/gltf-binary")
    assert glb_response.content[:4] == b"glTF"

    assert client.get("/api/starter-scenes/not-registered/manifest").status_code == 404
    assert client.get(f"/api/starter-scenes/{SCENE_ID}/files/scene_layout.json").status_code == 404
