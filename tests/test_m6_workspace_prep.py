from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

pytest.importorskip("gradio")
import scripts.m1_gradio_app as app


def _write_real_manifest(path: Path, latent_path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "asset_id": "bench_01",
                "text_desc": "a street bench",
                "latent_path": str(latent_path),
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_inspect_workspace_readiness_reports_ready_when_manifest_index_and_cache_exist(tmp_path: Path):
    manifest = tmp_path / "real_assets_manifest.jsonl"
    latent_path = tmp_path / "latents" / "bench_01.pt"
    latent_path.parent.mkdir(parents=True, exist_ok=True)
    latent_path.write_bytes(b"latent")
    _write_real_manifest(manifest, latent_path)

    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "index_ip.faiss").write_bytes(b"faiss")
    (artifacts_dir / "id_map.json").write_text("[]", encoding="utf-8")

    bbox = (121.45, 31.20, 121.46, 31.21)
    cache_dir = tmp_path / "osm_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"overpass_{app._bbox_hash(bbox)}.json").write_text("{}", encoding="utf-8")

    readiness = app.inspect_workspace_readiness(
        dataset_profile="real",
        data_dir_text=str(tmp_path / "unused"),
        artifacts_dir_text=str(artifacts_dir),
        real_manifest_text=str(manifest),
        model_dir_text="",
        real_latents_dir_text=str(tmp_path / "latents"),
        layout_mode="osm",
        aoi_bbox=bbox,
        osm_cache_dir_text=str(cache_dir),
    )

    assert readiness.manifest_ok is True
    assert readiness.latents_ok is True
    assert readiness.index_ok is True
    assert readiness.osm_cache_ok is True
    assert readiness.missing_items == ()


def test_prepare_workspace_skips_existing_latents_and_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manifest = tmp_path / "real_assets_manifest.jsonl"
    latent_path = tmp_path / "latents" / "bench_01.pt"
    latent_path.parent.mkdir(parents=True, exist_ok=True)
    latent_path.write_bytes(b"latent")
    _write_real_manifest(manifest, latent_path)

    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "index_ip.faiss").write_bytes(b"faiss")
    (artifacts_dir / "id_map.json").write_text("[]", encoding="utf-8")

    monkeypatch.setattr(app, "encode_real_latents", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("encode should be skipped")))
    monkeypatch.setattr(app, "prepare_assets_and_index", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("index should be skipped")))

    result = app.prepare_workspace(
        dataset_profile="real",
        data_dir_text=str(tmp_path / "unused"),
        artifacts_dir_text=str(artifacts_dir),
        real_manifest_text=str(manifest),
        real_mesh_root_text=str(tmp_path / "meshes"),
        real_latents_dir_text=str(tmp_path / "latents"),
        num_assets=8,
        seed=42,
        latent_dim=256,
        model_name="openai/clip-vit-base-patch32",
        model_dir_text="",
        local_files_only=True,
        device="cpu",
        shapee_model_dir_text="",
        render_cache_dir_text=str(tmp_path / "render_cache"),
        encode_mode="mesh_ref",
        shapee_local_only=True,
        layout_mode="template",
    )

    steps = {step.step: step for step in result.steps}
    assert steps["prepare_latents_if_needed"].status == "skipped"
    assert steps["prepare_index_if_needed"].status == "skipped"
    assert result.readiness.index_ok is True
    assert result.readiness.latents_ok is True


def test_prepare_workspace_prefetches_osm_cache_for_bbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manifest = tmp_path / "real_assets_manifest.jsonl"
    latent_path = tmp_path / "latents" / "bench_01.pt"
    latent_path.parent.mkdir(parents=True, exist_ok=True)
    latent_path.write_bytes(b"latent")
    _write_real_manifest(manifest, latent_path)

    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "index_ip.faiss").write_bytes(b"faiss")
    (artifacts_dir / "id_map.json").write_text("[]", encoding="utf-8")

    bbox = (121.45, 31.20, 121.46, 31.21)
    cache_dir = tmp_path / "osm_cache"

    def _fake_fetch(*, bbox, cache_dir, force_refetch=False):
        path = Path(cache_dir)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"overpass_{app._bbox_hash(tuple(bbox))}.json").write_text("{}", encoding="utf-8")
        return {"elements": []}

    monkeypatch.setattr(app, "fetch_osm_data", _fake_fetch)
    monkeypatch.setattr(app, "discover_poi_roads", lambda city, cache_dir: [])

    result = app.prepare_workspace(
        dataset_profile="real",
        data_dir_text=str(tmp_path / "unused"),
        artifacts_dir_text=str(artifacts_dir),
        real_manifest_text=str(manifest),
        real_mesh_root_text=str(tmp_path / "meshes"),
        real_latents_dir_text=str(tmp_path / "latents"),
        num_assets=8,
        seed=42,
        latent_dim=256,
        model_name="openai/clip-vit-base-patch32",
        model_dir_text="",
        local_files_only=True,
        device="cpu",
        shapee_model_dir_text="",
        render_cache_dir_text=str(tmp_path / "render_cache"),
        encode_mode="mesh_ref",
        shapee_local_only=True,
        layout_mode="osm",
        osm_cache_dir_text=str(cache_dir),
        aoi_bbox=bbox,
    )

    assert result.readiness.osm_cache_ok is True
    assert any(step.step == "prepare_osm_cache_if_needed" and step.status == "completed" for step in result.steps)


def test_build_demo_uses_three_top_level_tabs():
    pytest.importorskip("gradio")

    demo = app.build_demo()
    config = demo.get_config_file()
    labels = [component["props"]["label"] for component in config["components"] if component.get("type") == "tabitem"]

    assert labels == ["1) 准备", "2) 生成街道", "3) 研究与训练"]


def test_extract_program_summary_includes_poi_counts():
    layout_payload = {
        "street_program": {
            "road_type": "transit_corridor",
            "cross_section_type": "balanced_complete_street",
            "lane_count": 2,
            "bands": [{"name": "carriageway", "width_m": 8.0}],
            "furniture_requirements": {"bench": 3},
            "control_points": ["entry", "transit_stop", "exit"],
            "design_goals": ["transit_access"],
        },
        "summary": {
            "spatial_context": {
                "entrance_points_xz": [[1.0, 2.0], [3.0, 4.0]],
                "bus_stop_points_xz": [[5.0, 6.0]],
                "fire_points_xz": [],
            },
            "poi_exclusion_zones": [{"poi_type": "entrance"}],
        },
    }

    result = json.loads(app._extract_program_summary(json.dumps(layout_payload)))

    assert result["control_points"] == ["entry", "transit_stop", "exit"]
    assert result["poi_counts"] == {"entrance": 2, "bus_stop": 1}
    assert result["total_poi_points"] == 3
    assert result["exclusion_zone_count"] == 1
