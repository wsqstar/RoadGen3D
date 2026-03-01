from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.decoder import PlaceholderVoxelDecoder
from roadgen3d.embedder import l2_normalize
from roadgen3d.index_store import FaissIndexStore
from roadgen3d.latent_store import LatentStore
from roadgen3d.pipeline import M1Pipeline


def run_cmd(args):
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True)


def test_env_report_fields(tmp_path: Path):
    out = tmp_path / "env_report.json"
    proc = run_cmd([sys.executable, "scripts/m1_00_check_env.py", "--out", str(out)])
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert "python_version" in payload
    assert "platform" in payload
    assert "packages" in payload and isinstance(payload["packages"], dict)
    assert "torch" in payload and isinstance(payload["torch"], dict)


def test_seed_assets_outputs(tmp_path: Path):
    pytest.importorskip("torch")
    out_dir = tmp_path / "data"
    proc = run_cmd(
        [
            sys.executable,
            "scripts/m1_01_seed_assets.py",
            "--out-dir",
            str(out_dir),
            "--num-assets",
            "5",
            "--seed",
            "7",
        ]
    )
    assert proc.returncode == 0, proc.stderr
    assets_path = out_dir / "assets.jsonl"
    rows = [json.loads(line) for line in assets_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 5
    ids = [row["asset_id"] for row in rows]
    assert len(ids) == len(set(ids))
    for row in rows:
        assert (out_dir / row["latent_path"]).exists()


def test_embedding_shape_and_norm():
    projection_dim = 8
    raw = np.array([[1, 2, 3, 4, 0, 0, 0, 0], [5, 0, 0, 0, 0, 0, 0, 0]], dtype=np.float32)
    emb = l2_normalize(raw)
    assert emb.shape[1] == projection_dim
    norms = np.linalg.norm(emb, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)


def test_faiss_index_count():
    pytest.importorskip("faiss")
    matrix = l2_normalize(np.array([[1.0, 0.0], [0.0, 1.0], [0.7, 0.7]], dtype=np.float32))
    ids = ["a", "b", "c"]
    store = FaissIndexStore.build(embeddings=matrix, asset_ids=ids)
    assert store.ntotal == 3


def test_retrieve_returns_valid_ids():
    pytest.importorskip("faiss")
    matrix = l2_normalize(np.array([[1.0, 0.0], [0.0, 1.0], [0.7, 0.7]], dtype=np.float32))
    ids = ["bench_01", "lamp_01", "trash_01"]
    store = FaissIndexStore.build(embeddings=matrix, asset_ids=ids)
    hits = store.search(np.array([[0.99, 0.01]], dtype=np.float32), topk=2)[0]
    assert hits
    for hit in hits:
        assert hit.asset_id in ids


def test_decode_output_shape_and_binary():
    pytest.importorskip("torch")
    import torch

    latent = torch.randn(1, 256)
    decoder = PlaceholderVoxelDecoder(resolution=64, threshold=0.5)
    prob, voxel = decoder.decode(latent)
    assert prob.shape == (64, 64, 64)
    assert voxel.shape == (64, 64, 64)
    assert set(np.unique(voxel)).issubset({0, 1})


def test_pipeline_end_to_end(tmp_path: Path):
    pytest.importorskip("faiss")
    torch = pytest.importorskip("torch")

    data_dir = tmp_path / "data"
    latents_dir = data_dir / "latents"
    latents_dir.mkdir(parents=True)

    assets = [
        {"asset_id": "bench_01", "description": "wooden bench", "latent_path": "latents/bench_01.pt"},
        {"asset_id": "lamp_01", "description": "street lamp", "latent_path": "latents/lamp_01.pt"},
    ]
    for idx, item in enumerate(assets, start=1):
        torch.save(torch.ones(1, 256) * idx, data_dir / item["latent_path"])

    assets_path = data_dir / "assets.jsonl"
    with assets_path.open("w", encoding="utf-8") as handle:
        for item in assets:
            handle.write(json.dumps(item) + "\n")

    class FakeEmbedder:
        projection_dim = 4

        def encode_texts(self, texts):
            vectors = []
            for text in texts:
                text = text.lower()
                if "bench" in text:
                    vectors.append([1.0, 0.0, 0.0, 0.0])
                elif "lamp" in text:
                    vectors.append([0.0, 1.0, 0.0, 0.0])
                else:
                    vectors.append([0.0, 0.0, 1.0, 0.0])
            return l2_normalize(np.asarray(vectors, dtype=np.float32))

    embedder = FakeEmbedder()
    db_vectors = embedder.encode_texts([item["description"] for item in assets])
    store = FaissIndexStore.build(db_vectors, [item["asset_id"] for item in assets])
    latent_store = LatentStore(assets_path)
    decoder = PlaceholderVoxelDecoder()
    pipeline = M1Pipeline(embedder=embedder, index_store=store, latent_store=latent_store, decoder=decoder)

    artifacts = tmp_path / "artifacts"
    result, hits = pipeline.run(query="I need a bench", topk=1, output_dir=artifacts)
    assert result.top_hit.asset_id == "bench_01"
    assert (artifacts / "voxel_prob.npy").exists()
    assert (artifacts / "voxel_bin.npy").exists()

    result_path = artifacts / "pipeline_result.json"
    pipeline.save_result_json(result, hits, result_path)
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["query"] == "I need a bench"
    assert payload["top_hit"]["asset_id"] == "bench_01"


def test_missing_model_fails_cleanly(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    assets_path = data_dir / "assets.jsonl"
    assets_path.write_text(
        json.dumps(
            {
                "asset_id": "bench_01",
                "description": "a wooden park bench",
                "latent_path": "latents/bench_01.pt",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    proc = run_cmd(
        [
            sys.executable,
            "scripts/m1_02_embed_texts.py",
            "--assets",
            str(assets_path),
            "--out",
            str(tmp_path / "artifacts"),
            "--model-dir",
            str(tmp_path / "missing_model"),
            "--local-files-only",
        ]
    )
    assert proc.returncode != 0
    combined = (proc.stdout + "\n" + proc.stderr).lower()
    assert (
        "failed to load clip model" in combined
        or "transformers" in combined
        or "torch" in combined
    )


def test_missing_latent_fails_cleanly(tmp_path: Path):
    pytest.importorskip("torch")
    assets_path = tmp_path / "assets.jsonl"
    assets_path.write_text(
        json.dumps(
            {
                "asset_id": "bench_01",
                "description": "a wooden park bench",
                "latent_path": "latents/does_not_exist.pt",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    store = LatentStore(assets_jsonl_path=assets_path)
    with pytest.raises(FileNotFoundError) as exc:
        store.load("bench_01")
    message = str(exc.value)
    assert "bench_01" in message
    assert "does_not_exist.pt" in message
