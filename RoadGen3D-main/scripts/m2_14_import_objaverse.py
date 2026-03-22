#!/usr/bin/env python3
"""Select, cache, and materialize Objaverse assets for RoadGen3D."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.objaverse_import import (
    append_manifest_rows,
    import_objaverse_assets,
    recommended_default_categories,
    write_manifest_rows,
    write_report_json,
)
from scripts import m3_05_seed_production_parametric_assets as production_seed
from scripts import m3_04_clean_asset_manifest as manifest_cleaner


def _parse_categories(value: str | Sequence[str] | None) -> List[str]:
    if value is None:
        return list(recommended_default_categories())
    if isinstance(value, str):
        items = value.split(",")
    else:
        items = list(value)
    categories = [str(item).strip().lower() for item in items if str(item).strip()]
    return categories or list(recommended_default_categories())


def _write_placeholder_latent(latent_path: Path, mesh_path: Path) -> None:
    latent_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import torch
    except ImportError:
        latent_path.write_text(json.dumps({"mesh_path": str(mesh_path)}, ensure_ascii=True), encoding="utf-8")
        return
    torch.save({"mesh_path": str(mesh_path)}, latent_path)


def _ensure_placeholder_latents(rows: Sequence[Dict[str, Any]]) -> int:
    created = 0
    for row in rows:
        latent_path = Path(str(row.get("latent_path", "") or "")).expanduser()
        mesh_path = Path(str(row.get("mesh_path", "") or "")).expanduser()
        if not latent_path or not mesh_path:
            continue
        if latent_path.exists():
            continue
        _write_placeholder_latent(latent_path.resolve(), mesh_path.resolve())
        created += 1
    return int(created)


def run_objaverse_import(
    *,
    cache_root: Path,
    output_manifest: Path,
    latents_dir: Path,
    requested_categories: Sequence[str],
    max_per_category: int,
    download_processes: int,
    split: str,
    clean_manifest: bool,
    report_out: Path | None,
    append_manifest: Path | None,
    rebuild_index: bool,
    artifacts_dir: Path,
    model_name: str,
    model_dir: Path | None,
    local_files_only: bool,
    device: str,
) -> Dict[str, Any]:
    output_manifest = output_manifest.expanduser().resolve()
    cache_root = cache_root.expanduser().resolve()
    latents_dir = latents_dir.expanduser().resolve()
    result = import_objaverse_assets(
        cache_root=cache_root,
        latents_dir=latents_dir,
        requested_categories=requested_categories,
        max_per_category=int(max_per_category),
        download_processes=int(download_processes),
        split=split,
    )

    manifest_rows = list(result.manifest_rows)
    if clean_manifest:
        manifest_rows = manifest_cleaner.clean_manifest_rows(manifest_rows, output_manifest.parent.resolve())
    placeholder_latent_count = _ensure_placeholder_latents(manifest_rows)
    write_manifest_rows(output_manifest, manifest_rows)

    appended_count = 0
    if append_manifest is not None:
        append_manifest = append_manifest.expanduser().resolve()
        appended_count = append_manifest_rows(append_manifest, manifest_rows)

    index_summary: Dict[str, Any] = {}
    rebuild_manifest_path = append_manifest if append_manifest is not None else output_manifest
    if rebuild_index:
        index_summary = production_seed.rebuild_real_index(
            manifest_path=rebuild_manifest_path,
            artifacts_dir=artifacts_dir.expanduser().resolve(),
            model_name=model_name,
            model_dir=model_dir,
            local_files_only=bool(local_files_only),
            device=str(device),
        )

    report = dict(result.report)
    report["clean_manifest"] = bool(clean_manifest)
    report["output_manifest"] = str(output_manifest)
    report["output_manifest_row_count"] = int(len(manifest_rows))
    report["placeholder_latent_count"] = int(placeholder_latent_count)
    report["append_manifest"] = str(append_manifest) if append_manifest is not None else ""
    report["append_manifest_new_rows"] = int(appended_count)
    report["rebuild_index"] = bool(rebuild_index)
    report["rebuild_manifest_path"] = str(rebuild_manifest_path)
    report["index_summary"] = dict(index_summary)
    if clean_manifest:
        report["output_manifest_summary"] = manifest_cleaner.summarize_rows(manifest_rows)
    if report_out is not None:
        report_out = report_out.expanduser().resolve()
        write_report_json(report_out, report)
        report["report_out"] = str(report_out)
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import and cache RoadGen3D-suitable Objaverse assets.")
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path("artifacts/objaverse_cache"),
        help="Workspace-local Objaverse cache root.",
    )
    parser.add_argument(
        "--output-manifest",
        type=Path,
        default=Path("data/real/objaverse_assets_manifest.jsonl"),
        help="Output manifest for imported Objaverse assets.",
    )
    parser.add_argument(
        "--append-manifest",
        type=Path,
        default=None,
        help="Optional manifest to append imported rows into, with asset_id de-duplication.",
    )
    parser.add_argument(
        "--latents-dir",
        type=Path,
        default=Path("data/real/latents"),
        help="Latent output directory placeholder for imported assets.",
    )
    parser.add_argument(
        "--report-out",
        type=Path,
        default=Path("artifacts/objaverse_cache/roadgen3d_objaverse_selection_report.json"),
        help="Selection report JSON path.",
    )
    parser.add_argument(
        "--categories",
        default=",".join(recommended_default_categories()),
        help="Comma-separated RoadGen target categories. Defaults to the recommended first-wave set.",
    )
    parser.add_argument("--max-per-category", type=int, default=4)
    parser.add_argument("--download-processes", type=int, default=1)
    parser.add_argument("--split", default="train")
    parser.add_argument("--clean-manifest", dest="clean_manifest", action="store_true")
    parser.add_argument("--no-clean-manifest", dest="clean_manifest", action="store_false")
    parser.set_defaults(clean_manifest=True)
    parser.add_argument("--rebuild-index", dest="rebuild_index", action="store_true")
    parser.add_argument("--no-rebuild-index", dest="rebuild_index", action="store_false")
    parser.set_defaults(rebuild_index=False)
    parser.add_argument("--artifacts-dir", type=Path, default=Path("artifacts/real"))
    parser.add_argument("--model-name", default="openai/clip-vit-base-patch32")
    parser.add_argument("--model-dir", type=Path, default=None)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--device", default="cpu")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = run_objaverse_import(
            cache_root=args.cache_root,
            output_manifest=args.output_manifest,
            latents_dir=args.latents_dir,
            requested_categories=_parse_categories(args.categories),
            max_per_category=int(args.max_per_category),
            download_processes=int(args.download_processes),
            split=str(args.split).strip().lower() or "train",
            clean_manifest=bool(args.clean_manifest),
            report_out=args.report_out,
            append_manifest=args.append_manifest,
            rebuild_index=bool(args.rebuild_index),
            artifacts_dir=args.artifacts_dir,
            model_name=str(args.model_name),
            model_dir=args.model_dir,
            local_files_only=bool(args.local_files_only),
            device=str(args.device),
        )
    except Exception as exc:
        print(f"[objaverse-import] failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
