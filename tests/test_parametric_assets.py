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

from roadgen3d.parametric_assets import generate_parametric_asset
from roadgen3d.street_layout import _load_real_manifest
from scripts import street_generate_parametric_asset as cli


def test_bench_params_are_clamped_and_warned():
    pytest.importorskip("trimesh")
    result = generate_parametric_asset(
        {
            "asset_kind": "bench",
            "runtime_profile": "preview",
            "params": {
                "width_m": 10.0,
                "depth_m": 0.1,
                "seat_height_m": 0.2,
                "style_tag": "unknown_style",
            },
        }
    )
    snapshot = result.parameter_snapshot
    assert snapshot["width_m"] == pytest.approx(2.40)
    assert snapshot["depth_m"] == pytest.approx(0.40)
    assert snapshot["seat_height_m"] == pytest.approx(0.38)
    assert snapshot["style_tag"] == "modern"
    assert any("clamped" in item for item in result.warnings)
    assert any("Unknown style_tag" in item for item in result.warnings)


def test_bench_parameters_change_geometry_and_metrics():
    pytest.importorskip("trimesh")
    base = generate_parametric_asset({"asset_kind": "bench", "runtime_profile": "production", "params": {}})
    variant = generate_parametric_asset(
        {
            "asset_kind": "bench",
            "runtime_profile": "production",
            "params": {
                "seat_height_m": 0.50,
                "slat_count": 8,
                "leg_type": "pedestal",
                "armrest_enabled": True,
            },
        }
    )
    assert variant.bbox_size_xyz[1] > base.bbox_size_xyz[1]
    assert variant.quality_metrics.face_count != base.quality_metrics.face_count
    assert base.quality_metrics.support_count == 4
    assert variant.quality_metrics.support_count == 1


def test_lamp_preview_and_production_use_different_detail_presets():
    pytest.importorskip("trimesh")
    preview = generate_parametric_asset({"asset_kind": "lamp", "runtime_profile": "preview", "params": {}})
    production = generate_parametric_asset({"asset_kind": "lamp", "runtime_profile": "production", "params": {}})
    assert preview.parameter_snapshot["effective_detail_level"] == 1
    assert production.parameter_snapshot["effective_detail_level"] >= 2
    assert production.quality_metrics.face_count >= preview.quality_metrics.face_count


def test_lamp_parameters_change_geometry_and_quality():
    pytest.importorskip("trimesh")
    base = generate_parametric_asset({"asset_kind": "lamp", "runtime_profile": "production", "params": {}})
    variant = generate_parametric_asset(
        {
            "asset_kind": "lamp",
            "runtime_profile": "production",
            "params": {
                "arm_length_m": 1.5,
                "luminaire_type": "globe",
                "single_or_double_arm": "double",
            },
        }
    )
    assert variant.bbox_size_xyz[0] > base.bbox_size_xyz[0]
    assert variant.quality_metrics.face_count != base.quality_metrics.face_count
    assert variant.quality_metrics.clearance_ok is True


def test_cli_writes_outputs_and_manifest(tmp_path: Path):
    pytest.importorskip("trimesh")
    request_path = tmp_path / "request.json"
    request_payload = {
        "asset_id": "bench_cli",
        "text_desc": "bench from cli",
        "asset_kind": "bench",
        "runtime_profile": "production",
        "device_backend": "auto",
        "params": {
            "material_family": "metal_wood",
            "style_tag": "modern",
        },
    }
    request_path.write_text(json.dumps(request_payload, indent=2), encoding="utf-8")
    out_dir = tmp_path / "out"
    manifest_path = tmp_path / "manifest.jsonl"

    assert cli.main(["--request-json", str(request_path), "--out-dir", str(out_dir), "--manifest-out", str(manifest_path)]) == 0

    mesh_path = out_dir / "bench_cli.glb"
    result_path = out_dir / "bench_cli.result.json"
    latent_path = out_dir / "bench_cli.pt"
    assert mesh_path.exists()
    assert result_path.exists()
    assert latent_path.exists()

    result_payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert result_payload["asset_id"] == "bench_cli"
    assert result_payload["parameter_snapshot"]["effective_detail_level"] >= 2
    assert result_payload["quality_metrics"]["meets_min_faces"] is True

    rows = _load_real_manifest(manifest_path)
    assert len(rows) == 1
    assert rows[0]["asset_id"] == "bench_cli"
    assert rows[0]["generator_type"] == "parametric_v1"
    assert rows[0]["runtime_profile"] == "production"
    assert isinstance(rows[0]["parameter_snapshot"], dict)
    assert isinstance(rows[0]["quality_metrics"], dict)


