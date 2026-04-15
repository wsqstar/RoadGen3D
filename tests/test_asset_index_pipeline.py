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

from roadgen3d.decoder import PlaceholderVoxelDecoder
from roadgen3d.decoder_shapee import ShapeEDecoder
from roadgen3d.types import PipelineResult, RetrievalHit
from roadgen3d.voxel_export import export_voxel_meshes
from scripts.asset_ingest import check_mesh_latent_pairs, validate_manifest_row
from scripts.asset_build_index import evaluate_topk_category_hits


def test_voxel_export_files_created(tmp_path: Path):
    pytest.importorskip("trimesh")
    pytest.importorskip("skimage")
    voxel = np.zeros((16, 16, 16), dtype=np.uint8)
    voxel[4:12, 4:12, 4:12] = 1
    out = export_voxel_meshes(
        voxel_bin=voxel,
        out_dir=tmp_path,
        stem="sample",
        voxel_size=0.1,
        method="marching_cubes",
        export_format="both",
    )
    assert Path(out["mesh_glb"]).exists()
    assert Path(out["mesh_ply"]).exists()
    assert Path(out["mesh_glb"]).stat().st_size > 0
    assert Path(out["mesh_ply"]).stat().st_size > 0


def test_pipeline_result_contains_mesh_outputs(tmp_path: Path):
    pytest.importorskip("trimesh")
    pytest.importorskip("skimage")
    torch = pytest.importorskip("torch")

    from roadgen3d.latent_store import LatentStore
    from roadgen3d.pipeline import M1Pipeline

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    latent_path = data_dir / "latents" / "bench_01.pt"
    latent_path.parent.mkdir(parents=True)
    torch.save(torch.randn(1, 256), latent_path)

    assets_path = data_dir / "assets.jsonl"
    assets_path.write_text(
        json.dumps({"asset_id": "bench_01", "description": "wooden bench", "latent_path": str(latent_path)})
        + "\n",
        encoding="utf-8",
    )

    class FakeEmbedder:
        def encode_texts(self, texts):
            return np.array([[1.0, 0.0]], dtype=np.float32)

    class FakeIndexStore:
        def search(self, query_embeddings, topk=1):
            return [[RetrievalHit(asset_id="bench_01", score=1.0)]]

    pipeline = M1Pipeline(
        embedder=FakeEmbedder(),
        index_store=FakeIndexStore(),
        latent_store=LatentStore(assets_path),
        decoder=PlaceholderVoxelDecoder(),
    )
    result, _ = pipeline.run(query="bench", topk=1, output_dir=tmp_path / "artifacts")
    assert "mesh_glb" in result.outputs
    assert "mesh_ply" in result.outputs





def test_decoder_interface_placeholder_and_shapee():
    pytest.importorskip("torch")
    pytest.importorskip("trimesh")
    latent = np.random.randn(1, 256).astype(np.float32)
    placeholder = PlaceholderVoxelDecoder()
    p_prob, p_vox, p_meta = placeholder.decode(latent)
    assert p_prob.shape == (64, 64, 64)
    assert p_vox.shape == (64, 64, 64)
    assert p_meta["decoder"] == "placeholder"

    # Test ShapeEDecoder with voxel conversion enabled for backward compatibility
    shapee = ShapeEDecoder(fallback_decoder=placeholder, strict=False, skip_voxel=False)
    s_prob, s_vox, s_meta = shapee.decode(latent)
    assert s_prob.shape == (64, 64, 64)
    assert s_vox.shape == (64, 64, 64)
    assert "decoder" in s_meta


def test_shapee_missing_model_fallback():
    pytest.importorskip("torch")
    pytest.importorskip("trimesh")
    latent = np.random.randn(1, 256).astype(np.float32)
    shapee = ShapeEDecoder(
        resolution=64,
        threshold=0.5,
        strict=False,
        fallback_decoder=PlaceholderVoxelDecoder(),
        model_dir=Path("/tmp/nonexistent-shapee-model"),
        skip_voxel=False,  # Test with voxel conversion for backward compatibility
    )
    _, _, meta = shapee.decode(latent)
    assert meta["decoder"] in {"shapee_fallback", "shapee"}
    if meta["decoder"] == "shapee_fallback":
        assert "shapee_error" in meta


def test_real_manifest_schema_validation():
    row = {
        "asset_id": "bench_001",
        "category": "bench",
        "text_desc": "a wooden park bench",
        "mesh_path": "/tmp/mesh.glb",
        "latent_path": "/tmp/latent.pt",
        "license": "cc-by",
        "source": "objaverse",
        "split": "train",
    }
    assert validate_manifest_row(row) == []


def test_real_latent_mesh_pair_consistency(tmp_path: Path):
    mesh = tmp_path / "mesh.glb"
    latent = tmp_path / "latent.pt"
    mesh.write_bytes(b"mesh")
    latent.write_bytes(b"latent")
    rows = [
        {"asset_id": "a", "mesh_path": str(mesh), "latent_path": str(latent)},
        {"asset_id": "b", "mesh_path": str(mesh), "latent_path": str(latent)},
    ]
    assert check_mesh_latent_pairs(rows) == []

    bad_rows = [{"asset_id": "c", "mesh_path": str(tmp_path / "missing.glb"), "latent_path": str(latent)}]
    errors = check_mesh_latent_pairs(bad_rows)
    assert errors
    assert "mesh missing" in errors[0]


def test_real_retrieval_topk_category_metric():
    predictions = [
        {
            "target_category": "bench",
            "hits": [
                {"asset_id": "bench_1", "category": "bench", "score": 0.9},
                {"asset_id": "lamp_1", "category": "lamp", "score": 0.8},
            ],
        },
        {
            "target_category": "tree",
            "hits": [
                {"asset_id": "lamp_2", "category": "lamp", "score": 0.7},
                {"asset_id": "tree_2", "category": "tree", "score": 0.6},
            ],
        },
        {
            "target_category": "trash",
            "hits": [
                {"asset_id": "mailbox_3", "category": "mailbox", "score": 0.7},
                {"asset_id": "bench_3", "category": "bench", "score": 0.5},
            ],
        },
    ]
    score_top1 = evaluate_topk_category_hits(predictions, topk=1)
    score_top3 = evaluate_topk_category_hits(predictions, topk=3)
    assert score_top1 == pytest.approx(1.0 / 3.0)
    assert score_top3 == pytest.approx(2.0 / 3.0)
