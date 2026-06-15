#!/usr/bin/env python3
"""Multi-version auto-evaluation: generate, iterate, render, score.

Runs multiple design queries through the full AutoIterationController loop,
renders presentation views for each best result, and produces a consolidated
evaluation report.

Usage
-----
    .venv/bin/python scripts/run_auto_eval.py \
        --output-dir artifacts/auto_eval_$(date +%Y%m%d_%H%M%S) \
        --max-iterations 3 \
        --queries "modern transit boulevard" \
                  "pedestrian-friendly green street" \
                  "commercial shopping district street" \
        --manifest data/real/real_assets_manifest.jsonl \
        --model-dir models/clip-vit-base-patch32 \
        --local-files-only \
        --device cpu
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping

# ---------------------------------------------------------------------------
# Ensure project source is importable
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.auto_pipeline.graph_loader import GraphSceneContext
from roadgen3d.auto_pipeline.iteration_controller import (
    AutoIterationController,
    IterationResult,
)
from roadgen3d.beauty import render_presentation_views
from roadgen3d.graph_template_scene_bridge import build_graph_template_scene_bridge
from roadgen3d.services.design_runtime import build_compose_config_from_draft
from roadgen3d.services.design_types import (
    DesignDraft,
    sanitize_compose_config_patch,
)
from roadgen3d.types import StreetComposeConfig

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_QUERIES = [
    "modern transit boulevard with bus stops and bike lanes",
    "pedestrian-friendly green street with trees and benches",
    "commercial shopping district street with outdoor seating",
]
DEFAULT_TEMPLATE_ID = "hkust_gz_gate"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    """Convert a query string into a filesystem-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return re.sub(r"_+", "_", slug)


def build_graph_context(template_id: str = DEFAULT_TEMPLATE_ID) -> GraphSceneContext:
    """Build a *GraphSceneContext* from a built-in graph template."""
    bridge = build_graph_template_scene_bridge(template_id=template_id)
    from roadgen3d.auto_pipeline.graph_loader import _extract_graph_summary

    graph_summary = _extract_graph_summary(bridge.annotation, bridge.summary_metadata)
    return GraphSceneContext(
        road_segment_graph=bridge.road_segment_graph,
        projected_features=bridge.projected_features,
        placement_context=bridge.placement_context,
        annotation=bridge.annotation,
        graph_summary=graph_summary,
    )


def build_config_from_latest_patch(
    result: IterationResult,
) -> StreetComposeConfig:
    """Build a *StreetComposeConfig* from the best iteration's config patch."""
    best_snap = result.iterations[result.best_iteration]
    patch = sanitize_compose_config_patch(best_snap.config_patch)
    draft = DesignDraft(
        normalized_scene_query=str(patch.get("query", "auto eval")),
        compose_config_patch=patch,
        citations_by_field={},
        design_summary="Auto-eval best iteration",
    )
    return build_compose_config_from_draft(draft)


