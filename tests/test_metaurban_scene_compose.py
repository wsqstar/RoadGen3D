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

from roadgen3d.metaurban_scene_bridge import build_metaurban_scene_bridge
from roadgen3d.types import RetrievalHit, StreetComposeConfig
from roadgen3d.street_layout import compose_street_scene
import roadgen3d.street_layout as street_layout


def _make_mesh(path: Path, kind: str = "box") -> None:
    trimesh = pytest.importorskip("trimesh")
    if kind == "cylinder":
        mesh = trimesh.creation.cylinder(radius=0.1, height=1.5, sections=16)
    else:
        mesh = trimesh.creation.box(extents=(0.8, 0.5, 0.5))
    path.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(path)


def _write_manifest(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _build_real_rows(base_dir: Path, *, include_buildings: bool = False) -> list[dict[str, object]]:
    categories = [
        ("bench_01", "bench"),
        ("lamp_01", "lamp"),
        ("trash_01", "trash"),
        ("tree_01", "tree"),
        ("bus_stop_01", "bus_stop"),
        ("mailbox_01", "mailbox"),
        ("hydrant_01", "hydrant"),
        ("bollard_01", "bollard"),
    ]
    rows: list[dict[str, object]] = []
    for asset_id, category in categories:
        mesh_path = base_dir / "meshes" / f"{asset_id}.glb"
        _make_mesh(mesh_path, kind="cylinder" if category in {"lamp", "tree"} else "box")
        rows.append(
            {
                "asset_id": asset_id,
                "category": category,
                "text_desc": f"a roadside {category}",
                "mesh_path": str(mesh_path),
                "latent_path": str(base_dir / "latents" / f"{asset_id}.pt"),
                "license": "cc-by",
                "source": "test",
                "split": "train",
            }
        )
    if include_buildings:
        for idx, asset_id in enumerate(("building_01", "building_02"), start=len(rows)):
            mesh_path = base_dir / "meshes" / f"{asset_id}.glb"
            _make_mesh(mesh_path, kind="box")
            rows.append(
                {
                    "asset_id": asset_id,
                    "category": "building",
                    "asset_role": "building",
                    "theme_tags": ["commercial", "transit", "medium"],
                    "frontage_width_m": 14.0 + idx,
                    "depth_m": 10.0,
                    "height_class": "midrise" if asset_id.endswith("01") else "highrise",
                    "text_desc": "a contemporary street-side building",
                    "mesh_path": str(mesh_path),
                    "latent_path": str(base_dir / "latents" / f"{asset_id}.pt"),
                    "license": "cc-by",
                    "source": "test",
                    "split": "train",
                }
            )
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
        layout_mode="metaurban",
        constraint_mode="off",
        surrounding_building_mode="footprint_based",
        curated_street_assets_profile="disabled",
    )


def test_compose_street_scene_supports_metaurban_corridor_export(tmp_path: Path, monkeypatch):
    pytest.importorskip("trimesh")
    pytest.importorskip("shapely")

    rows = _build_real_rows(tmp_path / "data", include_buildings=True)
    manifest = tmp_path / "data" / "real_assets_manifest.jsonl"
    _write_manifest(manifest, rows)
    _setup_fake_retrieval(monkeypatch, [str(row["asset_id"]) for row in rows])
    monkeypatch.setattr(street_layout, "render_presentation_views", lambda *args, **kwargs: [])

    config = _build_config()
    bridge = build_metaurban_scene_bridge(config, plan_id="hkust_gz_gate")
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
    glb_path = Path(result.outputs["scene_glb"])
    payload = json.loads(layout_path.read_text(encoding="utf-8"))
    summary = payload["summary"]

    assert layout_path.exists()
    assert glb_path.exists()
    assert summary["layout_mode"] == "metaurban"
    assert summary["building_generation_mode"] == "grid_growth"
    assert "fell back to grid_growth" in summary["building_generation_fallback_reason"]
    assert summary["road_segment_graph_summary"]["segment_count"] > 0
    assert summary["frontage_parcel_count"] > 0 or summary["building_summary"]["footprint_count"] > 0
