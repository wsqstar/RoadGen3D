from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import sys
import struct

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import numpy as np
import pytest
from fastapi.testclient import TestClient

import trimesh

from roadgen3d.osm_ingest import OsmBuilding, OsmFeatures
from roadgen3d.diff_engine import compute_placements_diff
from roadgen3d.reference_annotation_scene_bridge import build_reference_annotation_scene_bridge
from roadgen3d.scene_layout_edits import SceneRevisionConflict, _apply_commands, _normalize_commands, apply_scene_layout_edits
from roadgen3d.scene_sources import normalize_scene_source
from roadgen3d.street_layout import _placeholder_building_entry
from roadgen3d.theme_buildings import collect_building_footprints

from web.api.routers import scene_sources as scene_sources_router
from web.api.main import create_app


def _manual_geojson_source() -> dict:
    return {
        "kind": "geojson",
        "source_id": "manual-map",
        "producer": "manual",
        "coordinate_space": "image_px",
        "image": {
            "path": "/tmp/manual-map.png",
            "width_px": 200,
            "height_px": 100,
            "pixels_per_meter": 2.0,
        },
        "geojson": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "road-1",
                    "properties": {"role": "centerline", "road_width_m": 8.0},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[10.0, 50.0], [190.0, 50.0]],
                    },
                },
                {
                    "type": "Feature",
                    "id": "building-1",
                    "properties": {"role": "building", "height_m": 12.0},
                    "geometry": {

                        "type": "Polygon",
                        "coordinates": [[[30.0, 10.0], [70.0, 10.0], [70.0, 40.0], [30.0, 40.0], [30.0, 10.0]]],
                    },
                },
            ],
        },
    }


