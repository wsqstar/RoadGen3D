from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.services import starter_scenes
from web.api.main import create_app


SCENE_ID = "guangzhou_road_skeleton_v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    assert normalized["summary"]["street_furniture_instance_count"] == 0
    assert normalized["osm_study"]["selection"]["hop_count"] == 1
    assert normalized["osm_study"]["selection"]["context_buffer_m"] == 100.0

    directory = starter_scenes.STARTER_ROOT / SCENE_ID
    scene_layout = json.loads((directory / "scene_layout.json").read_text(encoding="utf-8"))
    manifest = starter_scenes.starter_scene_manifest(SCENE_ID)
    assert scene_layout["placements"] == []
    assert scene_layout["building_placements"] == []
    assert scene_layout["config"]["street_furniture_profile"] == "none"
    assert scene_layout["config"]["amenity_coverage_mode"] == "off"
    assert manifest["instances"] == {}
    assert manifest["layout_overlay"]["road_centerlines"]
    assert manifest["final_scene"]["glb_url"].endswith("/road_base.glb")

    for name in ("package.json", "normalized_source.json", "scene_layout.json", "viewer_manifest.json"):
        text = (directory / name).read_text(encoding="utf-8")
        assert "/Users/" not in text
        assert "artifacts/" not in text


def test_starter_materialization_is_idempotent_and_never_mutates_bundle(tmp_path, monkeypatch) -> None:
    source_glb = starter_scenes.STARTER_ROOT / SCENE_ID / "road_base.glb"
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
    assert manifest["instances"] == {}
    assert manifest["final_scene"]["glb_url"] == f"/api/starter-scenes/{SCENE_ID}/files/road_base.glb"

    glb_response = client.get(manifest["final_scene"]["glb_url"])
    assert glb_response.status_code == 200
    assert glb_response.headers["content-type"].startswith("model/gltf-binary")
    assert glb_response.content[:4] == b"glTF"

    assert client.get("/api/starter-scenes/not-registered/manifest").status_code == 404
    assert client.get(f"/api/starter-scenes/{SCENE_ID}/files/scene_layout.json").status_code == 404
