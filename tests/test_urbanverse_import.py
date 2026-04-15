from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.urbanverse_import import run_urbanverse_subset_import  # noqa: E402


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, ensure_ascii=True) for row in rows)
    if text:
        text += "\n"
    path.write_text(text, encoding="utf-8")


def _write_mesh(path: Path, *, extents: tuple[float, float, float], shape: str = "box") -> None:
    import trimesh

    path.parent.mkdir(parents=True, exist_ok=True)
    if shape == "cylinder":
        mesh = trimesh.creation.cylinder(radius=float(extents[0]) / 2.0, height=float(extents[1]), sections=24)
        mesh.apply_translation([0.0, float(extents[1]) / 2.0, 0.0])
    else:
        mesh = trimesh.creation.box(extents=extents)
    mesh.export(path)


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_urbanverse_import_writes_object_ground_and_sky_manifests(tmp_path: Path):
    input_root = tmp_path / "input"
    metadata_dir = input_root / "metadata"
    assets_dir = input_root / "assets"
    _write_mesh(assets_dir / "bench.glb", extents=(2.0, 1.0, 0.8))
    _write_mesh(assets_dir / "tree.glb", extents=(0.4, 3.0, 0.6))
    _write_mesh(assets_dir / "bus_stop.glb", extents=(2.0, 2.0, 2.0))
    (assets_dir / "bench.png").write_text("img", encoding="utf-8")
    (assets_dir / "bench.npy").write_bytes(b"embed")
    (assets_dir / "road.png").write_text("road", encoding="utf-8")
    (assets_dir / "road_normal.png").write_text("road-normal", encoding="utf-8")
    (assets_dir / "road_preview.png").write_text("road-preview", encoding="utf-8")
    (assets_dir / "sky.hdr").write_text("sky", encoding="utf-8")
    (assets_dir / "sky_preview.png").write_text("sky-preview", encoding="utf-8")

    _write_jsonl(
        metadata_dir / "objects.jsonl",
        [
            {
                "uid": "bench-001",
                "category": "public_bench",
                "description": "Modern public bench for a walkable street",
                "mesh_path": "assets/bench.glb",
                "thumbnail_path": "assets/bench.png",
                "appearance_embedding_path": "assets/bench.npy",
                "bbox": {"size_xyz": [2.0, 1.0, 0.8]},
                "license": "cc-by-4.0",
            },
            {
                "uid": "mail-001",
                "category": "street_object",
                "name": "Blue mailbox on sidewalk",
                "mesh_path": "assets/bench.glb",
                "dimensions": {"width_m": 0.4, "depth_m": 0.3, "height_m": 1.2},
                "license": "cc-by-4.0",
                "tags": ["mailbox", "street"],
            },
            {
                "uid": "tree-001",
                "category": "evergreen_tree",
                "description": "Street tree for urban boulevard",
                "mesh_path": "assets/tree.glb",
                "license": "cc-by-4.0",
            },
            {
                "uid": "bus-001",
                "category": "bus_stop",
                "description": "Bus stop shelter",
                "mesh_path": "assets/bus_stop.glb",
                "license": "cc-by-4.0",
            },
            {
                "uid": "mystery-001",
                "category": "sculpture",
                "description": "Public art installation",
                "mesh_path": "assets/bench.glb",
                "license": "cc-by-4.0",
            },
        ],
    )
    _write_jsonl(
        metadata_dir / "ground_materials.jsonl",
        [
            {
                "uid": "road-001",
                "surface_type": "road",
                "albedo_path": "assets/road.png",
                "normal_path": "assets/road_normal.png",
                "preview_path": "assets/road_preview.png",
                "style_tags": ["urban"],
                "license": "cc-by-4.0",
            }
        ],
    )
    _write_jsonl(
        metadata_dir / "skies.jsonl",
        [
            {
                "uid": "sky-001",
                "time_of_day": "sunset",
                "hdri_path": "assets/sky.hdr",
                "preview_path": "assets/sky_preview.png",
                "license": "cc-by-4.0",
                "illumination_tags": ["warm"],
            }
        ],
    )

    report = run_urbanverse_subset_import(
        input_root=input_root,
        subset_name="demo-subset",
        output_root=tmp_path / "output",
        cache_root=tmp_path / "cache",
    )

    object_rows = _load_jsonl(tmp_path / "output" / "object_assets_manifest_v2.jsonl")
    ground_rows = _load_jsonl(tmp_path / "output" / "ground_material_manifest.jsonl")
    sky_rows = _load_jsonl(tmp_path / "output" / "sky_manifest.jsonl")
    unmapped_rows = _load_jsonl(tmp_path / "output" / "unmapped_objects.jsonl")
    skipped_rows = _load_jsonl(tmp_path / "output" / "skipped_rows.jsonl")

    assert report["imported_counts"]["objects"] == 3
    assert report["imported_counts"]["ground_materials"] == 1
    assert report["imported_counts"]["skies"] == 1
    assert report["report_only_category_counts"]["bus_stop"] == 1
    assert report["unmapped_counts"]["objects"] == 1
    assert len(object_rows) == 3
    assert {row["category"] for row in object_rows} == {"bench", "mailbox", "tree"}
    assert object_rows[0]["asset_id"].startswith("urbanverse_")
    assert any("tree_upright_validated" in row.get("quality_notes", []) for row in object_rows if row["category"] == "tree")
    assert any(Path(str(row["latent_path"])).exists() for row in object_rows)
    assert ground_rows[0]["surface_type"] == "carriageway"
    assert sky_rows[0]["time_of_day"] == "evening"
    assert unmapped_rows[0]["reason"] == "no_supported_category_mapping"
    assert any(row["reason"] == "unsupported_category_v1" for row in skipped_rows)


