#!/usr/bin/env python3
"""Evaluation scatter plot tool: compare 1 or 2 metrics across evaluation runs.

Usage
-----
    # Histogram (single metric)
    python scripts/eval_scatter.py \
        --input artifacts/m4/rule/eval_per_scene.csv \
        --x walkability_index \
        --bins 25

    # Scatter plot (two metrics)
    python scripts/eval_scatter.py \
        --input artifacts/m4/rule/eval_per_scene.csv \
               artifacts/m4/learned/eval_per_scene.csv \
        --x walkability_index \
        --y safety_score \
        --group-by policy_used \
        --output artifacts/scatter.png

    # With regression and Pareto frontier
    python scripts/eval_scatter.py \
        --input artifacts/m4/eval_per_scene.csv \
        --x safety_score \
        --y beauty_score \
        --show-regression \
        --show-pareto
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ---------------------------------------------------------------------------
# Available metrics
# ---------------------------------------------------------------------------

METRIC_HELP: Dict[str, str] = {
    # Aggregate scores (from eval_engine)
    "walkability_index": "Walkability Index (0-1)",
    "safety_score": "Safety Score (0-1)",
    "beauty_score": "Beauty Score (0-1)",
    "evaluation_score": "Overall Evaluation Score (0-1)",
    # Walkability indicators (from eval_engine)
    "walk_sid_clr": "SID_CLR - Clear Width",
    "walk_clear_cont": "CLEAR_CONT - Clear Continuity",
    "walk_furn_d": "FURN_D - Furniture Density",
    "walk_light_uni": "LIGHT_UNI - Light Uniformity",
    "walk_tree_shade": "TREE_SHADE - Tree Shade",
    "walk_buffer_ratio": "BUFFER_RATIO - Buffer Ratio",
    "walk_transit_prox": "TRANSIT_PROX - Transit Proximity",
    "walk_cross_prov": "CROSS_PROV - Crossing Provision",
    "walk_entr_dens": "ENTR_DENS - Entrance Density",
    "walk_poi_mix": "POI_MIX - POI Mix",
    "walk_micro_env": "MICRO_ENV - Micro Environment",
    # Walkability pillars
    "walk_pillar_protection": "Protection Pillar Score",
    "walk_pillar_comfort": "Comfort Pillar Score",
    "walk_pillar_delight": "Delight Pillar Score",
    # Safety features
    "safety_light_uni": "Safety: LIGHT_UNI",
    "safety_cross_prov": "Safety: CROSS_PROV",
    "safety_buffer_ratio": "Safety: BUFFER_RATIO",
    "safety_bollard_density": "Safety: BOLLARD_DENSITY",
    "safety_visibility_penalty": "Safety: VISIBILITY_PENALTY",
    "safety_structural_score": "Safety: Structural Score",
    # Beauty features
    "beauty_presentation_score": "Beauty: Presentation Score",
    "beauty_active_front_ratio": "Beauty: Active Front Ratio",
    "beauty_anchor_poi_score": "Beauty: Anchor POI Score",
    "beauty_structural_score": "Beauty: Structural Score",
    # Engineering metrics (from layout_eval.py CSV)
    "spacing_uniformity": "Spacing Uniformity",
    "style_consistency": "Style Consistency",
    "balance_score": "Balance Score",
    "dropped_slot_rate": "Dropped Slot Rate",
    "overlap_rate": "Overlap Rate",
    "diversity_ratio": "Diversity Ratio",
    "retrieval_top3_category_hit": "Retrieval Top-3 Hit Rate",
    "rule_satisfaction_rate": "Rule Satisfaction Rate",
    "topology_validity": "Topology Validity",
    "cross_section_feasibility": "Cross Section Feasibility",
    "editability": "Editability",
    "conflict_explainability": "Conflict Explainability",
    "latency_ms_total": "Total Latency (ms)",
    "latency_ms_per_instance": "Latency per Instance (ms)",
    "instance_count": "Instance Count",
    "dropped_slots": "Dropped Slots",
}

ALL_METRICS = list(METRIC_HELP.keys())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Plot evaluation metrics as histogram (1 metric) or scatter (2 metrics).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Available metrics:\n" + "\n".join(f"  {k}: {v}" for k, v in METRIC_HELP.items())
        ),
    )
    p.add_argument(
        "--input",
        "-i",
        type=Path,
        nargs="+",
        default=None,
        help="CSV file(s) from layout_eval.py. Multiple files are merged with source tracking.",
    )
    p.add_argument(
        "--x",
        type=str,
        default=None,
        help="X-axis metric (required for plotting).",
    )
    p.add_argument(
        "--y",
        type=str,
        default=None,
        help="Y-axis metric (optional). If omitted, shows histogram.",
    )
    p.add_argument(
        "--group-by",
        type=str,
        default=None,
        help="Column name to color-code points by (e.g., 'policy_used', 'query').",
    )
    p.add_argument(
        "--bins",
        type=int,
        default=20,
        help="Number of histogram bins (default: 20). Only used for single-metric mode.",
    )
    p.add_argument(
        "--show-regression",
        action="store_true",
        help="Show linear regression line.",
    )
    p.add_argument(
        "--show-pareto",
        action="store_true",
        help="Highlight Pareto frontier points.",
    )
    p.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Output file path (auto-generated if not specified).",
    )
    p.add_argument(
        "--format",
        choices=["png", "html", "both"],
        default="png",
        help="Output format (default: png).",
    )
    p.add_argument(
        "--title",
        type=str,
        default=None,
        help="Chart title (auto-generated if not specified).",
    )
    p.add_argument(
        "--list-metrics",
        action="store_true",
        help="List all available metrics and exit.",
    )
    p.add_argument(
        "--dpi",
        type=int,
        default=120,
        help="Image DPI (default: 120).",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_csv_data(paths: List[Path], group_by: Optional[str]) -> Dict[str, Any]:
    """Load and merge CSV files into a structured dict.

    Returns:
        {
            "x": [...],  # metric values for x-axis
            "y": [...],  # metric values for y-axis (None if single metric)
            "groups": [...],  # group labels (for color coding)
            "labels": [...],  # scene_id or row identifier
            "sources": [...],  # source file path for each row
            "all_columns": [...],  # all column names found
        }
    """
    try:
        import pandas as pd
    except ImportError:
        raise RuntimeError("pandas is required: pip install pandas")

    dfs: List[pd.DataFrame] = []
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {path}")
        df = pd.read_csv(path)
        df["_source_file"] = path.stem  # Use stem to avoid path issues
        dfs.append(df)

    merged = pd.concat(dfs, ignore_index=True)
    all_columns = list(merged.columns)

    return {
        "df": merged,
        "sources": paths,
        "all_columns": all_columns,
    }


def extract_metric_series(df, metric: str) -> pd.Series:
    """Extract a metric series, handling common naming variations."""
    # Direct match
    if metric in df.columns:
        return df[metric].astype(float)

    # Try prefix variations
    for col in df.columns:
        if col.lower() == metric.lower():
            return df[col].astype(float)

    # Try suffix match
    for col in df.columns:
        if col.lower().endswith(metric.lower()):
            return df[col].astype(float)

    raise ValueError(
        f"Metric '{metric}' not found in CSV. Available columns:\n"
        + "\n".join(f"  - {c}" for c in sorted(df.columns))
    )


def compute_pareto_frontier(xs: List[float], ys: List[float]) -> List[int]:
    """Return indices of points on the Pareto frontier (higher is better for both)."""
    points = sorted(zip(xs, ys), key=lambda p: (p[0], p[1]))
    pareto: List[int] = []
    max_y = -float("inf")
    for x, y in points:
        if y > max_y:
            pareto.append((x, y))
            max_y = y
    # Map back to original indices
    pareto_set = set(pareto)
    return [i for i, (x, y) in enumerate(zip(xs, ys)) if (x, y) in pareto_set]


def compute_regression(xs: List[float], ys: List[float]) -> tuple[float, float, float]:
    """Compute linear regression: y = slope * x + intercept.
    Returns: (slope, intercept, r_squared)
    """
    if len(xs) < 2:
        return 0.0, float(np.mean(ys)), 0.0

    x_arr = np.array(xs)
    y_arr = np.array(ys)

    # Handle NaN
    mask = ~(np.isnan(x_arr) | np.isnan(y_arr))
    if mask.sum() < 2:
        return 0.0, float(np.nanmean(y_arr)), 0.0

    x_clean = x_arr[mask]
    y_clean = y_arr[mask]

    slope, intercept = np.polyfit(x_clean, y_clean, 1)
    y_pred = slope * x_clean + intercept
    ss_res = np.sum((y_clean - y_pred) ** 2)
    ss_tot = np.sum((y_clean - np.mean(y_clean)) ** 2)
    r_squared = ss_res / ss_tot if ss_tot > 0 else 0.0

    return float(slope), float(intercept), float(r_squared)


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def plot_histogram(
    values: List[float],
    labels: List[str],
    metric_name: str,
    groups: Optional[List[str]] = None,
    bins: int = 20,
    title: Optional[str] = None,
    output_path: Optional[Path] = None,
    dpi: int = 120,
) -> Dict[str, Any]:
    """Plot histogram for single metric."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    values = np.array(values)
    valid_mask = ~np.isnan(values)
    values = values[valid_mask]

    if groups:
        groups_arr = np.array(groups)[valid_mask]
        unique_groups = sorted(set(groups_arr))
        colors = plt.cm.Set2(np.linspace(0, 1, len(unique_groups)))
        color_map = {g: colors[i] for i, g in enumerate(unique_groups)}

        fig, ax = plt.subplots(figsize=(10, 6))

        for group in unique_groups:
            group_vals = values[groups_arr == group]
            ax.hist(group_vals, bins=bins, alpha=0.6, label=group, color=color_map[group])

        ax.legend(title="Group")
    else:
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.hist(values, bins=bins, alpha=0.7, color="#3498db", edgecolor="white")

    # Statistics
    mean_val = np.mean(values)
    median_val = np.median(values)
    p25 = np.percentile(values, 25)
    p75 = np.percentile(values, 75)

    ax.axvline(mean_val, color="#e74c3c", linestyle="--", linewidth=2, label=f"Mean: {mean_val:.3f}")
    ax.axvline(median_val, color="#27ae60", linestyle="--", linewidth=2, label=f"Median: {median_val:.3f}")
    ax.axvspan(p25, p75, alpha=0.15, color="gray", label=f"IQR: [{p25:.3f}, {p75:.3f}]")

    ax.set_xlabel(METRIC_HELP.get(metric_name, metric_name))
    ax.set_ylabel("Count")
    ax.set_title(title or f"Distribution: {METRIC_HELP.get(metric_name, metric_name)}")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.2)

    stats = {
        "metric": metric_name,
        "count": int(len(values)),
        "mean": round(float(mean_val), 4),
        "median": round(float(median_val), 4),
        "std": round(float(np.std(values)), 4),
        "min": round(float(np.min(values)), 4),
        "max": round(float(np.max(values)), 4),
        "p25": round(float(p25), 4),
        "p75": round(float(p75), 4),
    }

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
        print(f"Histogram saved to: {output_path}")

    plt.close(fig)
    return stats


