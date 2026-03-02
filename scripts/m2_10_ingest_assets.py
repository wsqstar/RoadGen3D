#!/usr/bin/env python3
"""Ingest and normalize real mesh assets into RoadGen3D manifest format."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Tuple

REQUIRED_FIELDS = (
    "asset_id",
    "category",
    "text_desc",
    "mesh_path",
    "latent_path",
    "license",
    "source",
    "split",
)


def validate_manifest_row(row: Dict[str, object]) -> List[str]:
    errors: List[str] = []
    for field in REQUIRED_FIELDS:
        value = row.get(field)
        if value is None or str(value).strip() == "":
            errors.append(f"missing field: {field}")
    split = str(row.get("split", "")).strip().lower()
    if split not in {"train", "val", "test"}:
        errors.append("split must be one of train|val|test")
    return errors


def load_manifest(path: Path) -> List[Dict[str, object]]:
    if not path.exists():
        raise FileNotFoundError(f"manifest not found: {path}")
    rows: List[Dict[str, object]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        errors = validate_manifest_row(payload)
        if errors:
            raise ValueError(f"invalid manifest row at line {line_no}: {', '.join(errors)}")
        rows.append(payload)
    return rows


def check_mesh_latent_pairs(rows: List[Dict[str, object]]) -> List[str]:
    errors: List[str] = []
    for row in rows:
        asset_id = str(row.get("asset_id", ""))
        mesh_path = Path(str(row.get("mesh_path", ""))).expanduser()
        latent_path = Path(str(row.get("latent_path", ""))).expanduser()
        if not mesh_path.is_absolute():
            mesh_path = mesh_path.resolve()
        if not latent_path.is_absolute():
            latent_path = latent_path.resolve()
        if not mesh_path.exists():
            errors.append(f"{asset_id}: mesh missing -> {mesh_path}")
        if not latent_path.exists():
            errors.append(f"{asset_id}: latent missing -> {latent_path}")
    return errors


def _load_mesh_as_single_mesh(mesh_path: Path):
    try:
        import trimesh
    except ImportError as exc:
        raise RuntimeError("`trimesh` is required for ingestion. Install requirements-m2.txt.") from exc

    mesh_or_scene = trimesh.load(mesh_path, force="scene")
    if isinstance(mesh_or_scene, trimesh.Scene):
        if not mesh_or_scene.geometry:
            raise ValueError(f"empty scene mesh: {mesh_path}")
        merged = trimesh.util.concatenate(tuple(mesh_or_scene.geometry.values()))
        return merged
    return mesh_or_scene


def _normalize_mesh(mesh):
    bbox = mesh.bounds
    center = bbox.mean(axis=0)
    span = bbox[1] - bbox[0]
    max_span = float(max(span.max(), 1e-6))
    mesh = mesh.copy()
    mesh.apply_translation(-center)
    mesh.apply_scale(1.0 / max_span)
    return mesh


def ingest_assets(
    input_manifest: Path,
    output_manifest: Path,
    mesh_out_dir: Path,
    normalize_mesh: bool = True,
) -> Tuple[int, Path]:
    rows = load_manifest(input_manifest)
    mesh_out_dir.mkdir(parents=True, exist_ok=True)
    output_manifest.parent.mkdir(parents=True, exist_ok=True)

    output_rows: List[Dict[str, object]] = []
    for row in rows:
        asset_id = str(row["asset_id"])
        mesh_path = Path(str(row["mesh_path"])).expanduser().resolve()
        if not mesh_path.exists():
            raise FileNotFoundError(f"mesh_path for asset '{asset_id}' not found: {mesh_path}")

        target_mesh_path = (mesh_out_dir / f"{asset_id}.glb").resolve()
        if normalize_mesh:
            mesh = _load_mesh_as_single_mesh(mesh_path)
            mesh = _normalize_mesh(mesh)
            mesh.export(target_mesh_path)
        else:
            shutil.copy2(mesh_path, target_mesh_path)

        latent_path = Path(str(row["latent_path"])).expanduser()
        if not latent_path.is_absolute():
            latent_path = (output_manifest.parent / latent_path).resolve()

        out = dict(row)
        out["mesh_path"] = str(target_mesh_path)
        out["latent_path"] = str(latent_path)
        out["split"] = str(out["split"]).lower()
        output_rows.append(out)

    with output_manifest.open("w", encoding="utf-8") as handle:
        for row in output_rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")
    return len(output_rows), output_manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest and normalize real assets manifest.")
    parser.add_argument("--input-manifest", type=Path, required=True, help="Raw input manifest (.jsonl).")
    parser.add_argument(
        "--output-manifest",
        type=Path,
        default=Path("data/real/real_assets_manifest.jsonl"),
        help="Normalized output manifest (.jsonl).",
    )
    parser.add_argument(
        "--mesh-out-dir",
        type=Path,
        default=Path("data/real/meshes"),
        help="Directory for normalized meshes.",
    )
    parser.add_argument(
        "--no-normalize-mesh",
        action="store_true",
        help="Disable centering/scaling normalization and copy mesh directly.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        count, path = ingest_assets(
            input_manifest=args.input_manifest,
            output_manifest=args.output_manifest,
            mesh_out_dir=args.mesh_out_dir,
            normalize_mesh=not args.no_normalize_mesh,
        )
    except Exception as exc:
        print(f"Ingest failed: {exc}", file=sys.stderr)
        return 1

    print(f"Ingested assets: {count}")
    print(f"Output manifest: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
