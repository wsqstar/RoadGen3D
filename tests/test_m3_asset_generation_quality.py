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
        target_h=2.0 if category in {"lamp", "tree", "bus_stop"} else 1.0,
        target_w=1.5 if category in {"bus_stop", "bench"} else 0.8,
        target_d=1.2 if category in {"bus_stop", "bench"} else 0.8,
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
