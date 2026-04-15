from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.types import RetrievalHit, StreetComposeConfig, StreetComposeResult
from scripts import asset_clean_manifest as manifest_cleaner
from scripts import asset_import_external_trees as tree_import
import roadgen3d.street_layout as street_layout
from roadgen3d.street_layout import compose_street_scene


def _write_manifest(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=True) for row in rows) + "\n", encoding="utf-8")


def _make_mesh(path: Path, *, kind: str = "box") -> None:
    trimesh = pytest.importorskip("trimesh")
    if kind == "cylinder":
        mesh = trimesh.creation.cylinder(radius=0.1, height=1.5, sections=16)
        mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2.0, [1.0, 0.0, 0.0]))
    else:
        mesh = trimesh.creation.box(extents=(0.8, 0.5, 0.5))
    path.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(path)


def _make_tree_mesh(path: Path, *, sideways: bool = False) -> None:
    trimesh = pytest.importorskip("trimesh")
    trunk = trimesh.creation.box(extents=(0.18, 1.0, 0.18))
    trunk.apply_translation([0.0, 0.5, 0.0])
    canopy = trimesh.creation.icosphere(subdivisions=2, radius=0.45)
    canopy.apply_translation([0.0, 1.35, 0.0])
    mesh = trimesh.util.concatenate((trunk, canopy))
    if sideways:
        mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2.0, [0.0, 0.0, 1.0]))
    path.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(path)


def _make_stylized_upright_tree_mesh(path: Path) -> None:
    trimesh = pytest.importorskip("trimesh")
    canopy = trimesh.creation.cone(radius=0.35, height=1.5, sections=64)
    canopy.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2.0, [1.0, 0.0, 0.0]))
    canopy.apply_translation([0.0, 0.75, 0.0])
    path.parent.mkdir(parents=True, exist_ok=True)
    canopy.export(path)


def _build_rows(base_dir: Path, *, tree_source: str, external_tree: bool = False) -> list[dict[str, object]]:
    categories = [
        ("bench_01", "bench", "test"),
        ("lamp_01", "lamp", "test"),
        ("trash_01", "trash", "test"),
        ("tree_01", "tree", tree_source),
        ("bus_stop_01", "bus_stop", "test"),
        ("mailbox_01", "mailbox", "test"),
        ("hydrant_01", "hydrant", "test"),
        ("bollard_01", "bollard", "test"),
    ]
    rows: list[dict[str, object]] = []
    for asset_id, category, source in categories:
        mesh_path = base_dir / "meshes" / f"{asset_id}.glb"
        if category == "tree" and external_tree:
            _make_tree_mesh(mesh_path)
        else:
            _make_mesh(mesh_path, kind="cylinder" if category in {"lamp", "tree"} else "box")
        row = {
            "asset_id": asset_id,
            "category": category,
            "text_desc": f"{asset_id} desc",
            "mesh_path": str(mesh_path),
            "latent_path": str(base_dir / "latents" / f"{asset_id}.pt"),
            "license": "cc-by",
            "source": source,
            "split": "train",
        }
        if category == "tree" and external_tree:
            row["scene_eligible"] = True
            row["quality_tier"] = 3
            row["mesh_face_count"] = 512
            row["quality_notes"] = ["tree_upright_validated"]
        rows.append(row)
    return rows


def _setup_fake_retrieval(monkeypatch, asset_ids: list[str]) -> None:
    class FakeEmbedder:
        def __init__(self, *args, **kwargs):
            pass

        def encode_texts(self, texts):
            return np.ones((len(texts), 8), dtype=np.float32)

    class FakeIndexStore:
        @classmethod
        def load(cls, *args, **kwargs):
            return cls()

        def search(self, query_embeddings, topk=1):
            ranked = [
                RetrievalHit(asset_id=asset_id, score=float(1.0 - i * 0.01))
                for i, asset_id in enumerate(asset_ids[:topk])
            ]
            return [list(ranked) for _ in range(query_embeddings.shape[0])]

    monkeypatch.setattr(street_layout, "ClipTextEmbedder", FakeEmbedder)
    monkeypatch.setattr(street_layout, "FaissIndexStore", FakeIndexStore)


def _config() -> StreetComposeConfig:
    return StreetComposeConfig(
        query="tree-lined residential street",
        length_m=60.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        lane_count=2,
        density=1.0,
        seed=42,
        topk_per_category=20,
        max_trials_per_slot=20,
    )