def build_eval_report(
    results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build the top-level evaluation report dict."""
    versions: List[Dict[str, Any]] = []
    for r in results:
        versions.append({
            "query": r["query"],
            "slug": slugify(r["query"]),
            "total_iterations": r["total_iterations"],
            "best_score": r["best_score"],
            "best_iteration": r["best_iteration"],
            "views_rendered": len(r.get("views", [])),
            "view_names": [v.get("name", "") for v in r.get("views", [])],
            "iteration_log_path": r.get("iteration_log_path", ""),
        })

    scores = [v["best_score"] for v in versions if v["best_score"] is not None]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "num_versions": len(versions),
        "best_score_overall": max(scores) if scores else 0.0,
        "avg_score": round(sum(scores) / len(scores), 2) if scores else 0.0,
        "versions": versions,
    }


def print_summary_table(report: Dict[str, Any]) -> None:
    """Print a concise summary table to the terminal."""
    print("\n" + "=" * 78)
    print("  Auto-Eval Summary")
    print("=" * 78)
    header = f"  {'#':<4} {'Query':<40} {'Iters':<7} {'Score':<8} {'Views':<6}"
    print(header)
    print("  " + "-" * 72)
    for i, v in enumerate(report["versions"]):
        query_display = v["query"][:38] + ".." if len(v["query"]) > 40 else v["query"]
        print(
            f"  {i:<4} {query_display:<40} "
            f"{v['total_iterations']:<7} "
            f"{v['best_score']:<8.1f} "
            f"{v['views_rendered']:<6}"
        )
    print("-" * 78)
    print(
        f"  Overall best: {report['best_score_overall']:.1f}  |  "
        f"Average: {report['avg_score']:.1f}  |  "
        f"Versions: {report['num_versions']}"
    )
    print("=" * 78 + "\n")


# ---------------------------------------------------------------------------
# Per-version runner
# ---------------------------------------------------------------------------

def run_single_version(
    graph_ctx: GraphSceneContext,
    query: str,
    version_dir: Path,
    max_iterations: int,
    *,
    manifest_path: str,
    model_dir: str,
    local_files_only: bool,
    device: str,
    artifacts_dir: str,
) -> Dict[str, Any]:
    """Run one query through the full pipeline: iterate → render → log."""

    version_dir.mkdir(parents=True, exist_ok=True)

    controller = AutoIterationController(
        graph_ctx,
        manifest_path=manifest_path,
        artifacts_dir=artifacts_dir,
        output_dir=str(version_dir),
        max_iterations=max_iterations,
        model_dir=model_dir,
        local_files_only=local_files_only,
        device=device,
        query=query,
    )
    result = controller.run()

    # --- Render presentation views for the best layout -----------------------
    views: List[Dict[str, str]] = []
    try:
        best_layout_path = Path(result.best_layout_path)
        if best_layout_path.exists():
            layout_payload = json.loads(best_layout_path.read_text(encoding="utf-8"))
            config = build_config_from_latest_patch(result)
            views_dir = best_layout_path.parent
            views = render_presentation_views(
                layout_payload,
                out_dir=views_dir,
                config=config,
            )
    except Exception as exc:
        print(f"[auto_eval] Warning: presentation views rendering failed: {exc}")

    return {
        "query": query,
        "total_iterations": result.total_iterations,
        "best_score": result.best_score,
        "best_iteration": result.best_iteration,
        "best_layout_path": result.best_layout_path,
        "best_scene_path": result.best_scene_path,
        "views": views,
        "iteration_log_path": str(version_dir / "iteration_log.json"),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Multi-version auto-evaluation pipeline.",
    )
    p.add_argument(
        "--output-dir",
        default=None,
        help="Root output directory (default: artifacts/auto_eval_<timestamp>).",
    )
    p.add_argument(
        "--max-iterations",
        type=int,
        default=3,
        help="Max iterations per query (default: 3).",
    )
    p.add_argument(
        "--queries",
        nargs="+",
        default=None,
        help="Design queries. Defaults to 3 built-in queries.",
    )
    p.add_argument(
        "--template-id",
        default=DEFAULT_TEMPLATE_ID,
        help=f"Graph template ID (default: {DEFAULT_TEMPLATE_ID}).",
    )
    p.add_argument(
        "--manifest",
        default="data/real/real_assets_manifest.jsonl",
        help="Path to the asset manifest JSONL.",
    )
    p.add_argument(
        "--model-dir",
        default="models/clip-vit-base-patch32",
        help="Path to the CLIP model directory.",
    )
    p.add_argument(
        "--local-files-only",
        action="store_true",
        default=False,
        help="Run in offline mode.",
    )
    p.add_argument(
        "--device",
        default="cpu",
        help="Torch device (default: cpu).",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    queries = args.queries or DEFAULT_QUERIES
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir) if args.output_dir else ROOT / "artifacts" / f"auto_eval_{timestamp}"
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = str((ROOT / args.manifest).resolve())
    model_dir = str((ROOT / args.model_dir).resolve()) if args.model_dir else args.model_dir

    print(f"[auto_eval] Output directory: {output_dir}")
    print(f"[auto_eval] Queries ({len(queries)}): {queries}")
    print(f"[auto_eval] Max iterations per query: {args.max_iterations}")
    print(f"[auto_eval] Template: {args.template_id}")

    # Step 1 – Build shared graph context from template
    print(f"[auto_eval] Building graph context from template '{args.template_id}' ...")
    graph_ctx = build_graph_context(template_id=args.template_id)
    print(
        f"[auto_eval] Graph loaded: "
        f"{graph_ctx.graph_summary.get('centerline_count', '?')} centerline(s), "
        f"{graph_ctx.graph_summary.get('junction_count', '?')} junction(s)."
    )

    # Step 2 – Run each query
    results: List[Dict[str, Any]] = []
    for i, query in enumerate(queries):
        slug = slugify(query)
        version_dir = output_dir / f"version_{i:02d}_{slug}"
        print(f"\n[auto_eval] === Version {i}: '{query}' ===")
        result = run_single_version(
            graph_ctx,
            query=query,
            version_dir=version_dir,
            max_iterations=args.max_iterations,
            manifest_path=manifest,
            model_dir=model_dir,
            local_files_only=args.local_files_only,
            device=args.device,
            artifacts_dir=str(output_dir / "_shared_artifacts"),
        )
        results.append(result)
        print(
            f"[auto_eval] Version {i} done: "
            f"{result['total_iterations']} iters, "
            f"best score={result['best_score']:.1f}, "
            f"{len(result['views'])} views rendered."
        )

    # Step 3 – Build and save consolidated report
    report = build_eval_report(results)
    report_path = output_dir / "eval_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[auto_eval] Report saved to {report_path}")

    print_summary_table(report)


if __name__ == "__main__":
    main()
