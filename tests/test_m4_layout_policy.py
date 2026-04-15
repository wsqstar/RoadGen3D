from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.layout_features import (  # noqa: E402
    CandidateDescriptor,
    PolicyFeatureContext,
    build_candidate_feature,
)
from roadgen3d.layout_policy import (  # noqa: E402
    LayoutPolicyMLP,
    PolicyTrainConfig,
    split_samples_by_scene,
    train_layout_policy,
)
from roadgen3d.types import RetrievalHit, StreetComposeConfig  # noqa: E402
from roadgen3d.street_layout import compose_street_scene  # noqa: E402
import roadgen3d.street_layout as street_layout  # noqa: E402

import scripts.layout_collect_data as m4_collect  # noqa: E402


def _build_context() -> PolicyFeatureContext:
    return PolicyFeatureContext(
        query="modern clean urban street",
        category="bench",
        slot_idx=3,
        slot_x=12.5,
        slot_z=5.0,
        length_m=80.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        lane_count=2,
        density=1.0,
        topk=20,
        used_asset_ids={"bench_01"},
    )


def _make_box_mesh(path: Path) -> None:
    trimesh = pytest.importorskip("trimesh")
    mesh = trimesh.creation.box(extents=(0.8, 0.5, 0.5))
    path.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(path)


def test_feature_vector_shape_and_determinism():
    context = _build_context()
    candidate = CandidateDescriptor(asset_id="bench_02", category="bench", score=0.82)

    vec_a = build_candidate_feature(context, candidate, candidate_rank=1, candidate_count=4)
    vec_b = build_candidate_feature(context, candidate, candidate_rank=1, candidate_count=4)

    assert vec_a.shape == (35,)
    assert vec_a.dtype == np.float32
    assert np.array_equal(vec_a, vec_b)


def test_policy_forward_shape():
    torch = pytest.importorskip("torch")
    model = LayoutPolicyMLP(input_dim=35, hidden_dim=64, hidden_dim2=32, dropout=0.1)
    x = torch.randn(5, 35)
    y = model(x)
    assert tuple(y.shape) == (5, 1)


def test_collect_policy_data_schema(tmp_path: Path, monkeypatch):
    manifest = tmp_path / "real_assets_manifest.jsonl"
    mesh_path = tmp_path / "meshes" / "bench_01.glb"
    latent_path = tmp_path / "latents" / "bench_01.pt"
    manifest.write_text(
        json.dumps(
            {
                "asset_id": "bench_01",
                "category": "bench",
                "text_desc": "a wooden park bench",
                "mesh_path": str(mesh_path),
                "latent_path": str(latent_path),
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )

    queries = tmp_path / "queries.txt"
    queries.write_text("modern clean urban street\n", encoding="utf-8")

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
            hits = [RetrievalHit(asset_id="bench_01", score=0.95)]
            return [hits for _ in range(query_embeddings.shape[0])]

    monkeypatch.setattr(m4_collect, "ClipTextEmbedder", FakeEmbedder)
    monkeypatch.setattr(m4_collect, "FaissIndexStore", FakeIndexStore)
    monkeypatch.setattr(
        m4_collect,
        "_load_mesh_cache",
        lambda rows: {"bench_01": SimpleNamespace(half_x=0.2, half_z=0.2, min_y=0.0)},
    )

    out_path = tmp_path / "policy_train.jsonl"
    samples = m4_collect.collect_policy_data(
        manifest=manifest,
        artifacts=tmp_path / "artifacts",
        out=out_path,
        queries_path=queries,
        seed_start=0,
        seed_end=0,
        model_name="openai/clip-vit-base-patch32",
        model_dir=None,
        local_files_only=True,
        device="cpu",
        length_m=10.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        lane_count=2,
        density=1.0,
        topk_per_category=5,
        max_trials_per_slot=3,
    )

    assert samples
    assert out_path.exists()

    first = json.loads(out_path.read_text(encoding="utf-8").splitlines()[0])
    expected = {
        "scene_id",
        "query",
        "seed",
        "category",
        "slot_idx",
        "slot_x",
        "slot_z",
        "road_params",
        "candidate_asset_ids",
        "candidate_scores",
        "candidate_categories",
        "chosen_asset_id",
        "chosen_index",
        "chosen_source",
        "used_asset_ids_before_slot",
        "dropped",
    }
    assert expected.issubset(set(first.keys()))


def test_train_smoke_reduces_val_loss(tmp_path: Path):
    rng = np.random.default_rng(2026)
    samples = []
    for i in range(300):
        features = rng.normal(0.0, 0.05, size=(3, 35)).astype(np.float32)
        features[:, 0] += np.array([1.2, 0.2, -0.2], dtype=np.float32)
        chosen_index = int(np.argmax(features[:, 0]))
        samples.append(
            {
                "scene_id": f"scene_{i // 3}",
                "candidate_features": features,
                "chosen_index": chosen_index,
            }
        )

    train_samples, val_samples = split_samples_by_scene(samples, train_ratio=0.9)
    assert train_samples and val_samples

    config = PolicyTrainConfig(
        epochs=12,
        batch_size=64,
        lr=1e-3,
        weight_decay=1e-4,
        entropy_weight=0.01,
        patience=5,
        device="cpu",
    )
    result = train_layout_policy(
        train_samples=train_samples,
        val_samples=val_samples,
        out_dir=tmp_path,
        config=config,
    )
    curve = result["curve"]
    assert curve
    first_val = float(curve[0]["val_loss"])
    best_val = min(float(item["val_loss"]) for item in curve)
    assert best_val < first_val
    assert Path(result["checkpoint"]).exists()


def test_learned_policy_fallback_when_ckpt_missing(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    mesh_path = tmp_path / "meshes" / "bench_01.glb"
    _make_box_mesh(mesh_path)

    manifest = tmp_path / "real_assets_manifest.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "asset_id": "bench_01",
                "category": "bench",
                "text_desc": "a bench",
                "mesh_path": str(mesh_path),
                "latent_path": str(tmp_path / "latents" / "bench_01.pt"),
                "license": "cc-by",
                "source": "test",
                "split": "train",
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )

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
            hits = [RetrievalHit(asset_id="bench_01", score=0.99)]
            return [hits for _ in range(query_embeddings.shape[0])]

    monkeypatch.setattr(street_layout, "ClipTextEmbedder", FakeEmbedder)
    monkeypatch.setattr(street_layout, "FaissIndexStore", FakeIndexStore)

    config = StreetComposeConfig(
        query="modern clean urban street",
        length_m=20.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        lane_count=2,
        density=1.0,
        seed=7,
        topk_per_category=5,
        max_trials_per_slot=5,
    )

    result = compose_street_scene(
        config=config,
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        model_name="openai/clip-vit-base-patch32",
        model_dir=None,
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "out",
        placement_policy="learned",
        policy_ckpt=tmp_path / "missing_policy.pt",
        policy_temperature=0.12,
    )

    assert result.instance_count > 0
    assert result.outputs.get("policy_used") == "rule"
    assert "fallback" in str(result.outputs.get("policy_fallback_reason", "")).lower()