def test_clean_manifest_rows_disables_procedural_tree_scene_eligibility(tmp_path: Path):
    rows = [
        {
            "asset_id": "tree_legacy",
            "category": "tree",
            "text_desc": "legacy tree",
            "mesh_path": str(tmp_path / "tree.glb"),
            "latent_path": str(tmp_path / "tree.pt"),
            "source": "procedural_generated",
            "mesh_face_count": 620,
            "quality_metrics": {"face_count": 620},
        },
        {
            "asset_id": "tree_stylized",
            "category": "tree",
            "text_desc": "stylized imported tree",
            "mesh_path": str(tmp_path / "tree_external.glb"),
            "latent_path": str(tmp_path / "tree_external.pt"),
            "source": "external_import",
            "mesh_face_count": 620,
            "quality_metrics": {"face_count": 620},
            "quality_notes": ["tree_upright_validated"],
        },
        {
            "asset_id": "tree_real",
            "category": "tree",
            "text_desc": "real tree",
            "mesh_path": str(tmp_path / "tree_real.glb"),
            "latent_path": str(tmp_path / "tree_real.pt"),
            "source": "objaverse_import",
            "generator_type": "objaverse_v1",
            "mesh_face_count": 620,
            "quality_metrics": {"face_count": 620},
            "quality_notes": ["tree_upright_validated"],
        },
    ]

    cleaned = manifest_cleaner.clean_manifest_rows(rows, tmp_path)
    by_id = {str(row["asset_id"]): row for row in cleaned}

    assert by_id["tree_legacy"]["scene_eligible"] is False
    assert "procedural_tree_disabled_for_scene_generation" in by_id["tree_legacy"]["quality_notes"]
    assert by_id["tree_stylized"]["scene_eligible"] is True
    assert "tree_upright_validated" in by_id["tree_stylized"]["quality_notes"]
    assert by_id["tree_real"]["scene_eligible"] is True
    assert "tree_upright_validated" in by_id["tree_real"]["quality_notes"]


def test_compose_degrades_gracefully_when_only_procedural_trees_exist(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    rows = _build_rows(tmp_path / "data", tree_source="procedural_generated")
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])
    monkeypatch.setattr(street_layout, "render_presentation_views", lambda *args, **kwargs: [])

    result = compose_street_scene(
        config=_config(),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
    )

    assert isinstance(result, StreetComposeResult)

    payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    summary = payload["summary"]
    assert summary["tree_assets_unavailable"] is True
    assert summary["tree_inventory_raw_count"] == 1
    assert summary["parametric_tree_fallback_count"] == 0
    assert summary["tree_inventory_scene_ready_count"] == 0


