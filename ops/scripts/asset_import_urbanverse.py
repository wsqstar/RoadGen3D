#!/usr/bin/env python3
"""Import a local UrbanVerse subset directory into RoadGen3D manifests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.urbanverse_import import run_urbanverse_subset_import  # noqa: E402


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import a directory-packaged UrbanVerse subset into RoadGen3D.")
    parser.add_argument("--input-root", type=Path, required=True, help="Input root containing metadata/*.jsonl and source assets.")
    parser.add_argument("--subset-name", required=True, help="Stable subset name used in output paths and source_dataset ids.")
    parser.add_argument("--output-root", type=Path, default=None, help="Output manifest root. Defaults to data/urbanverse/<subset-name>/")
    parser.add_argument("--cache-root", type=Path, default=None, help="Copied-asset cache root. Defaults to artifacts/urbanverse_cache/<subset-name>/")
    parser.add_argument("--append-object-manifest", type=Path, default=None, help="Optional object v2 manifest to upsert imported object rows into.")
    parser.add_argument("--append-ground-manifest", type=Path, default=None, help="Optional ground material manifest to upsert imported rows into.")
    parser.add_argument("--append-sky-manifest", type=Path, default=None, help="Optional sky manifest to upsert imported rows into.")
    parser.add_argument("--rebuild-index", action="store_true", help="Rebuild the real asset retrieval index after appending object rows.")
    parser.add_argument("--artifacts-dir", type=Path, default=Path("artifacts/real"), help="Artifacts directory used when rebuilding the real asset index.")
    parser.add_argument("--model-name", default="openai/clip-vit-base-patch32")
    parser.add_argument("--model-dir", type=Path, default=None)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--device", default="cpu")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = run_urbanverse_subset_import(
            input_root=args.input_root,
            subset_name=str(args.subset_name),
            output_root=args.output_root,
            cache_root=args.cache_root,
            append_object_manifest=args.append_object_manifest,
            append_ground_manifest=args.append_ground_manifest,
            append_sky_manifest=args.append_sky_manifest,
            rebuild_index=bool(args.rebuild_index),
            artifacts_dir=args.artifacts_dir,
            model_name=str(args.model_name),
            model_dir=args.model_dir,
            local_files_only=bool(args.local_files_only),
            device=str(args.device),
        )
    except Exception as exc:
        print(f"[urbanverse-import] failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
