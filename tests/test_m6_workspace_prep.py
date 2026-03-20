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


def _labels_by_component_type(config: dict, component_type: str) -> list[str]:
    return [
        component.get("props", {}).get("label")
        for component in config["components"]
        if component.get("type") == component_type and component.get("props", {}).get("label")
    ]


def _props_by_label(config: dict) -> dict[str, dict]:
    return {
        component.get("props", {}).get("label"): component.get("props", {})
        for component in config["components"]
        if component.get("props", {}).get("label")
    }


def _typed_props_by_label(config: dict, component_type: str) -> dict[str, dict]:
    return {
        component.get("props", {}).get("label"): component.get("props", {})
        for component in config["components"]
        if component.get("type") == component_type and component.get("props", {}).get("label")
    }


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
    labels = _labels_by_component_type(config, "plot")

    assert "Theme / Building Zoning Preview" in labels


def test_build_demo_exposes_surrounding_building_mode_control():
    pytest.importorskip("gradio")

    demo = app.build_demo()
    config = demo.get_config_file()
    labels = _labels_by_component_type(config, "dropdown")

    assert "Surrounding Building Mode" in labels


def test_build_demo_exposes_asymmetry_and_setback_controls_with_defaults():
    pytest.importorskip("gradio")

    demo = app.build_demo()
    config = demo.get_config_file()
    slider_props = _typed_props_by_label(config, "slider")
    number_props = _typed_props_by_label(config, "number")

    assert slider_props["Land-Use Asymmetry Strength"]["value"] == 0.0
    assert slider_props["Land-Use Asymmetry Strength"]["minimum"] == 0.0
    assert slider_props["Land-Use Asymmetry Strength"]["maximum"] == 1.0
    assert slider_props["Left/Right Bias"]["value"] == 0.0
    assert slider_props["Left/Right Bias"]["minimum"] == -1.0
    assert slider_props["Left/Right Bias"]["maximum"] == 1.0
    assert slider_props["Streetwall Continuity"]["value"] == 0.95
    assert slider_props["Streetwall Continuity"]["minimum"] == 0.0
    assert slider_props["Streetwall Continuity"]["maximum"] == 1.0
    assert number_props["Front Setback Min (m)"]["value"] == 1.0
    assert number_props["Front Setback Max (m)"]["value"] == 2.0


def test_build_demo_exposes_zoning_and_infill_controls_with_defaults():
    pytest.importorskip("gradio")

    demo = app.build_demo()
    config = demo.get_config_file()
    dropdown_props = _typed_props_by_label(config, "dropdown")

    assert dropdown_props["Zoning Granularity"]["value"] == "fine"
    assert ("balanced", "balanced") in dropdown_props["Zoning Granularity"]["choices"]
    assert dropdown_props["Infill Policy"]["value"] == "aggressive"
    assert ("large_gap_only", "large_gap_only") in dropdown_props["Infill Policy"]["choices"]


def test_build_demo_exposes_parametric_asset_preview_controls():
    pytest.importorskip("gradio")

    demo = app.build_demo()
    config = demo.get_config_file()
    labels = [
        component.get("props", {}).get("label")
        for component in config["components"]
        if component.get("props", {}).get("label")
    ]

    assert "Parametric Preview (GLB)" in labels
    assert "Parametric Result JSON" in labels
    assert "Bench Width (m)" in labels
    assert "Lamp Pole Height (m)" in labels


def test_build_demo_defaults_device_to_auto_and_street_curation_to_scene_ready_first():
    pytest.importorskip("gradio")

    demo = app.build_demo()
    config = demo.get_config_file()
    by_label = _props_by_label(config)

    assert by_label["Device"]["value"] == "auto"
    assert ("auto", "auto") in by_label["Device"]["choices"]
    assert by_label["Asset Curation"]["value"] == "scene_ready_first"
    assert ("scene_ready_first", "scene_ready_first") in by_label["Asset Curation"]["choices"]
    assert "generator_type" in by_label["Street Instances"]["headers"]