# ---------------------------------------------------------------------------
# Building parametric generation tests
# ---------------------------------------------------------------------------


def test_building_rect_basic():
    pytest.importorskip("trimesh")
    result = generate_parametric_asset(
        {
            "asset_kind": "building",
            "runtime_profile": "preview",
            "params": {
                "frontage_width_m": 14.0,
                "depth_m": 10.0,
                "height_class": "midrise",
                "theme_name": "commercial",
            },
        }
    )
    assert result.asset_kind == "building"
    assert result.quality_metrics.ground_contact_ok is True
    assert result.quality_metrics.meets_min_faces is True
    assert result.quality_metrics.within_poly_budget is True
    assert result.bbox_size_xyz[0] > 10.0  # width
    assert result.bbox_size_xyz[1] > 5.0   # height
    assert result.bbox_size_xyz[2] > 5.0   # depth


def test_building_height_class_auto():
    pytest.importorskip("trimesh")
    lowrise = generate_parametric_asset(
        {
            "asset_kind": "building",
            "runtime_profile": "preview",
            "params": {"height_class": "lowrise", "frontage_width_m": 12.0},
        }
    )
    highrise = generate_parametric_asset(
        {
            "asset_kind": "building",
            "runtime_profile": "preview",
            "params": {"height_class": "highrise", "frontage_width_m": 12.0},
        }
    )
    assert highrise.bbox_size_xyz[1] > lowrise.bbox_size_xyz[1]


def test_building_explicit_height():
    pytest.importorskip("trimesh")
    result = generate_parametric_asset(
        {
            "asset_kind": "building",
            "runtime_profile": "preview",
            "params": {"height_m": 30.0, "frontage_width_m": 15.0},
        }
    )
    # Height should be close to 30m (with parapet, roof slab)
    assert result.bbox_size_xyz[1] > 28.0
    assert result.bbox_size_xyz[1] < 35.0


def test_building_l_shape():
    pytest.importorskip("trimesh")
    rect = generate_parametric_asset(
        {
            "asset_kind": "building",
            "runtime_profile": "preview",
            "params": {
                "frontage_width_m": 16.0,
                "depth_m": 12.0,
                "footprint_shape": "rect",
            },
        }
    )
    l_shape = generate_parametric_asset(
        {
            "asset_kind": "building",
            "runtime_profile": "preview",
            "params": {
                "frontage_width_m": 16.0,
                "depth_m": 12.0,
                "footprint_shape": "L",
            },
        }
    )
    assert l_shape.quality_metrics.ground_contact_ok is True
    assert l_shape.quality_metrics.meets_min_faces is True
    # L-shape should be wider than rect due to side wing
    assert l_shape.bbox_size_xyz[0] > rect.bbox_size_xyz[0]
    assert l_shape.parameter_snapshot["footprint_shape"] == "L"


def test_building_u_shape():
    pytest.importorskip("trimesh")
    u_shape = generate_parametric_asset(
        {
            "asset_kind": "building",
            "runtime_profile": "preview",
            "params": {
                "frontage_width_m": 20.0,
                "depth_m": 14.0,
                "footprint_shape": "U",
            },
        }
    )
    assert u_shape.quality_metrics.ground_contact_ok is True
    assert u_shape.quality_metrics.meets_min_faces is True
    assert u_shape.parameter_snapshot["footprint_shape"] == "U"


def test_building_detail_levels():
    pytest.importorskip("trimesh")
    level0 = generate_parametric_asset(
        {
            "asset_kind": "building",
            "runtime_profile": "preview",
            "params": {"detail_level": 0},
        }
    )
    level2 = generate_parametric_asset(
        {
            "asset_kind": "building",
            "runtime_profile": "production",
            "params": {"detail_level": 2},
        }
    )
    # Higher detail level should produce more faces
    assert level2.quality_metrics.face_count > level0.quality_metrics.face_count


def test_building_params_clamped():
    pytest.importorskip("trimesh")
    result = generate_parametric_asset(
        {
            "asset_kind": "building",
            "runtime_profile": "preview",
            "params": {
                "frontage_width_m": 200.0,  # way above max
                "depth_m": 1.0,             # way below min
                "floor_height_m": 1.0,      # below min
            },
        }
    )
    snapshot = result.parameter_snapshot
    assert snapshot["frontage_width_m"] == pytest.approx(60.0)
    assert snapshot["depth_m"] == pytest.approx(6.0)
    assert snapshot["floor_height_m"] == pytest.approx(2.8)
    assert any("clamped" in item for item in result.warnings)


