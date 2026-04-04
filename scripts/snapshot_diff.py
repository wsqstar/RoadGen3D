#!/usr/bin/env python3
"""Graph Template Snapshot Diff Pipeline.

Runs one design query through the full AutoIterationController loop using the
real LLM, then produces a self-contained output folder with complete evolution
history and visual comparisons.

Usage
-----
    .venv/bin/python scripts/snapshot_diff.py \
        --query "modern pedestrian-friendly street with trees and benches" \
        --template-id hkust_gz_gate \
        --max-iterations 3 \
        --output-dir artifacts/snapshot_diff_$(date +%Y%m%d_%H%M%S) \
        --manifest data/real/real_assets_manifest.jsonl \
        --model-dir models/clip-vit-base-patch32 \
        --local-files-only \
        --device cpu
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

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
from roadgen3d.embedder import ClipTextEmbedder
from roadgen3d.graph_template_scene_bridge import build_graph_template_scene_bridge
from roadgen3d.index_store import FaissIndexStore
from roadgen3d.services.design_runtime import build_compose_config_from_draft
from roadgen3d.services.design_types import (
    DesignDraft,
    sanitize_compose_config_patch,
)
from roadgen3d.types import StreetComposeConfig

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_TEMPLATE_ID = "hkust_gz_gate"
DEFAULT_QUERY = "modern pedestrian-friendly street with trees and benches"


# ---------------------------------------------------------------------------
# Index builder (auto-builds FAISS index if missing in artifacts dir)
# ---------------------------------------------------------------------------

def _ensure_index(
    manifest_path: str,
    artifacts_dir: Path,
    model_dir: str,
    local_files_only: bool,
    device: str,
) -> None:
    """Build a CLIP+FAISS index into *artifacts_dir* if it does not exist yet."""
    index_path = artifacts_dir / "index_ip.faiss"
    id_map_path = artifacts_dir / "id_map.json"
    if index_path.exists() and id_map_path.exists():
        return

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    print(f"[snapshot_diff] Building FAISS index from manifest …")

    # Load manifest rows
    rows: List[Dict[str, Any]] = []
    base_dir = Path(manifest_path).parent.resolve()
    for line in Path(manifest_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        rows.append(payload)

    if not rows:
        raise ValueError(f"Manifest is empty: {manifest_path}")

    asset_ids = [str(r["asset_id"]) for r in rows]
    descriptions = [str(r["text_desc"]) for r in rows]

    embedder = ClipTextEmbedder(
        model_name="openai/clip-vit-base-patch32",
        model_dir=Path(model_dir) if model_dir else None,
        local_files_only=local_files_only,
        device=device,
    )
    embeddings = embedder.encode_texts(descriptions)

    store = FaissIndexStore.build(embeddings=embeddings, asset_ids=asset_ids)
    store.save(index_path=index_path, id_map_path=id_map_path)
    print(f"[snapshot_diff] Index built: {len(rows)} assets → {index_path}")


# ---------------------------------------------------------------------------
# Graph context builder (reused from run_auto_eval.py)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Config diff
# ---------------------------------------------------------------------------

def compute_config_diff(old_patch: Dict[str, Any], new_patch: Dict[str, Any]) -> Dict[str, Any]:
    """Compute a field-level diff between two config patches.

    Returns a dict with keys:
      - "added":   fields present in *new* but not *old*
      - "removed": fields present in *old* but not *new*
      - "changed": fields present in both but with different values,
                   stored as ``{"old": ..., "new": ...}``
    """
    diff: Dict[str, Any] = {"added": {}, "removed": {}, "changed": {}}
    all_keys = set(old_patch) | set(new_patch)
    for key in sorted(all_keys):
        in_old = key in old_patch
        in_new = key in new_patch
        if in_old and not in_new:
            diff["removed"][key] = old_patch[key]
        elif in_new and not in_old:
            diff["added"][key] = new_patch[key]
        elif old_patch[key] != new_patch[key]:
            diff["changed"][key] = {"old": old_patch[key], "new": new_patch[key]}
    return diff


# ---------------------------------------------------------------------------
# Image stitching
# ---------------------------------------------------------------------------

def stitch_preview_pair(path_a: str, path_b: str, out_path: str) -> str:
    """Stitch two preview images horizontally and save to *out_path*.

    Returns the *out_path* as a string.
    """
    try:
        from PIL import Image
    except ImportError:
        raise RuntimeError("PIL (Pillow) is required for image stitching")

    img_a = Image.open(path_a)
    img_b = Image.open(path_b)

    # Resize to same height if needed
    target_h = min(img_a.height, img_b.height)
    if img_a.height != target_h:
        ratio = target_h / img_a.height
        img_a = img_a.resize((int(img_a.width * ratio), target_h))
    if img_b.height != target_h:
        ratio = target_h / img_b.height
        img_b = img_b.resize((int(img_b.width * ratio), target_h))

    gap = 20
    combined = Image.new("RGB", (img_a.width + gap + img_b.width, target_h), (255, 255, 255))
    combined.paste(img_a, (0, 0))
    combined.paste(img_b, (img_a.width + gap, 0))

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    combined.save(out_path)
    return out_path


# ---------------------------------------------------------------------------
# Score progression chart
# ---------------------------------------------------------------------------

def plot_score_progression(snapshots: List[Dict[str, Any]], out_path: str) -> str:
    """Plot a score progression line chart and save to *out_path*."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        raise RuntimeError("matplotlib is required for score progression chart")

    iterations = [s["iteration"] for s in snapshots]
    scores = [s["score"] for s in snapshots]

    fig, ax = plt.subplots(figsize=(8, 4), dpi=120)
    ax.plot(iterations, scores, "o-", color="#2c3e50", linewidth=2, markersize=8)
    ax.set_xlabel("Iteration")
    ax.set_ylabel("Score (0–10)")
    ax.set_title("Score Progression")
    ax.set_ylim(0, 10)
    ax.set_xticks(iterations)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------
