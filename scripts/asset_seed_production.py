#!/usr/bin/env python3
"""Seed canonical production parametric bench/lamp assets into the real asset library."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.embedder import ClipTextEmbedder
from roadgen3d.index_store import FaissIndexStore
from roadgen3d.parametric_assets import GenerationRequest, generate_parametric_asset
from scripts import m2_12_build_real_index as build_real_index
from scripts import m3_04_clean_asset_manifest as manifest_cleaner


PRODUCTION_ASSET_SPECS: Sequence[Dict[str, Any]] = (
    {
        "asset_id": "bench_modern_production",
        "asset_kind": "bench",
        "text_desc": "parametric production modern metal-wood bench with dual-frame supports",
        "params": {
            "width_m": 1.8,
            "depth_m": 0.55,
            "seat_height_m": 0.45,
            "backrest_height_m": 0.35,
            "backrest_angle_deg": 12.0,
            "leg_type": "dual_frame",
            "armrest_enabled": False,
            "slat_count": 5,
            "material_family": "metal_wood",
            "style_tag": "modern",
            "detail_level": 3,
        },
    },
    {
        "asset_id": "bench_nordic_production",
        "asset_kind": "bench",
        "text_desc": "parametric production nordic wood bench with dual-frame supports",
        "params": {
            "width_m": 2.0,
            "depth_m": 0.55,
            "seat_height_m": 0.45,
            "backrest_height_m": 0.35,
            "backrest_angle_deg": 12.0,
            "leg_type": "dual_frame",
            "armrest_enabled": False,
            "slat_count": 5,
            "material_family": "wood",
            "style_tag": "nordic",
            "detail_level": 3,
        },
    },
    {
        "asset_id": "lamp_modern_production",
        "asset_kind": "lamp",
        "text_desc": "parametric production modern metal street lamp with flat LED luminaire",
        "params": {
            "pole_height_m": 5.0,
            "pole_radius_m": 0.06,
            "base_diameter_m": 0.35,
            "arm_length_m": 0.8,
            "luminaire_type": "flat_led",
            "single_or_double_arm": "single",
            "light_direction": "roadside",
            "material_family": "metal",
            "style_tag": "modern",
            "detail_level": 3,
        },
    },
    {
        "asset_id": "lamp_victorian_production",
        "asset_kind": "lamp",
        "text_desc": "parametric production victorian cast-iron lamp with double globe luminaires",
        "params": {
            "pole_height_m": 4.8,
            "pole_radius_m": 0.07,
            "base_diameter_m": 0.40,
            "arm_length_m": 0.70,
            "luminaire_type": "globe",
            "single_or_double_arm": "double",
            "light_direction": "bidirectional",
            "material_family": "cast_iron",
            "style_tag": "victorian",
            "detail_level": 3,
        },
    },
)


def _load_manifest_rows(manifest_path: Path) -> List[Dict[str, Any]]:
    if not manifest_path.exists():
        return []
    return manifest_cleaner._load_rows(manifest_path)


def _write_manifest_rows(manifest_path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    manifest_cleaner._write_rows(manifest_path, rows)


def _write_placeholder_latent(latent_path: Path, mesh_path: Path) -> None:
    latent_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import torch
    except ImportError:
        latent_path.write_text(json.dumps({"mesh_path": str(mesh_path)}, ensure_ascii=True), encoding="utf-8")
        return
    torch.save({"mesh_path": str(mesh_path)}, latent_path)


def _manifest_row(
    *,
    asset_id: str,
    text_desc: str,
    mesh_path: Path,
    latent_path: Path,
    result_meta: Mapping[str, Any],
) -> Dict[str, Any]:
    bbox = result_meta.get("bbox", {}).get("size_xyz", [0.0, 0.0, 0.0])
    return {
        "asset_id": asset_id,
        "category": str(result_meta.get("asset_kind", "")).strip(),
        "text_desc": text_desc,
        "mesh_path": str(mesh_path),
        "latent_path": str(latent_path),
        "source": "parametric_generated",
        "generator_type": str(result_meta.get("generator_type", "parametric_v1")).strip(),
        "runtime_profile": str(result_meta.get("runtime_profile", "production")).strip(),
        "style_tags": list(result_meta.get("style_tags", []) or []),
        "material_family": str(result_meta.get("material_family", "")).strip(),
        "parameter_snapshot": dict(result_meta.get("parameter_snapshot", {}) or {}),
        "quality_metrics": dict(result_meta.get("quality_metrics", {}) or {}),
        "frontage_width_m": float(bbox[0]) if len(bbox) >= 1 else 0.0,
        "depth_m": float(bbox[2]) if len(bbox) >= 3 else 0.0,
        "asset_role": "street_furniture",
    }


def _upsert_rows(existing_rows: Sequence[Mapping[str, Any]], generated_rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    replacements = {str(row["asset_id"]).strip(): dict(row) for row in generated_rows}
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in existing_rows:
        asset_id = str(row.get("asset_id", "")).strip()
        if not asset_id:
            continue
        if asset_id in replacements:
            merged.append(dict(replacements[asset_id]))
            seen.add(asset_id)
        else:
            merged.append(dict(row))
    for row in generated_rows:
        asset_id = str(row["asset_id"]).strip()
        if asset_id not in seen:
            merged.append(dict(row))
    return merged


def rebuild_real_index(
    *,
    manifest_path: Path,
    artifacts_dir: Path,
    model_name: str,
    model_dir: Path | None,
    local_files_only: bool,
    device: str,
) -> Dict[str, Any]:
    rows = build_real_index.load_real_manifest(manifest_path)
    if not rows:
        raise ValueError(f"real manifest is empty: {manifest_path}")
    descriptions = [str(row["text_desc"]) for row in rows]
    asset_ids = [str(row["asset_id"]) for row in rows]

    embedder = ClipTextEmbedder(
        model_name=model_name,
        model_dir=model_dir,
        local_files_only=local_files_only,
        device=device,
    )
    embeddings = embedder.encode_texts(descriptions)

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    np.save(artifacts_dir / "asset_text_embeds.npy", embeddings)
    (artifacts_dir / "asset_ids.json").write_text(json.dumps(asset_ids, indent=2, ensure_ascii=True), encoding="utf-8")
    (artifacts_dir / "embed_meta.json").write_text(
        json.dumps(
            {
                "num_assets": len(asset_ids),
                "embedding_dim": int(embeddings.shape[1]),
                "model_source": embedder.model_source,
                "projection_dim": int(embedder.projection_dim),
                "local_files_only": bool(local_files_only),
            },
            indent=2,
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    store = FaissIndexStore.build(embeddings=embeddings, asset_ids=asset_ids)
    store.save(index_path=artifacts_dir / "index_ip.faiss", id_map_path=artifacts_dir / "id_map.json")
    assets_pipeline_path = build_real_index.write_assets_for_pipeline(rows=rows, out_path=artifacts_dir / "real_assets_for_pipeline.jsonl")
    return {
        "asset_count": int(len(asset_ids)),
        "embedding_dim": int(embeddings.shape[1]),
        "assets_pipeline_path": str(assets_pipeline_path),
    }


def seed_production_assets(
    *,
    manifest_path: Path,
    mesh_dir: Path,
    latents_dir: Path,
    metadata_dir: Path,
    artifacts_dir: Path,
    device: str = "cpu",
    rebuild_index_enabled: bool = True,
    model_name: str = "openai/clip-vit-base-patch32",
    model_dir: Path | None = None,
    local_files_only: bool = False,
) -> Dict[str, Any]:
    manifest_path = manifest_path.resolve()
    mesh_dir = mesh_dir.resolve()
    latents_dir = latents_dir.resolve()
    metadata_dir = metadata_dir.resolve()
    artifacts_dir = artifacts_dir.resolve()
    mesh_dir.mkdir(parents=True, exist_ok=True)
    latents_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    existing_rows = _load_manifest_rows(manifest_path)
    generated_rows: List[Dict[str, Any]] = []
    generated_asset_ids: List[str] = []

    for spec in PRODUCTION_ASSET_SPECS:
        asset_id = str(spec["asset_id"]).strip()
        request = GenerationRequest(
            asset_kind=str(spec["asset_kind"]).strip().lower(),
            runtime_profile="production",
            device_backend=str(device).strip().lower() or "cpu",
            params=dict(spec["params"]),
        )
        result = generate_parametric_asset(request)
        result_meta = result.to_metadata()

        mesh_path = mesh_dir / f"{asset_id}.glb"
        latent_path = latents_dir / f"{asset_id}.pt"
        result_json_path = metadata_dir / f"{asset_id}.result.json"
        result.mesh.export(str(mesh_path))
        _write_placeholder_latent(latent_path, mesh_path)

        result_payload = {
            **dict(result_meta),
            "asset_id": asset_id,
            "text_desc": str(spec["text_desc"]),
            "mesh_path": str(mesh_path),
            "latent_path": str(latent_path),
        }
        result_json_path.write_text(json.dumps(result_payload, indent=2, ensure_ascii=True), encoding="utf-8")
        generated_rows.append(
            _manifest_row(
                asset_id=asset_id,
                text_desc=str(spec["text_desc"]),
                mesh_path=mesh_path,
                latent_path=latent_path,
                result_meta=result_payload,
            )
        )
        generated_asset_ids.append(asset_id)

    merged_rows = _upsert_rows(existing_rows, generated_rows)
    cleaned_rows = manifest_cleaner.clean_manifest_rows(merged_rows, manifest_path.parent.resolve())
    _write_manifest_rows(manifest_path, cleaned_rows)

    index_summary: Dict[str, Any] | None = None
    if rebuild_index_enabled:
        index_summary = rebuild_real_index(
            manifest_path=manifest_path,
            artifacts_dir=artifacts_dir,
            model_name=model_name,
            model_dir=model_dir,
            local_files_only=local_files_only,
            device=device,
        )

    return {
        "generated_asset_ids": generated_asset_ids,
        "manifest_path": str(manifest_path),
        "manifest_summary": manifest_cleaner.summarize_rows(cleaned_rows),
        "rebuild_index": bool(rebuild_index_enabled),
        "index_summary": index_summary or {},
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed production parametric bench/lamp assets into the real manifest.")
    parser.add_argument("--manifest", type=Path, default=Path("data/real/real_assets_manifest.jsonl"))
    parser.add_argument("--mesh-dir", type=Path, default=Path("data/real/meshes"))
    parser.add_argument("--latents-dir", type=Path, default=Path("data/real/latents"))
    parser.add_argument("--metadata-dir", type=Path, default=Path("artifacts/real/parametric_production"))
    parser.add_argument("--artifacts-dir", type=Path, default=Path("artifacts/real"))
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--model-name", default="openai/clip-vit-base-patch32")
    parser.add_argument("--model-dir", type=Path, default=None)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--rebuild-index", dest="rebuild_index", action="store_true")
    parser.add_argument("--no-rebuild-index", dest="rebuild_index", action="store_false")
    parser.set_defaults(rebuild_index=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    summary = seed_production_assets(
        manifest_path=args.manifest,
        mesh_dir=args.mesh_dir,
        latents_dir=args.latents_dir,
        metadata_dir=args.metadata_dir,
        artifacts_dir=args.artifacts_dir,
        device=args.device,
        rebuild_index_enabled=bool(args.rebuild_index),
        model_name=args.model_name,
        model_dir=args.model_dir,
        local_files_only=bool(args.local_files_only),
    )
    print(json.dumps(summary, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