def test_urbanverse_import_skips_missing_optional_stage_manifests(tmp_path: Path):
    input_root = tmp_path / "input"
    metadata_dir = input_root / "metadata"
    assets_dir = input_root / "assets"
    _write_mesh(assets_dir / "bench.glb", extents=(2.0, 1.0, 0.8))
    _write_jsonl(
        metadata_dir / "objects.jsonl",
        [
            {
                "uid": "bench-001",
                "category": "bench",
                "description": "Bench asset",
                "mesh_path": "assets/bench.glb",
                "license": "cc-by-4.0",
            }
        ],
    )

    report = run_urbanverse_subset_import(
        input_root=input_root,
        subset_name="objects-only",
        output_root=tmp_path / "output",
        cache_root=tmp_path / "cache",
    )

    assert report["stage_statuses"]["ground_materials"]["status"] == "missing_input_manifest"
    assert report["stage_statuses"]["skies"]["status"] == "missing_input_manifest"
    assert report["imported_counts"]["objects"] == 1
    assert report["imported_counts"]["ground_materials"] == 0
    assert report["imported_counts"]["skies"] == 0


def test_urbanverse_import_append_is_idempotent_and_tree_failures_are_reported(tmp_path: Path, monkeypatch):
    from scripts import asset_seed_production as production_seed

    monkeypatch.setattr(
        production_seed,
        "rebuild_real_index",
        lambda **kwargs: {"asset_count": 1, "embedding_dim": 512},
    )

    input_root = tmp_path / "input"
    metadata_dir = input_root / "metadata"
    assets_dir = input_root / "assets"
    _write_mesh(assets_dir / "bench.glb", extents=(2.0, 1.0, 0.8))
    _write_mesh(assets_dir / "flat_tree.glb", extents=(2.0, 1.0, 2.0))
    _write_jsonl(
        metadata_dir / "objects.jsonl",
        [
            {
                "uid": "bench-001",
                "category": "bench",
                "description": "Bench asset",
                "mesh_path": "assets/bench.glb",
                "license": "cc-by-4.0",
            },
            {
                "uid": "tree-bad",
                "category": "tree",
                "description": "Tree that should fail upright validation",
                "mesh_path": "assets/flat_tree.glb",
                "license": "cc-by-4.0",
            },
        ],
    )

    append_manifest = tmp_path / "real_assets_manifest_v2.jsonl"
    _write_jsonl(
        append_manifest,
        [
            {
                "asset_id": "existing_asset",
                "source_dataset": "demo",
                "source_uid": "existing_asset",
                "category": "bench",
                "text_desc": "existing",
                "mesh_path": "/tmp/existing.glb",
                "latent_path": "/tmp/existing.pt",
                "license": "cc-by-4.0",
                "split": "train",
            }
        ],
    )

    first_report = run_urbanverse_subset_import(
        input_root=input_root,
        subset_name="append-demo",
        output_root=tmp_path / "output1",
        cache_root=tmp_path / "cache1",
        append_object_manifest=append_manifest,
        rebuild_index=True,
    )
    second_report = run_urbanverse_subset_import(
        input_root=input_root,
        subset_name="append-demo",
        output_root=tmp_path / "output2",
        cache_root=tmp_path / "cache2",
        append_object_manifest=append_manifest,
        rebuild_index=True,
    )

    appended_rows = _load_jsonl(append_manifest)
    assert first_report["rebuild_index"] is True
    assert first_report["skipped_counts"]["by_reason"]["tree_validation_failed"] == 1
    assert len(appended_rows) == 2
    assert second_report["appended_counts"]["objects"] == 0
    assert sorted(row["asset_id"] for row in appended_rows) == ["existing_asset", "urbanverse_bench_bench_001"]
