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

from scripts import m3_02_generate_procedural_assets as m3_assets


def _spec(
    asset_id: str,
    category: str,
    *,
    style: str = "modern",
    budget_k: int | None = None,
) -> m3_assets.AssetSpec:
    default_budget_k = {
        "bench": 15,
        "lamp": 20,
        "trash": 12,
        "tree": 25,
        "bus_stop": 30,
        "mailbox": 10,
        "hydrant": 12,
        "bollard": 8,
    }
    return m3_assets.AssetSpec(
        task_id=f"task_{asset_id}",
        category=category,
        asset_id=asset_id,
        style_tag=style,
        text_desc=f"a {style} {category}",
        target_h=5.0 if category == "lamp" else 2.0 if category in {"tree", "bus_stop"} else 1.0,
        target_w=1.5 if category in {"bus_stop", "bench"} else 0.4 if category == "lamp" else 0.8,
        target_d=1.2 if category in {"bus_stop", "bench"} else 0.4 if category == "lamp" else 0.8,
        poly_budget_k=budget_k if budget_k is not None else default_budget_k[category],
        license="cc-by",
        source="test",
    )


def _read_manifest_rows(path: Path) -> list[dict[str, object]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def test_min_face_thresholds_enforced(tmp_path: Path):
    trimesh = pytest.importorskip("trimesh")
    specs = [
        _spec("bench_q", "bench"),
        _spec("lamp_q", "lamp"),
        _spec("trash_q", "trash"),
        _spec("tree_q", "tree"),
        _spec("bus_stop_q", "bus_stop"),
        _spec("mailbox_q", "mailbox"),
        _spec("hydrant_q", "hydrant"),
        _spec("bollard_q", "bollard"),
    ]

    mesh_out = tmp_path / "meshes"
    manifest_out = tmp_path / "real_assets_manifest.jsonl"
    m3_assets.generate_all(
        specs=specs,
        mesh_out_dir=mesh_out,
        manifest_out=manifest_out,
        project_root=tmp_path,
        seed=42,
    )

    for row in _read_manifest_rows(manifest_out):
        category = str(row["category"])
        mesh_path = Path(str(row["mesh_path"]))
        mesh_or_scene = trimesh.load(mesh_path, force="scene")
        if isinstance(mesh_or_scene, trimesh.Scene):
            mesh = trimesh.util.concatenate(tuple(mesh_or_scene.geometry.values()))
        else:
            mesh = mesh_or_scene
        assert len(mesh.faces) >= m3_assets.MIN_FACES_BY_CATEGORY[category]


def test_generation_fails_when_threshold_unreachable(tmp_path: Path, monkeypatch):
    specs = [_spec("bench_fail", "bench", budget_k=1)]
    monkeypatch.setitem(m3_assets.MIN_FACES_BY_CATEGORY, "bench", 50000)

    with pytest.raises(RuntimeError, match="Failed to satisfy face constraints"):
        m3_assets.generate_all(
            specs=specs,
            mesh_out_dir=tmp_path / "meshes",
            manifest_out=tmp_path / "real_assets_manifest.jsonl",
            project_root=tmp_path,
            seed=1,
        )


def test_tree_and_lamp_not_lowpoly_baseline(tmp_path: Path):
    trimesh = pytest.importorskip("trimesh")
    specs = [
        _spec("tree_hi", "tree"),
        _spec("lamp_hi", "lamp"),
    ]
    mesh_out = tmp_path / "meshes"
    manifest_out = tmp_path / "real_assets_manifest.jsonl"
    m3_assets.generate_all(
        specs=specs,
        mesh_out_dir=mesh_out,
        manifest_out=manifest_out,
        project_root=tmp_path,
        seed=3,
    )

    faces_by_cat: dict[str, int] = {}
    for row in _read_manifest_rows(manifest_out):
        mesh_path = Path(str(row["mesh_path"]))
        mesh_or_scene = trimesh.load(mesh_path, force="scene")
        if isinstance(mesh_or_scene, trimesh.Scene):
            mesh = trimesh.util.concatenate(tuple(mesh_or_scene.geometry.values()))
        else:
            mesh = mesh_or_scene
        faces_by_cat[str(row["category"])] = len(mesh.faces)

    assert faces_by_cat["tree"] >= 1500
    assert faces_by_cat["lamp"] >= 500


def test_bench_and_lamp_default_to_parametric_backend_in_batch_generation(tmp_path: Path):
    trimesh = pytest.importorskip("trimesh")
    specs = [
        _spec("bench_param", "bench", style="classic"),
        _spec("lamp_param", "lamp", style="ornate"),
        _spec("trash_param", "trash"),
    ]
    mesh_out = tmp_path / "meshes"
    manifest_out = tmp_path / "real_assets_manifest.jsonl"
    m3_assets.generate_all(
        specs=specs,
        mesh_out_dir=mesh_out,
        manifest_out=manifest_out,
        project_root=tmp_path,
        seed=9,
    )

    rows = {str(row["asset_id"]): row for row in _read_manifest_rows(manifest_out)}
    assert rows["bench_param"]["source"] == "parametric_generated"
    assert rows["bench_param"]["generator_type"] == "parametric_v1"
    assert rows["bench_param"]["runtime_profile"] == "production"
    assert rows["bench_param"]["asset_role"] == "street_furniture"
    assert isinstance(rows["bench_param"]["parameter_snapshot"], dict)
    assert isinstance(rows["bench_param"]["quality_metrics"], dict)
    assert rows["lamp_param"]["source"] == "parametric_generated"
    assert rows["trash_param"]["source"] == "procedural_generated"

    for asset_id in ("bench_param", "lamp_param"):
        mesh_or_scene = trimesh.load(Path(str(rows[asset_id]["mesh_path"])), force="scene")
        mesh = trimesh.util.concatenate(tuple(mesh_or_scene.geometry.values())) if isinstance(mesh_or_scene, trimesh.Scene) else mesh_or_scene
        assert len(mesh.faces) >= m3_assets.MIN_FACES_BY_CATEGORY[str(rows[asset_id]["category"])]


def test_bench_and_lamp_can_fallback_to_legacy_backend(tmp_path: Path):
    specs = [
        _spec("bench_legacy", "bench", style="modern"),
        _spec("lamp_legacy", "lamp", style="modern"),
    ]
    manifest_out = tmp_path / "real_assets_manifest.jsonl"
    m3_assets.generate_all(
        specs=specs,
        mesh_out_dir=tmp_path / "meshes",
        manifest_out=manifest_out,
        project_root=tmp_path,
        seed=5,
        bench_lamp_backend="legacy",
    )

    rows = {str(row["asset_id"]): row for row in _read_manifest_rows(manifest_out)}
    assert rows["bench_legacy"]["source"] == "procedural_generated"
    assert "generator_type" not in rows["bench_legacy"]
    assert rows["lamp_legacy"]["source"] == "procedural_generated"