def test_import_external_tree_assets_accepts_upright_tree(tmp_path: Path):
    pytest.importorskip("trimesh")
    source_mesh = tmp_path / "external" / "tree_real_001.glb"
    _make_tree_mesh(source_mesh)
    input_manifest = tmp_path / "external_trees.jsonl"
    _write_manifest(
        input_manifest,
        [
            {
                "asset_id": "tree_real_001",
                "category": "tree",
                "text_desc": "high quality maple street tree",
                "mesh_path": str(source_mesh),
                "license": "cc-by",
                "source": "objaverse_import",
                "split": "train",
                "generator_type": "objaverse_v1",
                "style_tags": ["stylized", "street_tree"],
                "theme_tags": ["green", "residential"],
                "objaverse_uid": "obj_uid_tree_001",
                "objaverse_lvis_category": "tree",
                "objaverse_score": 3.25,
            }
        ],
    )

    summary = tree_import.import_external_tree_assets(
        input_manifest=input_manifest,
        output_manifest=tmp_path / "data" / "real_assets_manifest.jsonl",
        mesh_out_dir=tmp_path / "data" / "meshes",
        latents_dir=tmp_path / "data" / "latents",
        artifacts_dir=tmp_path / "artifacts" / "real",
        local_files_only=True,
        device="cpu",
        rebuild_index_enabled=False,
    )

    assert summary["imported_asset_ids"] == ["tree_real_001"]
    rows = [
        json.loads(line)
        for line in (tmp_path / "data" / "real_assets_manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    row = rows[0]
    assert row["asset_id"] == "tree_real_001"
    assert row["scene_eligible"] is True
    assert "tree_upright_validated" in row["quality_notes"]
    assert row["source"] == "objaverse_import"
    assert row["generator_type"] == "objaverse_v1"
    assert row["style_tags"] == ["stylized", "street_tree"]
    assert row["theme_tags"] == ["green", "residential"]
    assert row["objaverse_uid"] == "obj_uid_tree_001"
    assert row["objaverse_lvis_category"] == "tree"
    assert row["objaverse_score"] == pytest.approx(3.25)
    assert Path(row["mesh_path"]).exists()
    assert Path(row["latent_path"]).exists()


def test_import_external_tree_assets_accepts_stylized_overall_upright_tree(tmp_path: Path):
    pytest.importorskip("trimesh")
    source_mesh = tmp_path / "external" / "tree_stylized_001.glb"
    _make_stylized_upright_tree_mesh(source_mesh)
    input_manifest = tmp_path / "external_trees.jsonl"
    _write_manifest(
        input_manifest,
        [
            {
                "asset_id": "tree_stylized_001",
                "category": "tree",
                "text_desc": "stylized upright conifer tree",
                "mesh_path": str(source_mesh),
                "license": "cc-by",
                "source": "objaverse_import",
                "split": "train",
            }
        ],
    )

    tree_import.import_external_tree_assets(
        input_manifest=input_manifest,
        output_manifest=tmp_path / "data" / "real_assets_manifest.jsonl",
        mesh_out_dir=tmp_path / "data" / "meshes",
        latents_dir=tmp_path / "data" / "latents",
        artifacts_dir=tmp_path / "artifacts" / "real",
        local_files_only=True,
        device="cpu",
        rebuild_index_enabled=False,
    )

    rows = [
        json.loads(line)
        for line in (tmp_path / "data" / "real_assets_manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    metrics = rows[0]["quality_metrics"]["tree_upright_validation"]
    assert rows[0]["scene_eligible"] is True
    assert metrics["validation_mode"] == "overall_upright_fallback"


def test_compose_accepts_scene_ready_stylized_tree_inventory(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    rows = _build_rows(tmp_path / "data", tree_source="external_import", external_tree=True)
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])
    monkeypatch.setattr(street_layout, "render_presentation_views", lambda *args, **kwargs: [])

    result = compose_street_scene(
        config=_config(),
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts",
    )

    payload = json.loads(Path(result.outputs["scene_layout"]).read_text(encoding="utf-8"))
    summary = payload["summary"]

    assert summary["tree_assets_unavailable"] is False
    assert summary["tree_inventory_scene_ready_count"] == 1
    assert summary["parametric_tree_fallback_count"] == 0


def test_import_external_tree_assets_rejects_sideways_tree_without_rotation(tmp_path: Path):
    pytest.importorskip("trimesh")
    source_mesh = tmp_path / "external" / "tree_real_sideways.glb"
    _make_tree_mesh(source_mesh, sideways=True)
    input_manifest = tmp_path / "external_trees.jsonl"
    output_manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(
        input_manifest,
        [
            {
                "asset_id": "tree_real_sideways",
                "category": "tree",
                "text_desc": "sideways tree",
                "mesh_path": str(source_mesh),
                "license": "cc-by",
                "source": "objaverse",
                "split": "train",
            }
        ],
    )

    result = tree_import.import_external_tree_assets(
        input_manifest=input_manifest,
        output_manifest=output_manifest,
        mesh_out_dir=tmp_path / "data" / "meshes",
        latents_dir=tmp_path / "data" / "latents",
        artifacts_dir=tmp_path / "artifacts" / "real",
        local_files_only=True,
        device="cpu",
        rebuild_index_enabled=False,
    )

    assert "tree_real_sideways" in result["skipped_asset_ids"]
    assert "tree_real_sideways" not in result["imported_asset_ids"]


def test_import_external_tree_assets_accepts_sideways_tree_with_rotation(tmp_path: Path):
    pytest.importorskip("trimesh")
    source_mesh = tmp_path / "external" / "tree_real_sideways.glb"
    _make_tree_mesh(source_mesh, sideways=True)
    input_manifest = tmp_path / "external_trees.jsonl"
    output_manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(
        input_manifest,
        [
            {
                "asset_id": "tree_real_sideways",
                "category": "tree",
                "text_desc": "rotated tree",
                "mesh_path": str(source_mesh),
                "license": "cc-by",
                "source": "objaverse",
                "split": "train",
                "import_rotation_deg_xyz": [0.0, 0.0, -90.0],
            }
        ],
    )

    tree_import.import_external_tree_assets(
        input_manifest=input_manifest,
        output_manifest=output_manifest,
        mesh_out_dir=tmp_path / "data" / "meshes",
        latents_dir=tmp_path / "data" / "latents",
        artifacts_dir=tmp_path / "artifacts" / "real",
        local_files_only=True,
        device="cpu",
        rebuild_index_enabled=False,
    )

    rows = [json.loads(line) for line in output_manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    row = rows[0]
    assert row["scene_eligible"] is True
    assert "tree_upright_validated" in row["quality_notes"]

    trimesh = pytest.importorskip("trimesh")
    mesh = trimesh.load(Path(row["mesh_path"]), force="mesh")
    assert abs(float(mesh.bounds[0][1])) <= 1e-3
