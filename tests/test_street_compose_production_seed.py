from __future__ import annotations

import json
import random
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

from roadgen3d.types import RetrievalHit, StreetComposeConfig
import roadgen3d.street_layout as street_layout
from scripts import asset_seed_production as seed_assets


def _write_manifest(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=True) for row in rows) + "\n", encoding="utf-8")


def _make_mesh(path: Path, *, kind: str = "box") -> None:
    trimesh = pytest.importorskip("trimesh")
    if kind == "cylinder":
        mesh = trimesh.creation.cylinder(radius=0.1, height=1.5, sections=16)
    else:
        mesh = trimesh.creation.box(extents=(0.8, 0.5, 0.5))
    path.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(path)


def _preview_row(base_dir: Path, *, asset_id: str, category: str) -> dict[str, object]:
    mesh_path = base_dir / "preview_assets" / f"{asset_id}.glb"
    latent_path = base_dir / "preview_assets" / f"{asset_id}.pt"
    _make_mesh(mesh_path, kind="cylinder" if category == "lamp" else "box")
    latent_path.write_bytes(b"latent")
    return {
        "asset_id": asset_id,
        "category": category,
        "text_desc": f"preview {category}",
        "mesh_path": str(mesh_path),
        "latent_path": str(latent_path),
        "source": "parametric_generated",
        "generator_type": "parametric_v1",
        "runtime_profile": "preview",
        "style_tags": ["modern"],
        "material_family": "metal" if category == "lamp" else "metal_wood",
        "parameter_snapshot": {"detail_level": 2, "effective_detail_level": 1},
        "quality_metrics": {"face_count": 892 if category == "lamp" else 1120},
        "mesh_face_count": 892 if category == "lamp" else 1120,
        "quality_tier": 2,
        "scene_eligible": True,
        "quality_notes": ["preview_runtime"],
    }


class _FakeEmbedder:
    def __init__(self, *args, **kwargs):
        self.model_source = "fake-model"
        self.projection_dim = 4

    def encode_texts(self, texts):
        return np.ones((len(texts), 4), dtype=np.float32)


class _FakeIndexStore:
    def __init__(self, embeddings, asset_ids):
        self.embeddings = embeddings
        self.asset_ids = asset_ids

    @classmethod
    def build(cls, embeddings, asset_ids):
        return cls(embeddings, asset_ids)

    def save(self, index_path: Path, id_map_path: Path) -> None:
        index_path.write_bytes(b"faiss")
        id_map_path.write_text(json.dumps(self.asset_ids, ensure_ascii=True), encoding="utf-8")


class _UnitFakeEmbedder:
    def encode_texts(self, texts):
        return np.ones((len(texts), 8), dtype=np.float32)


class _UnitFakeIndexStore:
    def __init__(self, hits):
        self._hits = hits

    def search(self, query_embeddings, topk=1):
        return [self._hits[:topk]]


def test_seed_production_assets_upserts_manifest_and_demotes_preview(tmp_path: Path):
    pytest.importorskip("trimesh")

    manifest_path = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(
        manifest_path,
        [
            _preview_row(tmp_path, asset_id="bench_modern_preview", category="bench"),
            _preview_row(tmp_path, asset_id="lamp_modern_preview", category="lamp"),
        ],
    )

    summary = seed_assets.seed_production_assets(
        manifest_path=manifest_path,
        mesh_dir=tmp_path / "data" / "meshes",
        latents_dir=tmp_path / "data" / "latents",
        metadata_dir=tmp_path / "artifacts" / "parametric_production",
        artifacts_dir=tmp_path / "artifacts" / "real",
        device="cpu",
        rebuild_index_enabled=False,
    )

    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_id = {str(row["asset_id"]): row for row in rows}

    assert summary["generated_asset_ids"] == [
        "bench_modern_production",
        "bench_nordic_production",
        "lamp_modern_production",
        "lamp_victorian_production",
    ]

    for asset_id in summary["generated_asset_ids"]:
        row = by_id[asset_id]
        assert row["runtime_profile"] == "production"
        assert row["source"] == "parametric_generated"
        assert row["generator_type"] == "parametric_v1"
        assert "face_count" in row["quality_metrics"]
        assert Path(row["mesh_path"]).parent == (tmp_path / "data" / "meshes").resolve()
        assert Path(row["latent_path"]).parent == (tmp_path / "data" / "latents").resolve()
        assert Path(row["mesh_path"]).exists()
        assert Path(row["latent_path"]).exists()
        assert Path(tmp_path / "artifacts" / "parametric_production" / f"{asset_id}.result.json").exists()

    assert by_id["bench_modern_preview"]["scene_eligible"] is False
    assert "preview_demoted_after_production_seed" in by_id["bench_modern_preview"]["quality_notes"]
    assert by_id["lamp_modern_preview"]["scene_eligible"] is False
    assert "preview_demoted_after_production_seed" in by_id["lamp_modern_preview"]["quality_notes"]

    row_count_before = len(rows)
    seed_assets.seed_production_assets(
        manifest_path=manifest_path,
        mesh_dir=tmp_path / "data" / "meshes",
        latents_dir=tmp_path / "data" / "latents",
        metadata_dir=tmp_path / "artifacts" / "parametric_production",
        artifacts_dir=tmp_path / "artifacts" / "real",
        device="cpu",
        rebuild_index_enabled=False,
    )
    rows_after = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows_after) == row_count_before


