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

import roadgen3d.street_layout as street_layout  # noqa: E402
from roadgen3d.graph_template_scene_bridge import build_graph_template_scene_bridge  # noqa: E402
from roadgen3d.street_layout import compose_street_scene  # noqa: E402
from roadgen3d.types import RetrievalHit, StreetComposeConfig  # noqa: E402


def _write_manifest(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _build_real_rows(base_dir: Path) -> list[dict[str, object]]:
    base_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for index, category in enumerate(("bench", "lamp", "tree", "building")):
        asset_path = base_dir / f"{category}_{index}.glb"
        asset_path.write_bytes(b"glTF")
        rows.append({
            "asset_id": f"{category}_{index}",
            "category": category,
            "glb_path": str(asset_path),
            "thumbnail_path": str(asset_path),
            "caption": f"{category} asset",
            "tags": [category],
            "width_m": 1.0,
            "depth_m": 1.0,
            "height_m": 1.0,
            "quality_score": 1.0,
        })
    return rows


def _setup_fake_retrieval(monkeypatch: pytest.MonkeyPatch, asset_ids: list[str]) -> None:
    class FakeEmbedder:
        dim = 4

        @classmethod
        def load(cls, *args, **kwargs):
            return cls()

        def encode_texts(self, texts):
            import numpy as np
            return np.ones((len(texts), self.dim), dtype="float32")

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


def _build_config() -> StreetComposeConfig:
    return StreetComposeConfig(
        query="campus gateway boulevard",
        length_m=96.0,
        road_width_m=10.5,
        sidewalk_width_m=3.0,
        lane_count=3,
        density=1.0,
        seed=29,
        topk_per_category=20,
        max_trials_per_slot=20,
        layout_mode="graph_template",
        constraint_mode="off",
        surrounding_building_mode="footprint_based",
        curated_street_assets_profile="disabled",
    )


def test_compose_street_scene_supports_graph_template_corridor_export(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    pytest.importorskip("shapely")

    rows = _build_real_rows(tmp_path / "data")
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])
    monkeypatch.setattr(street_layout, "render_presentation_views", lambda *args, **kwargs: [])

    config = _build_config()
    bridge = build_graph_template_scene_bridge(config, template_id="hkust_gz_gate")
    result = compose_street_scene(
        config=config,
        manifest_path=manifest,
        artifacts_dir=tmp_path / "artifacts",
        model_name="openai/clip-vit-base-patch32",
        model_dir=None,
        local_files_only=True,
        device="cpu",
        export_format="glb",
        out_dir=tmp_path / "artifacts" / "scene",
        road_segment_graph_override=bridge.road_segment_graph,
        projected_features_override=bridge.projected_features,
        placement_context_override=bridge.placement_context,
    )

    layout_path = Path(result.outputs["scene_layout"])
    payload = json.loads(layout_path.read_text(encoding="utf-8"))
    summary = payload["summary"]

    assert layout_path.exists()
    assert summary["layout_mode"] == "graph_template"
    assert summary["road_segment_graph_summary"]["segment_count"] > 0
