from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import roadgen3d.web_viewer_dev as viewer


def test_build_web_viewer_url_accepts_repo_layout_and_encodes_query(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_root = (tmp_path / "repo").resolve()
    layout_path = repo_root / "artifacts" / "real" / "scene_layout.json"
    layout_path.parent.mkdir(parents=True, exist_ok=True)
    layout_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(viewer, "ROOT", repo_root)

    url = viewer.build_web_viewer_url(layout_path)

    assert url.startswith("http://127.0.0.1:4173/?layout=")
    assert str(layout_path) not in url
    assert "scene_layout.json" in url


def test_build_web_viewer_url_accepts_scene_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_root = (tmp_path / "repo").resolve()
    scene_dir = repo_root / "artifacts" / "real" / "metaurban" / "hkust_gz_gate" / "run_001"
    layout_path = scene_dir / "scene_layout.json"
    layout_path.parent.mkdir(parents=True, exist_ok=True)
    layout_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(viewer, "ROOT", repo_root)

    url = viewer.build_web_viewer_url(scene_dir)

    assert url.startswith("http://127.0.0.1:4173/?layout=")
    assert "scene_layout.json" in url


def test_build_web_viewer_url_uses_viewer_port_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_root = (tmp_path / "repo").resolve()
    layout_path = repo_root / "artifacts" / "real" / "scene_layout.json"
    layout_path.parent.mkdir(parents=True, exist_ok=True)
    layout_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(viewer, "ROOT", repo_root)
    monkeypatch.setenv("ROADGEN_VIEWER_HOST", "127.0.0.1")
    monkeypatch.setenv("ROADGEN_VIEWER_PORT", "4181")

    url = viewer.build_web_viewer_url(layout_path)

    assert url.startswith("http://127.0.0.1:4181/?layout=")


def test_infer_spawn_payload_defaults_to_street_center():
    payload = viewer.infer_spawn_payload({"summary": {"length_m": 120.0}})

    assert payload["spawn_point"] == [0.0, 1.65, 0.0]
    assert payload["forward_vector"] == [1.0, 0.0, 0.0]


def test_infer_spawn_payload_uses_first_road_segment_and_direction():
    payload = viewer.infer_spawn_payload(
        {
            "scene_graph": {
                "nodes": [
                    {"node_id": "building:ignored", "node_type": "building", "x": -999, "z": -999},
                    {"node_id": "road_segment:001", "node_type": "road_segment", "x": 116.5, "z": 74.0},
                    {"node_id": "road_segment:002", "node_type": "road_segment", "x": 119.5, "z": 70.0},
                ]
            }
        }
    )

    assert payload["spawn_point"] == [116.5, 1.65, 74.0]
    assert payload["forward_vector"] == pytest.approx([0.6, 0.0, -0.8])


def test_build_web_viewer_dev_command_allows_external_layout_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_root = (tmp_path / "repo").resolve()
    viewer_dir = repo_root / "web" / "viewer"
    viewer_dir.mkdir(parents=True, exist_ok=True)
    external_layout = (tmp_path / "outside" / "scene_layout.json").resolve()
    external_layout.parent.mkdir(parents=True, exist_ok=True)
    external_layout.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(viewer, "ROOT", repo_root)
    monkeypatch.setattr(viewer, "VIEWER_DIR", viewer_dir)

    command = viewer.build_web_viewer_dev_command(external_layout)

    assert "ROADGEN_VIEWER_ALLOWED_ROOTS=" in command
    assert str(external_layout.parent) in command
    assert "npm --prefix" in command
    assert "--open" in command
    assert "scene_layout.json" in command


def test_is_repo_local_path_is_false_for_layouts_outside_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_root = (tmp_path / "repo").resolve()
    repo_root.mkdir(parents=True, exist_ok=True)
    external_layout = (tmp_path / "outside" / "scene_layout.json").resolve()
    external_layout.parent.mkdir(parents=True, exist_ok=True)
    external_layout.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(viewer, "ROOT", repo_root)
    assert viewer.is_repo_local_path(external_layout) is False


def test_ensure_web_viewer_assets_reports_missing_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_root = (tmp_path / "repo").resolve()
    viewer_dir = repo_root / "web" / "viewer"
    viewer_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(viewer, "ROOT", repo_root)
    monkeypatch.setattr(viewer, "VIEWER_DIR", viewer_dir)
    monkeypatch.setattr(viewer, "VIEWER_DIST_DIR", viewer_dir / "dist")

    with pytest.raises(viewer.WebViewerError, match="npm --prefix web/viewer install && npm --prefix web/viewer run build"):
        viewer.ensure_web_viewer_assets()


def test_discover_recent_scene_layouts_sorts_newest_first_and_limits_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo_root = (tmp_path / "repo").resolve()
    newest_dir = repo_root / "artifacts" / "newest_run"
    older_dir = repo_root / "artifacts" / "older_run"
    ignored_dir = repo_root / "web" / "viewer" / "node_modules" / "fake_pkg"
    newest_dir.mkdir(parents=True, exist_ok=True)
    older_dir.mkdir(parents=True, exist_ok=True)
    ignored_dir.mkdir(parents=True, exist_ok=True)

    older_layout = older_dir / "scene_layout.json"
    newest_layout = newest_dir / "scene_layout.json"
    ignored_layout = ignored_dir / "scene_layout.json"
    older_layout.write_text("{}", encoding="utf-8")
    newest_layout.write_text("{}", encoding="utf-8")
    ignored_layout.write_text("{}", encoding="utf-8")

    base_time = time.time()
    os.utime(older_layout, (base_time - 60, base_time - 60))
    os.utime(newest_layout, (base_time, base_time))
    os.utime(ignored_layout, (base_time + 60, base_time + 60))

    monkeypatch.setattr(viewer, "ROOT", repo_root)

    results = viewer.discover_recent_scene_layouts(limit=1)

    assert len(results) == 1
    assert results[0]["layout_path"] == str(newest_layout.resolve())
    assert "node_modules" not in results[0]["relative_path"]


def test_build_recent_layouts_payload_includes_display_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo_root = (tmp_path / "repo").resolve()
    scene_dir = repo_root / "artifacts" / "demo_scene"
    scene_dir.mkdir(parents=True, exist_ok=True)
    layout_path = scene_dir / "scene_layout.json"
    layout_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(viewer, "ROOT", repo_root)

    payload = viewer.build_recent_layouts_payload(limit=5)

    assert len(payload["results"]) == 1
    entry = payload["results"][0]
    assert entry["layout_path"] == str(layout_path.resolve())
    assert entry["label"].startswith("demo_scene · ")
    assert entry["relative_path"].endswith("artifacts/demo_scene/scene_layout.json")
    assert "updated_at" in entry


def test_cache_scene_layout_for_viewer_mirrors_external_layout_into_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo_root = (tmp_path / "repo").resolve()
    external_layout = (tmp_path / "outside" / "run_001" / "scene_layout.json").resolve()
    external_layout.parent.mkdir(parents=True, exist_ok=True)
    external_layout.write_text(json.dumps({"summary": {"ok": True}, "outputs": {}}), encoding="utf-8")

    monkeypatch.setattr(viewer, "ROOT", repo_root)
    monkeypatch.setattr(viewer, "VIEWER_LAYOUTS_DIR", (repo_root / "artifacts" / "web_viewer_layouts").resolve())

    cached = viewer.cache_scene_layout_for_viewer(external_layout)

    assert cached.exists()
    assert str(cached).startswith(str(repo_root))
    assert json.loads(cached.read_text(encoding="utf-8"))["summary"]["ok"] is True


def test_cache_scene_layout_for_viewer_sanitizes_infinity_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo_root = (tmp_path / "repo").resolve()
    external_layout = (tmp_path / "outside" / "run_002" / "scene_layout.json").resolve()
    external_layout.parent.mkdir(parents=True, exist_ok=True)
    external_layout.write_text('{"summary":{"dist_to_nearest_entrance_m": Infinity},"outputs":{}}', encoding="utf-8")

    monkeypatch.setattr(viewer, "ROOT", repo_root)
    monkeypatch.setattr(viewer, "VIEWER_LAYOUTS_DIR", (repo_root / "artifacts" / "web_viewer_layouts").resolve())

    cached = viewer.cache_scene_layout_for_viewer(external_layout)
    cached_text = cached.read_text(encoding="utf-8")

    assert "Infinity" not in cached_text
    assert json.loads(cached_text)["summary"]["dist_to_nearest_entrance_m"] is None


def test_cache_scene_layout_for_viewer_sanitizes_repo_local_layouts_too(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo_root = (tmp_path / "repo").resolve()
    layout_path = (repo_root / "artifacts" / "run_003" / "scene_layout.json").resolve()
    layout_path.parent.mkdir(parents=True, exist_ok=True)
    layout_path.write_text('{"summary":{"clearance_m": Infinity},"outputs":{}}', encoding="utf-8")

    monkeypatch.setattr(viewer, "ROOT", repo_root)
    monkeypatch.setattr(viewer, "VIEWER_LAYOUTS_DIR", (repo_root / "artifacts" / "web_viewer_layouts").resolve())

    cached = viewer.cache_scene_layout_for_viewer(layout_path)
    cached_text = cached.read_text(encoding="utf-8")

    assert cached != layout_path
    assert str(cached).startswith(str(viewer.VIEWER_LAYOUTS_DIR))
    assert "Infinity" not in cached_text
    assert json.loads(cached_text)["summary"]["clearance_m"] is None


def test_build_layout_manifest_exposes_plan_overlay_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo_root = (tmp_path / "repo").resolve()
    scene_dir = repo_root / "artifacts" / "run_004"
    scene_dir.mkdir(parents=True, exist_ok=True)
    scene_glb = scene_dir / "scene.glb"
    scene_glb.write_bytes(b"glb")
    step_glb = scene_dir / "road_base.glb"
    step_glb.write_bytes(b"glb")
    layout_path = scene_dir / "scene_layout.json"
    layout_path.write_text(
        json.dumps(
            {
                "summary": {
                    "length_m": 60,
                    "spatial_context": {"road_half_width_m": 5},
                    "semantic_profile_pair": "sparse+balanced",
                    "curated_street_assets_profile": "curated_default",
                    "furniture_balance_policy": "street_focus",
                },
                "visual_style": {"style": "test"},
                "config": {"length_m": 60, "query": "test prompt", "density": 0.8, "road_width_m": 6.4, "lane_count": 2, "seed": 99, "style_preset": "test_style"},
                "production_steps": [{"step_id": "road_base", "title": "Road Base", "glb_path": str(step_glb)}],
                "street_program": {
                    "lane_count": 2,
                    "road_width_m": 6.4,
                    "bands": [{"kind": "drive_lane", "width_m": 3.2}],
                },
                "placements": [
                    {
                        "instance_id": "inst_tree",
                        "asset_id": "tree_01",
                        "category": "tree",
                        "placement_group": "street_furniture",
                        "position_xyz": [4, 0, 2],
                        "bbox_xz": [3, 1, 5, 3],
                        "violated_rules": ["distance_to_entry", "", None],
                    }
                ],
                "generated_lots": [{"lot_id": "lot_1"}],
                "building_footprints": [{"polygon_xz": [[0, 0], [4, 0], [4, 4], [0, 4]]}],
                "building_regions": [{"points": [[0, 0], [5, 0], [5, 5], [0, 5]]}],
                "regions": [{"region_role": "scene_region", "points": [[-1, -1], [6, -1], [6, 6], [-1, 6]]}],
                "derived_regions": [{"region_role": "building_region", "points": [[1, 1], [2, 1], [2, 2], [1, 2]]}],
                "functional_zones": [{"zone_type": "plaza", "points": [[2, 2], [3, 2], [3, 3], [2, 3]]}],
                "surface_annotations": [{"surface_role": "bike_lane", "points": [[0, 0], [1, 0], [1, 1], [0, 1]]}],
                "outputs": {"scene_glb": str(scene_glb)},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(viewer, "ROOT", repo_root)

    manifest = viewer.build_layout_manifest(layout_path)

    assert manifest["summary"]["length_m"] == 60
    assert manifest["visual_style"]["style"] == "test"
    assert manifest["scene_bounds"]["center"]
    assert manifest["instances"]["inst_tree"]["category"] == "tree"
    overlay = manifest["layout_overlay"]
    assert overlay["lane_count"] == 2
    assert overlay["generated_lots"] == [{"lot_id": "lot_1"}]
    assert overlay["bands"][0]["kind"] == "drive_lane"
    assert overlay["building_footprints"]
    assert overlay["building_regions"]
    assert overlay["regions"]
    assert overlay["derived_regions"]
    assert overlay["functional_zones"]
    assert overlay["surface_annotations"]
    metadata = manifest["comparison_metadata"]
    assert metadata["prompt"] == "test prompt"
    assert metadata["random_seed"] == 99
    assert metadata["density"] == 0.8
    assert metadata["road_width_m"] == 6.4
    assert metadata["lane_count"] == 2
    assert metadata["skeleton_design_profile"] == "sparse"
    assert metadata["street_furniture_profile"] == "balanced"
    assert metadata["curated_street_assets_profile"] == "curated_default"
    assert metadata["furniture_balance_policy"] == "street_focus"
    assert metadata["style_preset"] == "test_style"
    assert metadata["production_step_ids"] == ["road_base"]


def test_as_bbox_xz_supports_old_and_new_orders_with_position_hint():
    assert viewer._as_bbox_xz([0, 10, 2, 6], [5.0, 0.0, 4.0]) == [0.0, 10.0, 2.0, 6.0]
    assert viewer._as_bbox_xz((0, 10, 2, 6), (0.5, 0.0, 5.0)) == [0.0, 2.0, 6.0, 10.0]


def test_as_bbox_xz_prefers_smaller_footprint_when_position_missing():
    assert viewer._as_bbox_xz([0, 10, 2, 6]) == [0.0, 2.0, 6.0, 10.0]


def test_as_pair_and_instance_payloads_normalize_tuple_inputs_and_filter_empty_rules():
    payload = {
        "placements": [
            {
                "instance_id": "inst_01",
                "asset_id": "asset_tree",
                "category": "tree",
                "placement_group": "street_furniture",
                "position_xyz": (4, 0, 2),
                "bbox_xz": (3.5, 3.5, 1.0, 4.0),
                "anchor_target_xz": (7.0, 6.0),
                "violated_rules": ["distance_to_entry", "", None, 0, "spacing_ok"],
            }
        ]
    }
    instances = viewer._build_instance_payloads(payload)
    assert "inst_01" in instances
    assert instances["inst_01"]["bbox_xz"] == [3.5, 3.5, 1.0, 4.0]
    assert instances["inst_01"]["anchor_target_xz"] == [7.0, 6.0]
    assert instances["inst_01"]["violated_rules"] == ["distance_to_entry", "0", "spacing_ok"]


def test_build_scene_bounds_prefers_nearest_bbox_order_by_position():
    payload = {
        "placements": [
            {
                "instance_id": "inst_01",
                "position_xyz": [0.5, 0.0, 5.0],
                "bbox_xz": [0.0, 10.0, 2.0, 6.0],
            }
        ]
    }
    bounds = viewer._build_scene_bounds(payload)
    assert bounds["center"] == [1.0, 6.0, 8.0]
    assert bounds["size"] == [2.0, 12.0, 4.0]


def test_build_comparison_metadata_parses_semantic_profile_pair():
    payload = {
        "summary": {
            "semantic_profile_pair": "sparse+balanced",
            "curated_street_assets_profile": "curated_default",
            "furniture_balance_policy": "street_focus",
        },
        "config": {
            "query": "block scene test",
            "seed": 42,
            "density": 0.42,
            "road_width_m": 7.2,
            "lane_count": 2,
        },
        "production_steps": [],
    }

    metadata = viewer._build_comparison_metadata(payload, [])

    assert metadata["skeleton_design_profile"] == "sparse"
    assert metadata["street_furniture_profile"] == "balanced"
    assert metadata["curated_street_assets_profile"] == "curated_default"
    assert metadata["furniture_balance_policy"] == "street_focus"