def test_scene_source_normalization_api_returns_typed_graph_payload() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/scene-sources/normalize",
        json={"source": _manual_geojson_source(), "compose_config": {"sidewalk_width_m": 3.0}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["annotation"]["version"] == "roadgen3d_reference_annotation_v2"
    assert payload["source"]["source_id"] == "manual-map"
    assert payload["source_alignment"]["status"] == "aligned"
    assert payload["aligned_buildings"][0]["source_id"] == "building-1"


def test_osm_building_source_api_preserves_wgs84_footprints(monkeypatch) -> None:
    monkeypatch.setattr(scene_sources_router, "fetch_osm_data", lambda *_args, **_kwargs: {"elements": []})
    monkeypatch.setattr(
        scene_sources_router,
        "parse_osm_features",
        lambda _raw: OsmFeatures(
            buildings=[
                OsmBuilding(
                    osm_id=42,
                    coords=[
                        (114.160, 22.290),
                        (114.161, 22.290),
                        (114.161, 22.291),
                        (114.160, 22.291),
                        (114.160, 22.290),
                    ],
                    tags={"building": "yes", "building:levels": "4"},
                )
            ]
        ),
    )
    response = TestClient(create_app()).post(
        "/api/scene-sources/osm-buildings",
        json={"source_id": "osm-test", "aoi_bbox": [114.15, 22.28, 114.17, 22.30]},
    )

    assert response.status_code == 200
    payload = response.json()
    feature = payload["geojson"]["features"][0]
    assert feature["id"] == "osm-building-42"
    assert feature["properties"]["role"] == "building_footprint"
    assert feature["properties"]["editable"] is False
    assert feature["geometry"]["coordinates"][0][0] == feature["geometry"]["coordinates"][0][-1]


def test_vision_extraction_api_normalizes_model_geojson(monkeypatch) -> None:
    class FakeSettings:
        def public_identity(self, capability=None):
            return {"provider": "openai", "model": "gpt-4o-mini", "capability": capability}

    class FakeClient:
        settings = FakeSettings()

        def chat_json(self, _messages, *, temperature, capability):
            assert temperature == 0.0
            assert capability == "vision"
            return {
                "geojson": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "id": "road-ai",
                            "properties": {"role": "centerline", "road_width_m": 7.0},
                            "geometry": {"type": "LineString", "coordinates": [[2.0, 4.0], [30.0, 4.0]]},
                        }
                    ],
                }
            }

        def close(self):
            return None

    monkeypatch.setattr(scene_sources_router, "LLMClient", FakeClient)
    from PIL import Image
    from io import BytesIO

    buffer = BytesIO()
    Image.new("RGB", (32, 16), (240, 240, 240)).save(buffer, format="PNG")
    response = TestClient(create_app()).post(
        "/api/scene-sources/extract",
        json={
            "source_id": "vision-test",
            "image_data_url": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
            "prompt": "Trace only visible roads.",
            "image": {"path": "vision-test.png", "width_px": 32, "height_px": 16, "pixels_per_meter": 1.0},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["annotation"]["centerlines"][0]["id"] == "road-ai"
    assert payload["source"]["producer"] == "ai"
    assert payload["llm"]["model"] == "gpt-4o-mini"




def test_manual_geojson_normalizes_to_annotation_and_aligned_buildings() -> None:
    result = normalize_scene_source(_manual_geojson_source())

    assert result.annotation["version"] == "roadgen3d_reference_annotation_v2"
    assert result.annotation["centerlines"][0]["id"] == "road-1"
    assert result.annotation["centerlines"][0]["road_width_m"] == pytest.approx(8.0)
    assert result.aligned_buildings[0]["source_id"] == "building-1"
    assert result.aligned_buildings[0]["editable"] is False
    assert result.source_alignment["status"] == "aligned"
    assert len(result.source["annotation_sha256"]) == 64


def test_aligned_osm_buildings_become_noneditable_white_context_massing() -> None:
    normalized = normalize_scene_source(_manual_geojson_source())
    bridge = build_reference_annotation_scene_bridge(
        normalized.annotation,
        compose_config={},
        aligned_buildings=[
            {
                "osm_id": "42",
                "polygon_xz": [[-10.0, 25.0], [0.0, 25.0], [0.0, 35.0], [-10.0, 35.0], [-10.0, 25.0]],
                "tags": {"building:levels": "4"},
            }
        ],
        source_alignment={"status": "aligned"},
    )

    assert bridge.projected_features.buildings[0].tags["roadgen3d_context_massing"] == "white"
    footprints = collect_building_footprints(
        bridge.projected_features,
        placement_context=bridge.placement_context,
        theme_segments=[],
        road_segment_graph=bridge.road_segment_graph,
        road_buffer_m=80.0,
    )
    assert len(footprints) == 1
    assert footprints[0].source == "osm_context_white_massing"
    assert footprints[0].target_height_m == pytest.approx(12.0)

    entry = _placeholder_building_entry(
        asset_id="osm_white_massing_42",
        frontage_width_m=10.0,
        depth_m=10.0,
        height_class="midrise",
        theme_name="context_white_massing",
        target_height_m=12.0,
    )
    material = entry.mesh.visual.material
    assert material.name == "roadgen3d_transparent_massing"
    assert material.baseColorFactor.tolist() == [244, 247, 248, 107]
    assert material.alphaMode == "BLEND"
    assert material.roughnessFactor == pytest.approx(1.0)
    assert material.metallicFactor == pytest.approx(0.0)


def _write_editable_scene(root: Path) -> tuple[Path, dict]:
    scene_dir = root / "source"
    scene_dir.mkdir(parents=True)
    scene = trimesh.Scene()
    transform = np.eye(4)
    transform[:3, 3] = [1.0, 0.25, 2.0]
    scene.add_geometry(
        trimesh.creation.box(extents=[1.0, 0.5, 1.0]),
        node_name="inst_0001_box",
        geom_name="box",
        transform=transform,
    )
    glb_path = scene_dir / "scene.glb"
    glb_path.write_bytes(scene.export(file_type="glb"))
    layout = {
        "version": "roadgen3d.scene_layout.v1",
        "placements": [
            {
                "instance_id": "inst_0001",
                "category": "bench",
                "position_xyz": [1.0, 0.0, 2.0],
                "bbox_xz": [0.5, 1.5, 1.5, 2.5],
                "selection_source": "curated_allowlist_stable",
            }
        ],
        "summary": {"overall": 81.0},
        "outputs": {"scene_glb": str(glb_path)},
    }
    layout_path = scene_dir / "scene_layout.json"
    layout_path.write_text(json.dumps(layout), encoding="utf-8")
    return layout_path, layout


def test_scene_edit_publishes_new_layout_and_glb_without_mutating_source(tmp_path: Path) -> None:
    editable_root = tmp_path / "artifacts"
    revision_root = editable_root / "scene_layout_edits"
    layout_path, source_layout = _write_editable_scene(editable_root)
    source_bytes = layout_path.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest()

    result = apply_scene_layout_edits(
        layout_path=layout_path,
        base_revision=0,
        base_sha256=source_sha,
        commands=[
            {
                "command_id": "move-1",
                "op": "move_instance",
                "instance_id": "inst_0001",
                "position_xyz": [4.0, 0.0, 6.0],
            }
        ],
        editable_root=editable_root,
        revision_root=revision_root,
    )

    assert layout_path.read_bytes() == source_bytes
    published_layout_path = Path(result["revision"]["layout_path"])
    published_glb_path = Path(result["revision"]["scene_glb_path"])
    assert published_layout_path.is_file()
    assert published_glb_path.is_file()
    published = json.loads(published_layout_path.read_text(encoding="utf-8"))
    assert published["placements"][0]["position_xyz"] == [4.0, 0.0, 6.0]
    assert published["placements"][0]["bbox_xz"] == pytest.approx([3.5, 4.5, 5.5, 6.5])
    assert published["scene_edit"]["revision"] == 1
    assert published["summary"]["scene_edit_validation_status"] == "pending_re_evaluation"
    assert result["undo"]["commands"][0]["position_xyz"] == source_layout["placements"][0]["position_xyz"]

    scene = trimesh.load(published_glb_path, force="scene", process=False)
    node = next(name for name in scene.graph.nodes if str(name).startswith("inst_0001"))
    transform, _ = scene.graph.get(node)
    assert transform[:3, 3].tolist() == pytest.approx([4.0, 0.25, 6.0])

    glb_bytes = published_glb_path.read_bytes()
    json_length, _ = struct.unpack_from("<II", glb_bytes, 12)
    glb_document = json.loads(glb_bytes[20:20 + json_length].decode("utf-8").rstrip("\x00 "))
    node_record = next(
        item for item in glb_document["nodes"]
        if str(item.get("name", "")).startswith("inst_0001")
    )
    assert node_record["extras"]["instance_id"] == "inst_0001"
    assert node_record["extras"]["position_xyz"] == [4.0, 0.0, 6.0]
    assert node_record["extras"]["bbox_xz"] == pytest.approx([3.5, 4.5, 5.5, 6.5])

    with pytest.raises(SceneRevisionConflict) as conflict:
        apply_scene_layout_edits(
            layout_path=layout_path,
            base_revision=0,
            base_sha256=source_sha,
            commands=[
                {
                    "command_id": "move-stale",
                    "op": "move_instance",
                    "instance_id": "inst_0001",
                    "position_xyz": [5.0, 0.0, 7.0],
                }
            ],
            editable_root=editable_root,
            revision_root=revision_root,
        )
    assert conflict.value.current["revision"] == 1


def test_scene_edit_command_protocol_supports_full_student_edit_set() -> None:
    payload = {
        "placements": [
            {"instance_id": "tree-1", "asset_id": "tree-a", "category": "tree", "position_xyz": [0, 0, 0], "bbox_xz": [-0.5, 0.5, -0.5, 0.5], "yaw_deg": 0, "scale": 1},
            {"instance_id": "building-1", "asset_id": "building-a", "category": "building", "position_xyz": [5, 0, 5], "bbox_xz": [4, 6, 4, 6], "yaw_deg": 0, "scale": 1},
        ],
        "building_placements": [{"instance_id": "building-1", "style_id": "default"}],
    }
    commands = _normalize_commands([
        {"command_id": "move", "op": "move_instance", "instance_id": "tree-1", "position_xyz": [1, 0, 2]},
        {"command_id": "rotate", "op": "rotate_instance", "instance_id": "tree-1", "yaw_deg": 90},
        {"command_id": "scale", "op": "scale_instance", "instance_id": "tree-1", "scale": 1.5},
        {"command_id": "duplicate", "op": "duplicate_instance", "instance_id": "tree-1", "new_instance_id": "tree-2", "position_xyz": [3, 0, 2]},
        {"command_id": "replace", "op": "replace_asset", "instance_id": "tree-2", "asset_id": "tree-b", "category": "tree"},
        {"command_id": "style", "op": "set_building_style", "instance_id": "building-1", "style_id": "lingnan"},
        {"command_id": "plant", "op": "auto_plant_trees", "asset_id": "tree-auto", "points_xyz": [[7, 0, 1], [9, 0, 1]]},
        {"command_id": "delete", "op": "delete_instance", "instance_id": "tree-1"},
    ])

    updated, applied, inverse = _apply_commands(payload, commands)

    by_id = {item["instance_id"]: item for item in updated["placements"]}
    assert "tree-1" not in by_id
    assert by_id["tree-2"]["asset_id"] == "tree-b"
    assert by_id["tree-2"]["position_xyz"] == [3.0, 0.0, 2.0]
    assert updated["building_placements"][0]["style_id"] == "lingnan"
    assert len([key for key in by_id if key.startswith("auto-tree-plant")]) == 2
    assert len(applied) == 9  # auto planting expands into two applied rows
    assert inverse[-1]["command"]["op"] == "add_instance"


def test_scene_edit_rotates_and_scales_glb_and_publishes_revision(tmp_path: Path) -> None:
    editable_root = tmp_path / "artifacts"
    layout_path, _ = _write_editable_scene(editable_root)
    source_sha = hashlib.sha256(layout_path.read_bytes()).hexdigest()

    result = apply_scene_layout_edits(
        layout_path=layout_path,
        base_revision=0,
        base_sha256=source_sha,
        commands=[
            {"command_id": "rotate", "op": "rotate_instance", "instance_id": "inst_0001", "yaw_deg": 90},
            {"command_id": "scale", "op": "scale_instance", "instance_id": "inst_0001", "scale": 2},
        ],
        editable_root=editable_root,
        revision_root=editable_root / "scene_layout_edits",
    )

    published = json.loads(Path(result["revision"]["layout_path"]).read_text(encoding="utf-8"))
    assert published["placements"][0]["yaw_deg"] == pytest.approx(90)
    assert published["placements"][0]["scale"] == pytest.approx(2)
    assert [item["op"] for item in result["undo"]["commands"]] == ["scale_instance", "rotate_instance"]
    scene = trimesh.load(result["revision"]["scene_glb_path"], force="scene", process=False)
    node = next(name for name in scene.graph.nodes if str(name).startswith("inst_0001"))
    transform, _ = scene.graph.get(node)
    assert np.linalg.norm(transform[:3, 0]) == pytest.approx(2.0)




def test_placement_diff_uses_durable_instance_identity_before_proximity() -> None:
    before = {
        "placements": [
            {"instance_id": "a", "category": "tree", "position_xyz": [0.0, 0.0, 0.0]},
            {"instance_id": "b", "category": "tree", "position_xyz": [10.0, 0.0, 0.0]},
        ]
    }
    after = {
        "placements": [
            {"instance_id": "a", "category": "tree", "position_xyz": [9.9, 0.0, 0.0]},
            {"instance_id": "b", "category": "tree", "position_xyz": [0.1, 0.0, 0.0]},
        ]
    }

    diff = compute_placements_diff(before, after)

    moved = {item["instance_id"]: item for item in diff["moved_instances"]}
    assert moved["a"]["distance_m"] == pytest.approx(9.9)
    assert moved["b"]["distance_m"] == pytest.approx(9.9)
    assert {item["match_method"] for item in moved.values()} == {"instance_id"}