def test_build_demo_exposes_production_timeline_controls():
    pytest.importorskip("gradio")

    demo = app.build_demo()
    config = demo.get_config_file()
    labels = _labels_by_component_type(config, "slider") + _labels_by_component_type(config, "model3d") + _labels_by_component_type(config, "image") + _labels_by_component_type(config, "file")

    assert "Production Step" in labels
    assert "Production Step Preview (GLB)" in labels
    assert "Production Companion View" in labels
    assert "Production Step Downloads" in labels


def test_build_demo_street_tab_defaults_to_minimal_surface():
    pytest.importorskip("gradio")

    demo = app.build_demo()
    config = demo.get_config_file()
    accordion_props = _typed_props_by_label(config, "accordion")
    textbox_labels = _labels_by_component_type(config, "textbox")
    button_values = [
        component.get("props", {}).get("value")
        for component in config["components"]
        if component.get("type") == "button"
    ]

    assert "Query" in textbox_labels
    assert "Run Street" in button_values
    assert accordion_props["Production Timeline"]["open"] is True
    assert accordion_props["高级设置"]["open"] is False
    assert accordion_props["更多结果"]["open"] is False
    assert accordion_props["Presentation Views"]["open"] is False
    assert accordion_props["Cross-Section Preview"]["open"] is False
    assert accordion_props["Solver Diagnostics"]["open"] is False
    assert accordion_props["Scene Graph"]["open"] is False
    assert accordion_props["POI Analysis"]["open"] is False


def test_build_demo_street_defaults_are_procedural_first():
    pytest.importorskip("gradio")

    demo = app.build_demo()
    config = demo.get_config_file()
    dropdown_props = _typed_props_by_label(config, "dropdown")
    plot_labels = _labels_by_component_type(config, "plot")
    code_labels = _labels_by_component_type(config, "code")

    assert dropdown_props["Layout Mode"]["value"] == "osm"
    assert dropdown_props["Program Generator"]["value"] == "heuristic_v1"
    assert dropdown_props["Policy"]["value"] == "rule"
    assert dropdown_props["Layout Solver"]["value"] == "hybrid_milp_v1"
    assert dropdown_props["Surrounding Building Mode"]["value"] == "grid_growth"
    assert dropdown_props["Objective Profile"]["value"] == "balanced"
    assert dropdown_props["Ped Demand"]["value"] == "medium"
    assert dropdown_props["Bike Demand"]["value"] == "low"
    assert dropdown_props["Transit Demand"]["value"] == "medium"
    assert dropdown_props["Vehicle Demand"]["value"] == "medium"
    assert dropdown_props["Tree Species Policy"]["value"] == "per_theme_single_species"
    assert dropdown_props["Furniture Balance Policy"]["value"] == "overall_balanced"
    assert dropdown_props["Placement Logging"]["value"] == "full_with_ui_summary"
    assert "Placement Decision Summary" in code_labels
    assert "Cross-Section Preview" in plot_labels
    assert "Cross-Section Summary" in code_labels
    assert "Solver Diagnostics" in plot_labels
    assert "Solver Diagnostics Summary" in code_labels