def test_building_window_count_reasonable():
    pytest.importorskip("trimesh")
    result = generate_parametric_asset(
        {
            "asset_kind": "building",
            "runtime_profile": "production",
            "params": {
                "frontage_width_m": 20.0,
                "depth_m": 12.0,
                "height_m": 20.0,
                "detail_level": 2,
            },
        }
    )
    # A 20m wide, 20m tall building should have multiple windows
    assert result.quality_metrics.face_count > 200


def test_building_themes_produce_different_colors():
    pytest.importorskip("trimesh")
    import numpy as np

    residential = generate_parametric_asset(
        {
            "asset_kind": "building",
            "runtime_profile": "preview",
            "params": {"theme_name": "residential", "detail_level": 0},
        }
    )
    commercial = generate_parametric_asset(
        {
            "asset_kind": "building",
            "runtime_profile": "preview",
            "params": {"theme_name": "commercial", "detail_level": 0},
        }
    )
    # Different themes should produce different face colors
    res_colors = np.array(residential.mesh.visual.face_colors)
    com_colors = np.array(commercial.mesh.visual.face_colors)
    assert not np.array_equal(res_colors[0], com_colors[0])


# ---------------------------------------------------------------------------
# Tree tests
# ---------------------------------------------------------------------------


def test_tree_sphere_basic():
    pytest.importorskip("trimesh")
    result = generate_parametric_asset(
        {
            "asset_kind": "tree",
            "runtime_profile": "preview",
            "params": {"canopy_style": "sphere"},
        }
    )
    assert result.asset_kind == "tree"
    assert result.quality_metrics.ground_contact_ok
    assert result.quality_metrics.meets_min_faces
    # Tree should be taller than wide
    w, h, d = result.bbox_size_xyz
    assert h > max(w, d)


def test_tree_all_canopy_styles():
    pytest.importorskip("trimesh")
    for style in ("sphere", "cone", "oval", "flat_disc", "multi_blob"):
        result = generate_parametric_asset(
            {
                "asset_kind": "tree",
                "runtime_profile": "preview",
                "params": {"canopy_style": style},
            }
        )
        assert result.asset_kind == "tree"
        assert result.quality_metrics.ground_contact_ok
        assert result.quality_metrics.meets_min_faces


def test_tree_detail_levels():
    pytest.importorskip("trimesh")
    results = []
    for dl in range(4):
        result = generate_parametric_asset(
            {
                "asset_kind": "tree",
                "runtime_profile": "production",
                "params": {"detail_level": dl},
            }
        )
        results.append(result)
        assert result.quality_metrics.meets_min_faces
        assert result.quality_metrics.ground_contact_ok
    # Higher detail should produce more faces
    assert results[-1].quality_metrics.face_count >= results[0].quality_metrics.face_count


def test_tree_params_clamped():
    pytest.importorskip("trimesh")
    result = generate_parametric_asset(
        {
            "asset_kind": "tree",
            "runtime_profile": "preview",
            "params": {
                "trunk_height_m": 100.0,
                "trunk_radius_m": 0.01,
                "canopy_radius_m": 0.1,
            },
        }
    )
    snapshot = result.parameter_snapshot
    assert snapshot["trunk_height_m"] == pytest.approx(8.0)
    assert snapshot["trunk_radius_m"] == pytest.approx(0.06)
    assert snapshot["canopy_radius_m"] == pytest.approx(0.50)
    assert any("clamped" in w for w in result.warnings)


def test_tree_canopy_colors():
    pytest.importorskip("trimesh")
    import numpy as np

    green = generate_parametric_asset(
        {
            "asset_kind": "tree",
            "runtime_profile": "preview",
            "params": {"canopy_color_name": "deciduous_green"},
        }
    )
    autumn = generate_parametric_asset(
        {
            "asset_kind": "tree",
            "runtime_profile": "preview",
            "params": {"canopy_color_name": "autumn_orange"},
        }
    )
    # result.mesh is now a Scene; compare PBR materials of the canopy geometry
    import trimesh as _tm

    def _canopy_base_color(result):
        scene = result.mesh
        assert isinstance(scene, _tm.Scene)
        for name, geom in scene.geometry.items():
            if "canopy" in name:
                return tuple(geom.visual.material.baseColorFactor)
        raise AssertionError("No canopy geometry found")

    g_color = _canopy_base_color(green)
    a_color = _canopy_base_color(autumn)
    assert g_color != a_color


def test_tree_unknown_canopy_style_falls_back():
    pytest.importorskip("trimesh")
    result = generate_parametric_asset(
        {
            "asset_kind": "tree",
            "runtime_profile": "preview",
            "params": {"canopy_style": "nonexistent_style"},
        }
    )
    assert result.parameter_snapshot["canopy_style"] == "sphere"
    assert any("canopy_style" in w for w in result.warnings)
