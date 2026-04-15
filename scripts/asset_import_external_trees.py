#!/usr/bin/env python3
"""Import external real tree assets into the scene-ready real asset library."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts import m3_04_clean_asset_manifest as manifest_cleaner
from scripts import m3_05_seed_production_parametric_assets as production_seed
from scripts.m2_10_ingest_assets import (
    _load_as_filtered_scene,
    _load_mesh_as_single_mesh,
    normalize_grounded_mesh,
    normalize_grounded_scene,
    scene_to_merged_mesh,
    validate_tree_upright,
)


REQUIRED_INPUT_FIELDS = (
    "asset_id",
    "category",
    "text_desc",
    "mesh_path",
    "license",
    "source",
    "split",
)


def _parse_rotation_deg_xyz(value: object) -> tuple[float, float, float] | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",") if part.strip()]
    else:
        parts = [str(part).strip() for part in value]
    if len(parts) != 3:
        raise ValueError("import_rotation_deg_xyz must contain exactly 3 values")
    return tuple(float(part) for part in parts)


def _load_input_rows(input_manifest: Path) -> List[Dict[str, Any]]:
    if not input_manifest.exists():
        raise FileNotFoundError(f"tree input manifest not found: {input_manifest}")
    rows: List[Dict[str, Any]] = []
    for line_no, line in enumerate(input_manifest.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        missing = [field for field in REQUIRED_INPUT_FIELDS if str(row.get(field, "")).strip() == ""]
        if missing:
            raise ValueError(f"invalid tree manifest row at line {line_no}: missing {', '.join(missing)}")
        if str(row.get("category", "")).strip().lower() != "tree":
            raise ValueError(f"invalid tree manifest row at line {line_no}: category must be 'tree'")
        split = str(row.get("split", "")).strip().lower()
        if split not in {"train", "val", "test"}:
            raise ValueError(f"invalid tree manifest row at line {line_no}: split must be train|val|test")
        parsed = dict(row)
        parsed["split"] = split
        parsed["import_rotation_deg_xyz"] = _parse_rotation_deg_xyz(row.get("import_rotation_deg_xyz"))
        rows.append(parsed)
    return rows


def _write_placeholder_latent(latent_path: Path, mesh_path: Path) -> None:
    latent_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import torch
    except ImportError:
        latent_path.write_text(json.dumps({"mesh_path": str(mesh_path)}, ensure_ascii=True), encoding="utf-8")
        return
    torch.save({"mesh_path": str(mesh_path)}, latent_path)


def _mesh_face_count(mesh_or_scene: object) -> int:
    try:
        import trimesh
    except ImportError:
        return int(len(getattr(mesh_or_scene, "faces", ())))
    if isinstance(mesh_or_scene, trimesh.Scene):
        return sum(int(len(getattr(g, "faces", ()))) for g in mesh_or_scene.geometry.values())
    return int(len(getattr(mesh_or_scene, "faces", ())))


def _validate_tree_for_scene_import(mesh: object) -> tuple[bool, Dict[str, Any]]:
    is_upright, diagnostics = validate_tree_upright(mesh)
    if is_upright:
        out = dict(diagnostics)
        out["validation_mode"] = "trunk_axis"
        return True, out

    bounds = getattr(mesh, "bounds", None)
    if bounds is None:
        return False, dict(diagnostics)
    span = bounds[1] - bounds[0]
    width = float(max(span[0], 0.0))
    height = float(max(span[1], 0.0))
    depth = float(max(span[2], 0.0))
    dominant_horizontal = max(width, depth)
    if (
        float(bounds[0][1]) >= -1e-3
        and dominant_horizontal > 1e-6
        and height >= dominant_horizontal * 1.2
    ):
        relaxed = dict(diagnostics)
        relaxed["failure_reason"] = ""
        relaxed["validation_mode"] = "overall_upright_fallback"
        relaxed["fallback_threshold_ratio"] = 1.2
        return True, relaxed
    return False, dict(diagnostics)


def _tree_manifest_row(
    *,
    source_row: Mapping[str, Any],
    mesh_path: Path,
    latent_path: Path,
    face_count: int,
    upright_diagnostics: Mapping[str, Any],
) -> Dict[str, Any]:
    quality_metrics = {
        "face_count": int(face_count),
        "tree_upright_validation": dict(upright_diagnostics),
    }
    row: Dict[str, Any] = {
        "asset_id": str(source_row["asset_id"]).strip(),
        "category": "tree",
        "text_desc": str(source_row["text_desc"]).strip(),
        "mesh_path": str(mesh_path),
        "latent_path": str(latent_path),
        "license": str(source_row["license"]).strip(),
        "source": str(source_row["source"]).strip(),
        "split": str(source_row["split"]).strip().lower(),
        "asset_role": "street_furniture",
        "scene_eligible": True,
        "mesh_face_count": int(face_count),
        "quality_metrics": quality_metrics,
        "quality_notes": ["tree_upright_validated"],
    }
    passthrough_fields = (
        "generator_type",
        "runtime_profile",
        "style_tags",
        "material_family",
        "theme_tags",
        "hero_asset",
        "avoid_with_presets",
        "frontage_width_m",
        "depth_m",
        "tags",
        "objaverse_uid",
        "objaverse_uri",
        "objaverse_viewer_url",
        "objaverse_thumbnail_url",
        "objaverse_lvis_category",
        "objaverse_score",
        "objaverse_reasons",
    )
    for field in passthrough_fields:
        if field in source_row and source_row[field] is not None:
            row[field] = source_row[field]
    return row


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


def import_external_tree_assets(
    *,
    input_manifest: Path,
    output_manifest: Path,
    mesh_out_dir: Path,
    latents_dir: Path,
    artifacts_dir: Path,
    model_name: str = "openai/clip-vit-base-patch32",
    model_dir: Path | None = None,
    local_files_only: bool = False,
    device: str = "cpu",
    rebuild_index_enabled: bool = True,
) -> Dict[str, Any]:
    input_manifest = input_manifest.resolve()
    output_manifest = output_manifest.resolve()
    mesh_out_dir = mesh_out_dir.resolve()
    latents_dir = latents_dir.resolve()
    artifacts_dir = artifacts_dir.resolve()
    mesh_out_dir.mkdir(parents=True, exist_ok=True)
    latents_dir.mkdir(parents=True, exist_ok=True)
    output_manifest.parent.mkdir(parents=True, exist_ok=True)

    input_rows = _load_input_rows(input_manifest)
    imported_rows: List[Dict[str, Any]] = []
    imported_asset_ids: List[str] = []
    skipped_asset_ids: List[str] = []

    for row in input_rows:
        asset_id = str(row["asset_id"]).strip()
        source_mesh_path = Path(str(row["mesh_path"])).expanduser()
        if not source_mesh_path.is_absolute():
            source_mesh_path = (input_manifest.parent / source_mesh_path).resolve()
        if not source_mesh_path.exists():
            print(f"[skip] tree mesh for asset '{asset_id}' not found: {source_mesh_path}", file=sys.stderr)
            skipped_asset_ids.append(asset_id)
            continue

        # --- validation path (lossy merge) -----------------------------------
        mesh = _load_mesh_as_single_mesh(source_mesh_path)
        mesh = normalize_grounded_mesh(mesh, rotation_deg_xyz=row.get("import_rotation_deg_xyz"))
        is_upright, diagnostics = _validate_tree_for_scene_import(mesh)
        if not is_upright:
            print(
                f"[skip] tree asset '{asset_id}' failed upright validation: "
                f"{json.dumps(diagnostics, ensure_ascii=True, sort_keys=True)}",
                file=sys.stderr,
            )
            skipped_asset_ids.append(asset_id)
            continue

        # --- export path (Scene with PBR materials preserved) ----------------
        scene = _load_as_filtered_scene(source_mesh_path)
        scene = normalize_grounded_scene(scene, rotation_deg_xyz=row.get("import_rotation_deg_xyz"))

        target_mesh_path = (mesh_out_dir / f"{asset_id}.glb").resolve()
        target_latent_path = (latents_dir / f"{asset_id}.pt").resolve()
        scene.export(target_mesh_path)
        _write_placeholder_latent(target_latent_path, target_mesh_path)

        face_count = _mesh_face_count(scene)
        imported_rows.append(
            _tree_manifest_row(
                source_row=row,
                mesh_path=target_mesh_path,
                latent_path=target_latent_path,
                face_count=face_count,
                upright_diagnostics=diagnostics,
            )
        )
        imported_asset_ids.append(asset_id)

    existing_rows = manifest_cleaner._load_rows(output_manifest) if output_manifest.exists() else []
    merged_rows = _upsert_rows(existing_rows, imported_rows)
    cleaned_rows = manifest_cleaner.clean_manifest_rows(merged_rows, output_manifest.parent.resolve())
    manifest_cleaner._write_rows(output_manifest, cleaned_rows)

    index_summary: Dict[str, Any] | None = None
    if rebuild_index_enabled:
        index_summary = production_seed.rebuild_real_index(
            manifest_path=output_manifest,
            artifacts_dir=artifacts_dir,
            model_name=model_name,
            model_dir=model_dir,
            local_files_only=local_files_only,
            device=device,
        )

    return {
        "imported_asset_ids": imported_asset_ids,
        "skipped_asset_ids": skipped_asset_ids,
        "output_manifest": str(output_manifest),
        "manifest_summary": manifest_cleaner.summarize_rows(cleaned_rows),
        "rebuild_index": bool(rebuild_index_enabled),
        "index_summary": index_summary or {},
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import external real tree assets into the scene-ready manifest.")
    parser.add_argument("--input-manifest", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, default=Path("data/real/real_assets_manifest.jsonl"))
    parser.add_argument("--mesh-out-dir", type=Path, default=Path("data/real/meshes"))
    parser.add_argument("--latents-dir", type=Path, default=Path("data/real/latents"))
    parser.add_argument("--artifacts-dir", type=Path, default=Path("artifacts/real"))
    parser.add_argument("--model-name", default="openai/clip-vit-base-patch32")
    parser.add_argument("--model-dir", type=Path, default=None)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--rebuild-index", dest="rebuild_index", action="store_true")
    parser.add_argument("--no-rebuild-index", dest="rebuild_index", action="store_false")
    parser.set_defaults(rebuild_index=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        summary = import_external_tree_assets(
            input_manifest=args.input_manifest,
            output_manifest=args.output_manifest,
            mesh_out_dir=args.mesh_out_dir,
            latents_dir=args.latents_dir,
            artifacts_dir=args.artifacts_dir,
            model_name=args.model_name,
            model_dir=args.model_dir,
            local_files_only=bool(args.local_files_only),
            device=str(args.device),
            rebuild_index_enabled=bool(args.rebuild_index),
        )
    except Exception as exc:
        print(f"External tree import failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
