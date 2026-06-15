#!/usr/bin/env python3
"""Auto-pipeline CLI: graph JSON + base-map → generate → evaluate → iterate.

Usage example
-------------
    .venv/bin/python scripts/auto_scene_pipeline.py \
        --graph-json artifacts/exported_graph.json \
        --base-map path/to/reference.png \
        --output-dir artifacts/auto_pipeline/my_scene \
        --manifest data/real/real_assets_manifest.jsonl \
        --model-dir models/clip-vit-base-patch32 \
        --max-iterations 5 \
        --query "modern clean urban street" \
        --local-files-only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure the project source is importable
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.auto_pipeline.graph_loader import load_graph_from_exported_json
from roadgen3d.auto_pipeline.iteration_controller import AutoIterationController


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Auto-pipeline: generate, evaluate, and iteratively improve a street scene.",
    )
    p.add_argument(
        "--graph-json",
        required=True,
        help="Path to a Viewer-exported graph JSON file (ConvertedGraphPayload).",
    )
    p.add_argument(
        "--base-map",
        default=None,
        help="Path to an optional reference base-map PNG image.",
    )
    p.add_argument(
        "--output-dir",
        default="artifacts/auto_pipeline",
        help="Root output directory (default: artifacts/auto_pipeline).",
    )
    p.add_argument(
        "--manifest",
        default="data/real/real_assets_manifest.jsonl",
        help="Path to the asset manifest JSONL file.",
    )
    p.add_argument(
        "--model-dir",
        default="models/clip-vit-base-patch32",
        help="Path to the CLIP model directory.",
    )
    p.add_argument(
        "--max-iterations",
        type=int,
        default=5,
        help="Maximum number of generate-evaluate-improve iterations (default: 5).",
    )
    p.add_argument(
        "--query",
        default="modern clean urban street",
        help="Text description guiding the initial scene design.",
    )
    p.add_argument(
        "--local-files-only",
        action="store_true",
        default=False,
        help="Run in offline mode (no model downloads).",
    )
    p.add_argument(
        "--device",
        default="cpu",
        help="Torch device for CLIP inference (default: cpu).",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    # Resolve all paths relative to project root
    graph_json = (ROOT / args.graph_json).resolve()
    output_dir = (ROOT / args.output_dir).resolve()
    manifest = (ROOT / args.manifest).resolve()
    model_dir = (ROOT / args.model_dir).resolve() if args.model_dir else None
    base_map = (ROOT / args.base_map).resolve() if args.base_map else None

    # Step 1 – Load graph
    print(f"[auto_pipeline] Loading graph from {graph_json} ...")
    graph_ctx = load_graph_from_exported_json(graph_json)
    print(
        f"[auto_pipeline] Graph loaded: "
        f"{graph_ctx.graph_summary.get('centerline_count', '?')} centerline(s), "
        f"{graph_ctx.graph_summary.get('junction_count', '?')} junction(s)."
    )

    # Step 2 – Run iteration loop
    controller = AutoIterationController(
        graph_ctx,
        base_map_path=str(base_map) if base_map else None,
        manifest_path=str(manifest),
        artifacts_dir=str(output_dir),
        output_dir=str(output_dir / "scene"),
        max_iterations=args.max_iterations,
        model_dir=str(model_dir) if model_dir else "models/clip-vit-base-patch32",
        local_files_only=args.local_files_only,
        device=args.device,
        query=args.query,
    )

    result = controller.run()

    # Print summary
    print("\n" + "=" * 60)
    print("Auto-pipeline finished.")
    print(f"  Total iterations : {result.total_iterations}")
    print(f"  Best iteration   : {result.best_iteration}")
    print(f"  Best score       : {result.best_score:.1f} / 10")
    print(f"  Best layout      : {result.best_layout_path}")
    print(f"  Best scene GLB   : {result.best_scene_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