def plot_scatter(
    xs: List[float],
    ys: List[float],
    labels: List[str],
    x_metric: str,
    y_metric: str,
    groups: Optional[List[str]] = None,
    show_regression: bool = False,
    show_pareto: bool = False,
    title: Optional[str] = None,
    output_path: Optional[Path] = None,
    dpi: int = 120,
) -> Dict[str, Any]:
    """Plot scatter plot for two metrics."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    xs = np.array(xs)
    ys = np.array(ys)

    # Remove NaN
    valid_mask = ~(np.isnan(xs) | np.isnan(ys))
    xs = xs[valid_mask]
    ys = ys[valid_mask]
    labels = [l for l, m in zip(labels, valid_mask) if m]

    fig, ax = plt.subplots(figsize=(10, 8))

    if groups:
        groups_arr = np.array(groups)[valid_mask]
        unique_groups = sorted(set(groups_arr))
        colors = plt.cm.Set1(np.linspace(0, 1, len(unique_groups)))
        color_map = {g: colors[i] for i, g in enumerate(unique_groups)}

        for group in unique_groups:
            mask = groups_arr == group
            ax.scatter(
                xs[mask], ys[mask],
                c=[color_map[group]],
                label=group,
                alpha=0.7,
                s=60,
                edgecolors="white",
                linewidths=0.5,
            )
        ax.legend(title="Group", fontsize=9)
    else:
        ax.scatter(xs, ys, c="#3498db", alpha=0.7, s=60, edgecolors="white", linewidths=0.5)

    # Pareto frontier
    pareto_indices: List[int] = []
    if show_pareto:
        pareto_indices = compute_pareto_frontier(xs.tolist(), ys.tolist())
        ax.scatter(
            xs[pareto_indices], ys[pareto_indices],
            c="none",
            edgecolors="#27ae60",
            s=150,
            linewidths=2,
            label="Pareto Frontier",
        )

    # Regression line
    if show_regression:
        slope, intercept, r2 = compute_regression(xs.tolist(), ys.tolist())
        x_range = np.linspace(np.min(xs), np.max(xs), 100)
        y_pred = slope * x_range + intercept
        ax.plot(x_range, y_pred, "--", color="#e74c3c", linewidth=2,
                label=f"Regression: y={slope:.3f}x+{intercept:.3f} (R²={r2:.3f})")
        ax.legend(fontsize=9)

    ax.set_xlabel(METRIC_HELP.get(x_metric, x_metric))
    ax.set_ylabel(METRIC_HELP.get(y_metric, y_metric))
    ax.set_title(title or f"{METRIC_HELP.get(x_metric, x_metric)} vs {METRIC_HELP.get(y_metric, y_metric)}")
    ax.grid(True, alpha=0.2)
    ax.set_xlim(0, 1.05)
    ax.set_ylim(0, 1.05)

    # Correlation (handle NaN safely)
    n_valid = sum(1 for x, y in zip(xs, ys) if not (np.isnan(x) or np.isnan(y)))
    if n_valid > 1:
        valid_xs = [x for x in xs if not np.isnan(x)]
        valid_ys = [y for y in ys if not np.isnan(y)]
        if len(valid_xs) > 1:
            corr = float(np.corrcoef(valid_xs, valid_ys)[0, 1])
        else:
            corr = 0.0
    else:
        corr = 0.0
    ax.text(0.05, 0.95, f"Pearson r = {corr:.3f}\nn = {len(xs)}",
            transform=ax.transAxes, fontsize=10, verticalalignment="top",
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.8))

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
        print(f"Scatter plot saved to: {output_path}")

    plt.close(fig)

    return {
        "x_metric": x_metric,
        "y_metric": y_metric,
        "count": int(len(xs)),
        "correlation": round(corr, 4),
        "pareto_count": len(pareto_indices),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

    if args.list_metrics:
        print("Available metrics:")
        for k, v in METRIC_HELP.items():
            print(f"  {k}: {v}")
        return 0

    # Validate required args for plotting
    if args.input is None or args.x is None:
        print("Error: --input and --x are required for plotting.")
        print("Use --list-metrics to see available metrics.")
        return 1

    # Load data
    print(f"Loading {len(args.input)} CSV file(s)...")
    data = load_csv_data(args.input, args.group_by)
    df = data["df"]

    # Extract metrics
    print(f"Extracting metric: {args.x}")
    x_vals = extract_metric_series(df, args.x)

    if args.y:
        print(f"Extracting metric: {args.y}")
        y_vals = extract_metric_series(df, args.y)
    else:
        y_vals = None

    # Extract grouping
    groups: Optional[List[str]] = None
    if args.group_by:
        if args.group_by in df.columns:
            groups = df[args.group_by].fillna("unknown").tolist()
            print(f"Grouping by: {args.group_by} ({len(set(groups))} unique groups)")
        else:
            print(f"Warning: group-by column '{args.group_by}' not found, ignoring.")

    # Labels (scene_id or index)
    if "scene_id" in df.columns:
        labels = df["scene_id"].fillna("").tolist()
    else:
        labels = [f"row_{i}" for i in range(len(df))]

    # Auto-generate output path
    if args.output is None:
        suffix = f"{args.x}" if not args.y else f"{args.x}_vs_{args.y}"
        output_path = ROOT / "artifacts" / f"eval_scatter_{suffix}.{args.format}"
    else:
        output_path = args.output

    # Auto-generate title
    if args.title is None:
        title = None
    else:
        title = args.title

    # Plot
    print("\n" + "=" * 60)
    if y_vals is None:
        # Histogram
        print(f"Generating histogram: {args.x} ({args.bins} bins)")
        stats = plot_histogram(
            x_vals.tolist(),
            labels,
            args.x,
            groups=groups,
            bins=args.bins,
            title=title,
            output_path=output_path if args.format in ("png", "both") else None,
            dpi=args.dpi,
        )
        print(f"\nStatistics for {args.x}:")
        for k, v in stats.items():
            print(f"  {k}: {v}")
    else:
        # Scatter
        print(f"Generating scatter: {args.x} vs {args.y}")
        if groups:
            print(f"Color-coded by: {args.group_by}")
        if args.show_regression:
            print("Showing regression line")
        if args.show_pareto:
            print("Showing Pareto frontier")

        stats = plot_scatter(
            x_vals.tolist(),
            y_vals.tolist(),
            labels,
            args.x,
            args.y,
            groups=groups,
            show_regression=args.show_regression,
            show_pareto=args.show_pareto,
            title=title,
            output_path=output_path if args.format in ("png", "both") else None,
            dpi=args.dpi,
        )
        print(f"\nScatter statistics:")
        for k, v in stats.items():
            print(f"  {k}: {v}")

    print("=" * 60 + "\n")

    # HTML export
    if args.format in ("html", "both"):
        try:
            import plotly.express as px
            import plotly.graph_objects as go

            if y_vals is None:
                # Histogram with plotly
                fig = px.histogram(
                    x=x_vals,
                    nbins=args.bins,
                    title=title or f"Distribution: {METRIC_HELP.get(args.x, args.x)}",
                    labels={"x": METRIC_HELP.get(args.x, args.x), "count": "Count"},
                )
                if groups:
                    fig.data[0].name = "count"
            else:
                # Scatter with plotly
                plot_df = df.copy()
                plot_df["_x"] = x_vals
                plot_df["_y"] = y_vals

                if groups:
                    fig = px.scatter(
                        plot_df,
                        x="_x",
                        y="_y",
                        color=args.group_by,
                        hover_data=["scene_id"] if "scene_id" in df.columns else None,
                        title=title or f"{METRIC_HELP.get(args.x)} vs {METRIC_HELP.get(args.y)}",
                        labels={"_x": METRIC_HELP.get(args.x, args.x), "_y": METRIC_HELP.get(args.y, args.y)},
                    )
                else:
                    fig = px.scatter(
                        plot_df,
                        x="_x",
                        y="_y",
                        hover_data=["scene_id"] if "scene_id" in df.columns else None,
                        title=title or f"{METRIC_HELP.get(args.x)} vs {METRIC_HELP.get(args.y)}",
                        labels={"_x": METRIC_HELP.get(args.x, args.x), "_y": METRIC_HELP.get(args.y, args.y)},
                    )

                fig.update_layout(xaxis_range=[0, 1.05], yaxis_range=[0, 1.05])

            html_path = output_path.with_suffix(".html")
            fig.write_html(str(html_path))
            print(f"Interactive HTML saved to: {html_path}")

        except ImportError:
            print("Note: plotly not installed. Install with: pip install plotly")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