# HTML report builder
# ---------------------------------------------------------------------------

def _embed_image(path: str) -> str:
    """Read an image file and return a base64 data-URI."""
    data = Path(path).read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/png;base64,{b64}"


def build_html_report(
    output_dir: Path,
    result: Dict[str, Any],
    diffs: List[Dict[str, Any]],
    diff_paths: List[str],
    compare_paths: List[str],
    score_chart_path: str,
) -> str:
    """Build a self-contained HTML report embedding all images as base64."""
    total_iters = result["total_iterations"]
    best_iter = result["best_iteration"]
    best_score = result["best_score"]

    sections: List[str] = []
    sections.append("<h2>Pipeline Summary</h2>")
    sections.append(
        f"<p><strong>Query:</strong> {result.get('query', '')}</p>"
        f"<p><strong>Iterations:</strong> {total_iters}  |  "
        f"<strong>Best iteration:</strong> {best_iter}  |  "
        f"<strong>Best score:</strong> {best_score:.1f}/10</p>"
    )

    # Score progression chart
    if Path(score_chart_path).exists():
        src = _embed_image(score_chart_path)
        sections.append("<h2>Score Progression</h2>")
        sections.append(f'<img src="{src}" style="max-width:100%;" />')

    # Per-iteration previews
    sections.append("<h2>Iteration Previews</h2>")
    for i in range(total_iters):
        iter_dir = output_dir / f"iter_{i:02d}"
        preview = iter_dir / "preview.png"
        marker = " (best)" if i == best_iter else ""
        if preview.exists():
            src = _embed_image(str(preview))
            sections.append(
                f"<h3>Iteration {i}{marker}</h3>"
                f'<img src="{src}" style="max-width:45%;" />'
            )
        eval_path = iter_dir / "evaluation.json"
        if eval_path.exists():
            ev = json.loads(eval_path.read_text(encoding="utf-8"))
            score = ev.get("score", "?")
            text = ev.get("evaluation", "")
            sections.append(f"<p><em>Score: {score}/10</em></p>")
            sections.append(f"<p>{text}</p>")

    # Preview comparisons
    if compare_paths:
        sections.append("<h2>Preview Comparisons</h2>")
        for cp in compare_paths:
            if Path(cp).exists():
                src = _embed_image(cp)
                label = Path(cp).stem
                sections.append(
                    f"<h3>{label}</h3>"
                    f'<img src="{src}" style="max-width:100%;" />'
                )

    # Config diffs
    if diffs:
        sections.append("<h2>Config Diffs</h2>")
        for i, diff in enumerate(diffs):
            sections.append(f"<h3>Config diff iter {i} → iter {i + 1}</h3>")
            sections.append(
                f"<pre>{json.dumps(diff, indent=2, ensure_ascii=False)}</pre>"
            )

    # Final result
    final_dir = output_dir / "final"
    final_preview = final_dir / "preview.png"
    if final_preview.exists():
        src = _embed_image(str(final_preview))
        sections.append("<h2>Final Result</h2>")
        sections.append(f'<img src="{src}" style="max-width:100%;" />')

    html = (
        "<!DOCTYPE html>\n<html lang='en'>\n<head>\n"
        "<meta charset='utf-8'>\n"
        "<title>Snapshot Diff Report</title>\n"
        "<style>\n"
        "body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;"
        " max-width: 960px; margin: 2rem auto; padding: 0 1rem; color: #2c3e50; }\n"
        "h1 { border-bottom: 2px solid #2c3e50; padding-bottom: .5rem; }\n"
        "h2 { margin-top: 2rem; color: #34495e; }\n"
        "img { border: 1px solid #ddd; border-radius: 4px; margin: .5rem 0; }\n"
        "pre { background: #f8f9fa; padding: 1rem; border-radius: 4px; overflow-x: auto; }\n"
        "</style>\n"
        "</head>\n<body>\n"
        "<h1>Snapshot Diff Report</h1>\n"
        f"<p><em>Generated {datetime.now(timezone.utc).isoformat()}</em></p>\n"
        + "\n".join(sections) + "\n"
        "</body>\n</html>"
    )
    return html