def test_production_step_helpers_select_stage_outputs(tmp_path: Path):
    stage0_glb = tmp_path / "00_road_base.glb"
    stage1_glb = tmp_path / "01_poi_context.glb"
    companion_png = tmp_path / "01_poi_context.png"
    layout_json = tmp_path / "scene_layout.json"
    stage0_glb.write_bytes(b"glb")
    stage1_glb.write_bytes(b"glb")
    companion_png.write_bytes(b"png")
    layout_json.write_text("{}", encoding="utf-8")

    payload = {
        "outputs": {
            "scene_glb": str(stage1_glb),
            "scene_layout": str(layout_json),
        },
        "production_steps": [
            {
                "step_id": "road_base",
                "index": 0,
                "title": "Road Base",
                "glb_path": str(stage0_glb),
                "companion_path": "",
                "visible_instance_ids": [],
                "delta_instance_ids": [],
                "counts": {"visible_instance_count": 0, "street_furniture_count": 0},
            },
            {
                "step_id": "poi_context",
                "index": 1,
                "title": "POI Context",
                "glb_path": str(stage1_glb),
                "companion_path": str(companion_png),
                "visible_instance_ids": ["building_01"],
                "delta_instance_ids": [],
                "counts": {"visible_instance_count": 1, "poi_point_count": 2},
            },
        ],
    }

    steps, slider_update, summary, model_path, companion_path, files, prev_btn, next_btn = app._load_production_steps(
        json.dumps(payload, ensure_ascii=True)
    )
    assert len(steps) == 2
    assert slider_update["maximum"] == 1
    assert slider_update["value"] == 0
    assert "Road Base" in summary
    assert model_path == str(stage0_glb)
    assert companion_path is None
    assert str(stage0_glb) in files

    slider_label, selected_summary, selected_model, selected_companion, selected_files = app._select_production_step(steps, 1)
    assert "POI Context" in selected_summary
    assert selected_model == str(stage1_glb)
    assert selected_companion == str(companion_png)
    assert str(stage1_glb) in selected_files
    assert str(companion_png) in selected_files


def test_parametric_identity_uses_parameter_hash_for_auto_asset_id():
    base_meta = {
        "asset_kind": "lamp",
        "runtime_profile": "preview",
        "material_family": "metal",
        "style_tags": ["modern"],
        "parameter_snapshot": {
            "pole_height_m": 5.0,
            "arm_length_m": 0.8,
            "detail_level": 1,
        },
    }

    asset_id_a, _text_desc_a = app._parametric_identity(base_meta, "", "")
    asset_id_b, _text_desc_b = app._parametric_identity(
        {
            **base_meta,
            "parameter_snapshot": {
                "pole_height_m": 5.0,
                "arm_length_m": 1.2,
                "detail_level": 1,
            },
        },
        "",
        "",
    )
    manual_asset_id, _manual_text_desc = app._parametric_identity(base_meta, "custom_lamp_asset", "")

    assert asset_id_a.startswith("lamp_modern_metal_preview_")
    assert len(asset_id_a.rsplit("_", 1)[-1]) == 8
    assert asset_id_a != asset_id_b
    assert manual_asset_id == "custom_lamp_asset"


def test_preview_parametric_asset_generates_files_and_state(tmp_path: Path):
    pytest.importorskip("trimesh")

    status, result_json, model_path, files, preview_state = app.preview_parametric_asset(
        asset_kind="bench",
        runtime_profile="production",
        device_backend="auto",
        preview_out_dir_text=str(tmp_path / "preview"),
        asset_id_text="bench_preview",
        text_desc_text="preview bench",
        bench_width_m=1.8,
        bench_depth_m=0.55,
        bench_seat_height_m=0.45,
        bench_backrest_height_m=0.35,
        bench_backrest_angle_deg=12.0,
        bench_leg_type="dual_frame",
        bench_armrest_enabled=False,
        bench_slat_count=5,
        bench_material_family="metal_wood",
        bench_style_tag="modern",
        bench_detail_level=2,
        lamp_pole_height_m=5.0,
        lamp_pole_radius_m=0.06,
        lamp_base_diameter_m=0.35,
        lamp_arm_length_m=0.8,
        lamp_luminaire_type="flat_led",
        lamp_single_or_double_arm="single",
        lamp_light_direction="roadside",
        lamp_material_family="metal",
        lamp_style_tag="modern",
        lamp_detail_level=2,
    )

    assert "Parametric preview ready" in status
    payload = json.loads(result_json)
    assert payload["asset_id"] == "bench_preview"
    assert payload["quality_metrics"]["meets_min_faces"] is True
    assert model_path and Path(model_path).exists()
    assert len(files) == 3
    assert preview_state is not None
    assert preview_state["asset_id"] == "bench_preview"
    assert Path(preview_state["result_json_path"]).exists()
    assert Path(preview_state["latent_path"]).exists()


