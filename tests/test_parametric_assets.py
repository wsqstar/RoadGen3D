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
from scripts import m3_03_generate_parametric_asset as cli


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
