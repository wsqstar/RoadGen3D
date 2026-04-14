"""2D diff renderers for layout comparison.

Provides two render modes:
  - overlay:   pixel-level diff of top-down previews (red=removed, green=added)
  - delta_map: vector delta map with arrows for moved placements
"""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .auto_pipeline.scene_renderer import render_topdown_preview
from .diff_engine import compute_placements_diff, match_placements_greedy, position_xz

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
except Exception:
    plt = None  # type: ignore[assignment]
    Line2D = None  # type: ignore[assignment,misc]

try:
    from PIL import Image, ImageChops
except Exception:
    Image = ImageChops = None  # type: ignore[misc,assignment]

try:
    import numpy as np
except Exception:
    np = None  # type: ignore[assignment]


_CATEGORY_COLORS: Dict[str, str] = {
    "bench": "#e6194b",
    "lamp": "#f58231",
    "trash": "#808000",
    "tree": "#3cb44b",
    "bus_stop": "#4363d8",
    "mailbox": "#911eb4",
    "hydrant": "#42d4f4",
    "bollard": "#f032e6",
}
_DEFAULT_COLOR = "#aaaaaa"


def _require_pillow() -> None:
    if Image is None or ImageChops is None:
        raise RuntimeError("Pillow is required for overlay diff rendering")


def _require_matplotlib() -> None:
    if plt is None:
        raise RuntimeError("matplotlib is required for delta map rendering")


def _require_numpy() -> None:
    if np is None:
        raise RuntimeError("numpy is required for overlay diff rendering")