def test_append_parametric_asset_to_manifest_refreshes_library(tmp_path: Path):
    pytest.importorskip("trimesh")

    manifest = tmp_path / "real_assets_manifest.jsonl"
    latent_path = tmp_path / "latents" / "bench_01.pt"
    latent_path.parent.mkdir(parents=True, exist_ok=True)
    latent_path.write_bytes(b"latent")
    _write_real_manifest(manifest, latent_path)

    _status, _result_json, _model_path, _files, preview_state = app.preview_parametric_asset(
        asset_kind="lamp",
        runtime_profile="preview",
        device_backend="auto",
        preview_out_dir_text=str(tmp_path / "preview"),
        asset_id_text="lamp_preview",
        text_desc_text="",
        bench_width_m=1.8,
        bench_depth_m=0.55,
        bench_seat_height_m=0.45,
        bench_backrest_height_m=0.35,
        bench_backrest_angle_deg=12.0,
        bench_leg_type="dual_frame",
        bench_armrest_enabled=False,
        bench_slat_count=5,
        bench_material_family="metal_wood",
        bench_style_tag="modern",
        bench_detail_level=2,
        lamp_pole_height_m=5.0,
        lamp_pole_radius_m=0.06,
        lamp_base_diameter_m=0.35,
        lamp_arm_length_m=0.8,
        lamp_luminaire_type="flat_led",
        lamp_single_or_double_arm="single",
        lamp_light_direction="roadside",
        lamp_material_family="metal",
        lamp_style_tag="modern",
        lamp_detail_level=1,
    )

    status, table, stats_json = app.append_parametric_asset_to_manifest(preview_state, str(manifest))
    stats = json.loads(stats_json)

    assert "Parametric asset appended to manifest" in status
    assert any(row[0] == "lamp_preview" for row in table)
    assert stats["asset_count"] == 2


def test_append_parametric_asset_rejects_duplicate_asset_id(tmp_path: Path):
    pytest.importorskip("trimesh")

    manifest = tmp_path / "real_assets_manifest.jsonl"
    latent_path = tmp_path / "latents" / "bench_01.pt"
    latent_path.parent.mkdir(parents=True, exist_ok=True)
    latent_path.write_bytes(b"latent")
    _write_real_manifest(manifest, latent_path)

    preview_state = {
        "asset_id": "bench_01",
        "manifest_row": {
            "asset_id": "bench_01",
            "category": "bench",
            "text_desc": "duplicate bench",
            "mesh_path": str(tmp_path / "bench_01.glb"),
            "latent_path": str(tmp_path / "bench_01.pt"),
        },
    }

    status, _table, _stats_json = app.append_parametric_asset_to_manifest(preview_state, str(manifest))
    assert "already exists" in status


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
                "land_use_type": "commercial",
                "buildable": True,
                "lot_id": "lot_000",
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
                "land_use_type": "",
                "buildable": False,
                "lot_id": "",
                "theme_id": "theme_000",
                "theme_name": "commercial",
                "segment_ids": ["seg_0000"],
                "footprint_ids": [],
            },
        ],
        "generated_lots": [
            {
                "lot_id": "lot_000",
                "polygon_xz": [[-4.0, 3.0], [4.0, 3.0], [4.0, 7.0], [-4.0, 7.0], [-4.0, 3.0]],
                "center_xz": [0.0, 5.0],
                "side": "left",
                "land_use_type": "commercial",
                "theme_id": "theme_000",
                "frontage_width_m": 8.0,
                "depth_m": 4.0,
                "height_class": "midrise",
                "source": "grid_growth",
            }
        ],
        "building_placements": [
            {
                "anchor_geom_id": "lot_000",
            }
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
