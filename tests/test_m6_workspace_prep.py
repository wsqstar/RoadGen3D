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


def _write_real_manifest(path: Path, latent_path: Path, *, include_building: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "asset_id": "bench_01",
            "text_desc": "a street bench",
            "latent_path": str(latent_path),
        }
    ]
    if include_building:
        rows.append(
            {
                "asset_id": "building_01",
                "category": "building",
                "asset_role": "building",
                "theme_tags": ["commercial", "transit"],
                "frontage_width_m": 16.0,
                "depth_m": 12.0,
                "height_class": "midrise",
                "text_desc": "a mixed-use building",
                "latent_path": str(latent_path),
                "source": "test",
            }
        )
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=True) for row in rows) + "\n",
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


def test_browse_asset_library_reports_building_roles_and_theme_counts(tmp_path: Path):
    manifest = tmp_path / "real_assets_manifest.jsonl"
    latent_path = tmp_path / "latents" / "bench_01.pt"
    latent_path.parent.mkdir(parents=True, exist_ok=True)
    latent_path.write_bytes(b"latent")
    _write_real_manifest(manifest, latent_path, include_building=True)

    table, stats_json = app.browse_asset_library(str(manifest), "building")
    stats = json.loads(stats_json)

    assert len(table) == 1
    assert table[0][0] == "building_01"
    assert table[0][2] == "building"
    assert stats["building_asset_count"] == 1
    assert stats["role_counts"]["building"] == 1
    assert stats["theme_counts"]["commercial"] == 1
    assert stats["theme_counts"]["transit"] == 1


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


def test_build_demo_exposes_zoning_preview_plot():
    pytest.importorskip("gradio")

    demo = app.build_demo()
    config = demo.get_config_file()
    labels = [
        component.get("props", {}).get("label")
        for component in config["components"]
        if component.get("props", {}).get("label")
    ]

    assert "Theme / Building Zoning Preview" in labels


def test_render_zoning_preview_returns_figure():
    pytest.importorskip("matplotlib")

    layout_payload = {
        "summary": {
            "osm_geometry": {
                "carriageway_rings": [[[-4.0, -1.0], [4.0, -1.0], [4.0, 1.0], [-4.0, 1.0], [-4.0, -1.0]]],
            }
        },
        "zoning_grid": [
            {
                "cell_id": "zone_000_left_building_buffer",
                "polygon_xz": [[-4.0, 3.0], [4.0, 3.0], [4.0, 7.0], [-4.0, 7.0], [-4.0, 3.0]],
                "center_xz": [0.0, 5.0],
                "lane_role": "left_building_buffer",
                "theme_id": "theme_000",
                "theme_name": "commercial",
                "segment_ids": ["seg_0000"],
                "footprint_ids": ["building_000"],
            },
            {
                "cell_id": "zone_000_carriageway",
                "polygon_xz": [[-4.0, -1.0], [4.0, -1.0], [4.0, 1.0], [-4.0, 1.0], [-4.0, -1.0]],
                "center_xz": [0.0, 0.0],
                "lane_role": "carriageway",
                "theme_id": "theme_000",
                "theme_name": "commercial",
                "segment_ids": ["seg_0000"],
                "footprint_ids": [],
            },
        ],
        "building_footprints": [
            {
                "footprint_id": "building_000",
                "source": "osm",
                "polygon_xz": [[-2.0, 4.0], [2.0, 4.0], [2.0, 6.0], [-2.0, 6.0], [-2.0, 4.0]],
            }
        ],
    }

    fig = app._render_zoning_preview(json.dumps(layout_payload))

    assert fig is not None
    assert fig.axes[0].get_title() == "Theme / Building Zoning Preview"


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
            "left_clear_path_width_m": 2.8,
            "right_clear_path_width_m": 2.4,
            "left_furnishing_width_m": 1.4,
            "right_furnishing_width_m": 1.0,
            "row_width_m": 15.6,
            "width_expanded": True,
            "width_reallocation_reason": "expanded total row width by 1.2m",
            "poi_fit_feasible": True,
            "poi_fit_report": {"candidate_poi_count": 3},
        },
        "summary": {
            "spatial_context": {
                "entrance_points_xz": [[1.0, 2.0], [3.0, 4.0]],
                "bus_stop_points_xz": [[5.0, 6.0]],
                "fire_points_xz": [],
            },
            "poi_exclusion_zones": [{"poi_type": "entrance"}],
            "selected_road_required_left_width_m": 4.2,
            "selected_road_required_right_width_m": 3.4,
            "selected_road_final_row_width_m": 15.6,
        },
    }

    result = json.loads(app._extract_program_summary(json.dumps(layout_payload)))

    assert result["control_points"] == ["entry", "transit_stop", "exit"]
    assert result["poi_counts"] == {"entrance": 2, "bus_stop": 1}
    assert result["total_poi_points"] == 3
    assert result["exclusion_zone_count"] == 1
    assert result["poi_fit_feasible"] is True
    assert result["selected_road_required_left_width_m"] == 4.2
    assert result["row_width_m"] == 15.6
    assert result["width_expanded"] is True


def test_extract_presentation_views_returns_gallery_and_report(tmp_path: Path):
    overview_path = (tmp_path / "overview_top.png").resolve()
    overview_path.write_bytes(b"png")
    layout_payload = {
        "summary": {
            "style_preset": "civic_clean_v1",
            "beauty_mode": "presentation_v1",
            "render_preset": "jury_default_v1",
            "presentation_score": 0.82,
            "style_coherence": 0.78,
            "visual_clutter": 0.16,
            "spacing_rhythm": 0.71,
            "focal_readability": 0.84,
            "composition_report": {"trimmed_optional_slots": 3},
            "render_views": [
                {
                    "name": "overview_top",
                    "title": "Overview Top",
                    "path": str(overview_path),
                }
            ],
        }
    }

    gallery, report_json = app._extract_presentation_views(json.dumps(layout_payload))

    assert gallery == [(str(overview_path), "Overview Top")]
    report = json.loads(report_json)
    assert report["style_preset"] == "civic_clean_v1"
    assert report["presentation_score"] == 0.82