def render_diff_overlay(
    layout_a_path: str | Path,
    layout_b_path: str | Path,
    out_path: str | Path,
) -> str:
    """Render a pixel-level overlay diff of two top-down previews.

    Best-effort alignment assumes both layouts share roughly the same
    spatial bounds.  Red = present in A but not B, Green = present in B
    but not A, Yellow = changed colour/texture at same location.
    """
    _require_pillow()
    _require_numpy()

    layout_a_path = Path(layout_a_path)
    layout_b_path = Path(layout_b_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_a = Path(tmpdir) / "a_preview.png"
        tmp_b = Path(tmpdir) / "b_preview.png"
        render_topdown_preview(layout_a_path, tmp_a)
        render_topdown_preview(layout_b_path, tmp_b)

        img_a = Image.open(tmp_a).convert("RGBA")
        img_b = Image.open(tmp_b).convert("RGBA")

        # Normalize to common size and cap at 4096 to avoid OOM
        target_size = (max(img_a.width, img_b.width), max(img_a.height, img_b.height))
        max_dimension = 4096
        if target_size[0] > max_dimension or target_size[1] > max_dimension:
            scale = max_dimension / max(target_size)
            target_size = (
                max(1, int(round(target_size[0] * scale))),
                max(1, int(round(target_size[1] * scale))),
            )
        if img_a.size != target_size:
            img_a = img_a.resize(target_size, Image.Resampling.LANCZOS)  # type: ignore[attr-defined]
        if img_b.size != target_size:
            img_b = img_b.resize(target_size, Image.Resampling.LANCZOS)  # type: ignore[attr-defined]

        # 50 % blend as base
        base = Image.blend(img_a, img_b, alpha=0.5)

        # Difference mask
        diff = ImageChops.difference(img_a, img_b)
        mask = np.array(diff.convert("L"))
        mask = (mask > 12).astype(np.uint8) * 255

        a_gray = np.array(img_a.convert("L"))
        b_gray = np.array(img_b.convert("L"))

        overlay = np.zeros((*target_size[::-1], 4), dtype=np.uint8)
        red = (mask > 0) & (a_gray > b_gray)
        green = (mask > 0) & (b_gray > a_gray)
        yellow = (mask > 0) & (a_gray == b_gray)

        overlay[red] = [239, 68, 68, 180]
        overlay[green] = [34, 197, 94, 180]
        overlay[yellow] = [234, 179, 8, 160]

        overlay_img = Image.fromarray(overlay, "RGBA")
        result = Image.alpha_composite(base, overlay_img)
        result.convert("RGB").save(out_path)
        return str(out_path)


def _draw_simple_road_bg(ax: Any, payload: Mapping[str, Any]) -> None:
    """Draw a simple road background from layout payload config/summary."""
    summary = dict(payload.get("summary", {}) or {})
    config = dict(payload.get("config", {}) or {})
    road_w = float(summary.get("road_width_m", config.get("road_width_m", 8.0)))
    sw_w = float(summary.get("sidewalk_width_m", config.get("sidewalk_width_m", 2.5)))
    length = float(summary.get("length_m", config.get("length_m", 80.0)))
    half_len = length / 2.0
    half_road = road_w / 2.0

    ax.fill_between([-half_len, half_len], -half_road, half_road, color="#cccccc", alpha=0.5)
    ax.fill_between([-half_len, half_len], half_road, half_road + sw_w, color="#e8e8e8", alpha=0.5)
    ax.fill_between([-half_len, half_len], -half_road - sw_w, -half_road, color="#e8e8e8", alpha=0.5)

    margin = max(road_w + sw_w * 2, 10)
    ax.set_xlim(-half_len - margin, half_len + margin)
    ax.set_ylim(-half_road - sw_w - margin, half_road + sw_w + margin)


def render_delta_map(
    layout_a_path: str | Path,
    layout_b_path: str | Path,
    out_path: str | Path,
) -> str:
    """Render a vector delta map with arrows for spatial shifts."""
    _require_matplotlib()

    layout_a = json.loads(Path(layout_a_path).read_text(encoding="utf-8"))
    layout_b = json.loads(Path(layout_b_path).read_text(encoding="utf-8"))

    fig, ax = plt.subplots(figsize=(10, 5.5))
    _draw_simple_road_bg(ax, layout_a)

    a_placements = list(layout_a.get("placements", []) or [])
    b_placements = list(layout_b.get("placements", []) or [])

    a_by_cat: Dict[str, List[Mapping[str, Any]]] = {}
    b_by_cat: Dict[str, List[Mapping[str, Any]]] = {}
    for p in a_placements:
        cat = str(p.get("category", "unknown")).strip().lower() or "unknown"
        a_by_cat.setdefault(cat, []).append(p)
    for p in b_placements:
        cat = str(p.get("category", "unknown")).strip().lower() or "unknown"
        b_by_cat.setdefault(cat, []).append(p)

    all_cats = sorted(set(a_by_cat) | set(b_by_cat))

    for cat in all_cats:
        color = _CATEGORY_COLORS.get(cat, _DEFAULT_COLOR)
        a_list = a_by_cat.get(cat, [])
        b_list = b_by_cat.get(cat, [])
        matched, a_unmatched, b_unmatched = match_placements_greedy(a_list, b_list)

        for ai, bi in matched:
            ax_a = position_xz(a_list[ai])
            ax_b = position_xz(b_list[bi])
            dist = math.hypot(ax_a[0] - ax_b[0], ax_a[1] - ax_b[1])
            if dist > 0.3:
                ax.annotate(
                    "",
                    xy=(ax_b[0], ax_b[1]),
                    xytext=(ax_a[0], ax_a[1]),
                    arrowprops=dict(arrowstyle="->", color=color, lw=1.5, alpha=0.8),
                )
            ax.plot(ax_a[0], ax_a[1], "o", color=color, markersize=5, alpha=0.5)
            ax.plot(ax_b[0], ax_b[1], "o", color=color, markersize=5, alpha=0.9)

        for ai in a_unmatched:
            pos = position_xz(a_list[ai])
            ax.plot(pos[0], pos[1], "x", color="#ef4444", markersize=7, markeredgewidth=2)

        for bi in b_unmatched:
            pos = position_xz(b_list[bi])
            ax.plot(pos[0], pos[1], "P", color="#22c55e", markersize=7)

    ax.set_aspect("equal")
    ax.set_title("Placement Delta Map (A → B)")

    if Line2D is not None:
        handles = [
            Line2D([0], [0], marker="P", color="w", markerfacecolor="#22c55e", markersize=8, label="Added in B"),
            Line2D([0], [0], marker="x", color="w", markerfacecolor="#ef4444", markersize=8, label="Deleted from A"),
            Line2D([0], [0], color="#64748b", lw=1.5, label="Moved"),
        ]
        ax.legend(handles=handles, loc="upper right", fontsize=8)

    fig.tight_layout()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(out_path)