def test_rebuild_real_index_writes_expected_outputs(tmp_path: Path, monkeypatch):
    manifest_path = tmp_path / "data" / "real_assets_manifest.jsonl"
    latent_path = tmp_path / "data" / "latents" / "bench_modern_production.pt"
    latent_path.parent.mkdir(parents=True, exist_ok=True)
    latent_path.write_bytes(b"latent")
    _write_manifest(
        manifest_path,
        [
            {
                "asset_id": "bench_modern_production",
                "category": "bench",
                "text_desc": "production bench",
                "mesh_path": str(tmp_path / "data" / "meshes" / "bench_modern_production.glb"),
                "latent_path": str(latent_path),
                "source": "parametric_generated",
                "runtime_profile": "production",
            }
        ],
    )

    monkeypatch.setattr(seed_assets, "ClipTextEmbedder", _FakeEmbedder)
    monkeypatch.setattr(seed_assets, "FaissIndexStore", _FakeIndexStore)

    summary = seed_assets.rebuild_real_index(
        manifest_path=manifest_path,
        artifacts_dir=tmp_path / "artifacts" / "real",
        model_name="fake-model",
        model_dir=None,
        local_files_only=True,
        device="cpu",
    )

    assert summary["asset_count"] == 1
    assert (tmp_path / "artifacts" / "real" / "asset_text_embeds.npy").exists()
    assert (tmp_path / "artifacts" / "real" / "asset_ids.json").exists()
    assert (tmp_path / "artifacts" / "real" / "embed_meta.json").exists()
    assert (tmp_path / "artifacts" / "real" / "index_ip.faiss").exists()
    assert (tmp_path / "artifacts" / "real" / "id_map.json").exists()
    assert (tmp_path / "artifacts" / "real" / "real_assets_for_pipeline.jsonl").exists()


@pytest.mark.parametrize("category", ["bench", "lamp"])
@pytest.mark.parametrize("curation_mode", ["scene_ready_first", "parametric_first"])
def test_scene_ready_curator_prefers_production_parametric_over_preview(monkeypatch, category: str, curation_mode: str):
    preview_id = f"{category}_preview"
    production_id = f"{category}_production"
    material_family = "metal" if category == "lamp" else "metal_wood"
    asset_by_id = {
        preview_id: {
            "asset_id": preview_id,
            "category": category,
            "text_desc": f"preview {category}",
            "mesh_path": "",
            "latent_path": "",
            "source": "parametric_generated",
            "generator_type": "parametric_v1",
            "runtime_profile": "preview",
            "scene_eligible": False,
            "quality_tier": 1,
            "mesh_face_count": 900,
            "material_family": material_family,
        },
        production_id: {
            "asset_id": production_id,
            "category": category,
            "text_desc": f"production {category}",
            "mesh_path": "",
            "latent_path": "",
            "source": "parametric_generated",
            "generator_type": "parametric_v1",
            "runtime_profile": "production",
            "scene_eligible": True,
            "quality_tier": 3,
            "mesh_face_count": 1500,
            "material_family": material_family,
        },
    }
    hits = [
        RetrievalHit(asset_id=preview_id, score=0.97),
        RetrievalHit(asset_id=production_id, score=0.85),
    ]
    monkeypatch.setattr(street_layout, "_softmax_weights", lambda scores, temperature: [1.0] + [0.0] * (len(scores) - 1))

    row, _score, _source = street_layout._pick_category_candidate(
        query="street",
        category=category,
        topk=2,
        embedder=_UnitFakeEmbedder(),
        index_store=_UnitFakeIndexStore(hits),
        asset_by_id=asset_by_id,
        category_pool=list(asset_by_id.values()),
        used_asset_ids=set(),
        rng=random.Random(0),
        config=StreetComposeConfig(
            query="street",
            length_m=60.0,
            road_width_m=8.0,
            sidewalk_width_m=2.5,
            lane_count=2,
            density=1.0,
            seed=0,
            topk_per_category=2,
            max_trials_per_slot=5,
            asset_curation_mode=curation_mode,
        ),
    )

    assert row["asset_id"] == production_id