# ---------------------------------------------------------------------------
# Core pipeline function (extracted for testability)
# ---------------------------------------------------------------------------

def run_snapshot_pipeline(
    *,
    graph_ctx: GraphSceneContext,
    query: str,
    output_dir: Path,
    max_iterations: int,
    manifest_path: str,
    model_dir: str,
    local_files_only: bool,
    device: str,
    design_service: Any | None = None,
) -> Dict[str, Any]:
    """Run the full snapshot-diff pipeline and return structured result."""
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- Step 0: Ensure FAISS index exists in _shared_artifacts ---
    shared_artifacts = output_dir / "_shared_artifacts"
    _ensure_index(
        manifest_path=manifest_path,
        artifacts_dir=shared_artifacts,
        model_dir=model_dir,
        local_files_only=local_files_only,
        device=device,
    )

    # --- Step 1: Run AutoIterationController ---
    controller = AutoIterationController(
        graph_ctx,
        manifest_path=manifest_path,
        artifacts_dir=str(output_dir / "_shared_artifacts"),
        output_dir=str(output_dir),
        max_iterations=max_iterations,
        model_dir=model_dir,
        local_files_only=local_files_only,
        device=device,
        query=query,
        design_service=design_service,
    )
    result: IterationResult = controller.run()

    # --- Step 2: Render presentation views for best result ---
    views: List[Dict[str, str]] = []
    try:
        best_layout_path = Path(result.best_layout_path)
        if best_layout_path.exists():
            layout_payload = json.loads(best_layout_path.read_text(encoding="utf-8"))
            best_snap = result.iterations[result.best_iteration]
            patch = sanitize_compose_config_patch(best_snap.config_patch)
            draft = DesignDraft(
                normalized_scene_query=str(patch.get("query", query)),
                compose_config_patch=patch,
                citations_by_field={},
                design_summary="Snapshot diff best iteration",
            )
            config = build_compose_config_from_draft(draft)
            views = render_presentation_views(
                layout_payload,
                out_dir=Path(result.best_layout_path).parent,
                config=config,
            )
    except Exception as exc:
        print(f"[snapshot_diff] Warning: presentation views rendering failed: {exc}")

    # --- Step 3: Post-processing ---
    diffs_dir = output_dir / "diffs"
    diffs_dir.mkdir(parents=True, exist_ok=True)

    config_diffs: List[Dict[str, Any]] = []
    diff_paths: List[str] = []
    compare_paths: List[str] = []

    snapshots = result.iterations
    for i in range(len(snapshots) - 1):
        old_patch = snapshots[i].config_patch
        new_patch = snapshots[i + 1].config_patch
        diff = compute_config_diff(old_patch, new_patch)

        # Save config diff
        diff_name = f"config_diff_{i:02d}_to_{i + 1:02d}.json"
        diff_path = str(diffs_dir / diff_name)
        Path(diff_path).write_text(
            json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        config_diffs.append(diff)
        diff_paths.append(diff_path)

        # Generate side-by-side preview comparison
        preview_a = snapshots[i].preview_path
        preview_b = snapshots[i + 1].preview_path
        if preview_a and preview_b and Path(preview_a).exists() and Path(preview_b).exists():
            compare_name = f"preview_compare_{i:02d}_vs_{i + 1:02d}.png"
            compare_path = str(diffs_dir / compare_name)
            try:
                stitch_preview_pair(preview_a, preview_b, compare_path)
                compare_paths.append(compare_path)
            except Exception as exc:
                print(f"[snapshot_diff] Warning: image stitching failed for iter {i}: {exc}")

    # Score progression chart
    snap_dicts = [
        {"iteration": s.iteration, "score": s.score}
        for s in snapshots
    ]
    score_chart_path = str(diffs_dir / "score_progression.png")
    try:
        plot_score_progression(snap_dicts, score_chart_path)
    except Exception as exc:
        print(f"[snapshot_diff] Warning: score chart failed: {exc}")

    # --- Step 4: Build eval report ---
    eval_report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "query": query,
        "total_iterations": result.total_iterations,
        "best_iteration": result.best_iteration,
        "best_score": result.best_score,
        "iterations": snap_dicts,
        "views_rendered": len(views),
        "view_names": [v.get("name", "") for v in views],
        "config_diffs": config_diffs,
    }
    eval_report_path = output_dir / "eval_report.json"
    eval_report_path.write_text(
        json.dumps(eval_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # --- Step 5: Build HTML report ---
    try:
        html = build_html_report(
            output_dir=output_dir,
            result=eval_report,
            diffs=config_diffs,
            diff_paths=diff_paths,
            compare_paths=compare_paths,
            score_chart_path=score_chart_path,
        )
        html_path = output_dir / "report.html"
        html_path.write_text(html, encoding="utf-8")
    except Exception as exc:
        print(f"[snapshot_diff] Warning: HTML report generation failed: {exc}")

    return {
        "query": query,
        "total_iterations": result.total_iterations,
        "best_score": result.best_score,
        "best_iteration": result.best_iteration,
        "best_layout_path": result.best_layout_path,
        "best_scene_path": result.best_scene_path,
        "views": views,
        "config_diffs": config_diffs,
        "compare_paths": compare_paths,
        "score_chart_path": score_chart_path,
        "html_report_path": str(output_dir / "report.html"),
        "eval_report_path": str(eval_report_path),
        "iteration_log_path": str(output_dir / "iteration_log.json"),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Snapshot Diff Pipeline: run one query and produce visual diff report.",
    )
    p.add_argument(
        "--query",
        default=DEFAULT_QUERY,
        help=f"Design query (default: {DEFAULT_QUERY}).",
    )
    p.add_argument(
        "--output-dir",
        default=None,
        help="Root output directory (default: artifacts/snapshot_diff_<timestamp>).",
    )
    p.add_argument(
        "--max-iterations",
        type=int,
        default=3,
        help="Max iterations (default: 3).",
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

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = (
        Path(args.output_dir) if args.output_dir
        else ROOT / "artifacts" / f"snapshot_diff_{timestamp}"
    )
    output_dir = output_dir.resolve()

    manifest = str((ROOT / args.manifest).resolve())
    model_dir = str((ROOT / args.model_dir).resolve()) if args.model_dir else args.model_dir

    print(f"[snapshot_diff] Output directory: {output_dir}")
    print(f"[snapshot_diff] Query: {args.query}")
    print(f"[snapshot_diff] Max iterations: {args.max_iterations}")
    print(f"[snapshot_diff] Template: {args.template_id}")

    # Step 1 – Build graph context from template
    print(f"[snapshot_diff] Building graph context from template '{args.template_id}' ...")
    graph_ctx = build_graph_context(template_id=args.template_id)
    print(
        f"[snapshot_diff] Graph loaded: "
        f"{graph_ctx.graph_summary.get('centerline_count', '?')} centerline(s), "
        f"{graph_ctx.graph_summary.get('junction_count', '?')} junction(s)."
    )

    # Step 2 – Run the snapshot-diff pipeline
    print(f"[snapshot_diff] Running pipeline ...")
    pipeline_result = run_snapshot_pipeline(
        graph_ctx=graph_ctx,
        query=args.query,
        output_dir=output_dir,
        max_iterations=args.max_iterations,
        manifest_path=manifest,
        model_dir=model_dir,
        local_files_only=args.local_files_only,
        device=args.device,
    )

    # Step 3 – Print summary
    print("\n" + "=" * 60)
    print("  Snapshot Diff Summary")
    print("=" * 60)
    print(f"  Query:            {pipeline_result['query']}")
    print(f"  Iterations:       {pipeline_result['total_iterations']}")
    print(f"  Best iteration:   {pipeline_result['best_iteration']}")
    print(f"  Best score:       {pipeline_result['best_score']:.1f}/10")
    print(f"  Config diffs:     {len(pipeline_result['config_diffs'])}")
    print(f"  Comparisons:      {len(pipeline_result['compare_paths'])}")
    print(f"  Views rendered:   {len(pipeline_result['views'])}")
    print(f"  HTML report:      {pipeline_result['html_report_path']}")
    print(f"  Eval report:      {pipeline_result['eval_report_path']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
