"""Render a top-down 2D preview from ``scene_layout.json`` using matplotlib."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.axes import Axes
    _HAS_MPL = True
except ImportError:  # pragma: no cover
    _HAS_MPL = False


# Category → colour mapping for the schematic view
_CATEGORY_COLORS: Dict[str, str] = {
    "tree": "#2ecc71",
    "shrub": "#27ae60",
    "bench": "#3498db",
    "bollard": "#e67e22",
    "street_lamp": "#f1c40f",
    "light_pole": "#f1c40f",
    "traffic_light": "#e74c3c",
    "trash_can": "#95a5a6",
    "fire_hydrant": "#e74c3c",
    "bus_stop": "#9b59b6",
    "bike_rack": "#1abc9c",
    "barrier": "#e67e22",
    "planter": "#2ecc71",
    "sign": "#3498db",
    "utility_pole": "#7f8c8d",
    "awning": "#d35400",
    "newsstand": "#8e44ad",
    "bicycle": "#1abc9c",
    "car": "#bdc3c7",
    "truck": "#7f8c8d",
}

_DEFAULT_COLOR = "#34495e"
_ROAD_COLOR = "#bdc3c7"
_SIDEWALK_COLOR = "#ecf0f1"


def render_topdown_preview(
    layout_path: str | Path,
    output_path: str | Path,
    *,
    image_width: int = 1024,
    image_height: int = 600,
    dpi: int = 120,
    draw_road_region: bool = True,
) -> str:
    """Render a top-down schematic from *scene_layout.json* and save as PNG.

    Returns the *output_path* as a string.
    """
    if not _HAS_MPL:
        raise RuntimeError("matplotlib is required for render_topdown_preview")

    layout = Path(layout_path).expanduser().resolve()
    if not layout.exists():
        raise FileNotFoundError(f"Layout file not found: {layout}")

    payload: Dict[str, Any] = json.loads(layout.read_text(encoding="utf-8"))
    placements: List[Dict[str, Any]] = payload.get("placements") or []
    config: Dict[str, Any] = payload.get("config") or {}

    out = Path(output_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(1, 1, figsize=(image_width / dpi, image_height / dpi), dpi=dpi)
    ax.set_aspect("equal")
    ax.set_facecolor("#f8f9fa")
    fig.patch.set_facecolor("white")

    # --- Road region background ---
    if draw_road_region:
        _draw_road_region(ax, config, placements)

    # --- Placements ---
    for p in placements:
        _draw_placement(ax, p)

    # --- Axes limits ---
    if placements:
        all_x: List[float] = []
        all_z: List[float] = []
        for p in placements:
            pos = p.get("position_xyz")
            if pos and len(pos) >= 3:
                all_x.append(float(pos[0]))
                all_z.append(float(pos[2]))
            bbox = p.get("bbox_xz")
            if bbox and len(bbox) >= 4:
                all_x.extend([float(bbox[0]), float(bbox[1])])
                all_z.extend([float(bbox[2]), float(bbox[3])])
        if all_x and all_z:
            pad_x = max(5.0, (max(all_x) - min(all_x)) * 0.1)
            pad_z = max(5.0, (max(all_z) - min(all_z)) * 0.1)
            ax.set_xlim(min(all_x) - pad_x, max(all_x) + pad_x)
            ax.set_ylim(min(all_z) - pad_z, max(all_z) + pad_z)

    # --- Labels & scale ---
    ax.set_xlabel("X (m)")
    ax.set_ylabel("Z (m)")
    ax.set_title("Top-down Scene Preview")

    _add_scale_bar(ax)

    # --- Legend ---
    categories_in_scene = {p.get("category", "") for p in placements if p.get("category")}
    handles = [
        mpatches.Patch(color=_CATEGORY_COLORS.get(cat, _DEFAULT_COLOR), label=cat)
        for cat in sorted(categories_in_scene)
    ]
    if handles:
        ax.legend(handles=handles, loc="upper right", fontsize=7, framealpha=0.8)

    fig.tight_layout()
    fig.savefig(str(out), dpi=dpi, bbox_inches="tight")
    plt.close(fig)

    return str(out)


# ---------------------------------------------------------------------------
# Internal drawing helpers
# ---------------------------------------------------------------------------

def _draw_road_region(ax: Axes, config: Dict[str, Any], placements: List[Dict[str, Any]]) -> None:
    """Draw a simplified road + sidewalk background rectangle."""
    road_width = float(config.get("road_width_m", 7.0))
    sidewalk_width = float(config.get("sidewalk_width_m", 2.4))
    length = float(config.get("length_m", 80.0))

    # Determine extent from placements if possible
    if placements:
        xs = [float(p.get("position_xyz", [0])[0]) for p in placements if p.get("position_xyz")]
        zs = [float(p.get("position_xyz", [0, 0, 0])[2]) for p in placements if p.get("position_xyz")]
        if xs and zs:
            cx = (min(xs) + max(xs)) / 2
            cz = (min(zs) + max(zs)) / 2
            half_len = max(length / 2, (max(xs) - min(xs)) / 2 + 5)
        else:
            cx, cz = 0.0, 0.0
            half_len = length / 2
    else:
        cx, cz = 0.0, 0.0
        half_len = length / 2

    total_half_w = road_width / 2 + sidewalk_width

    # Sidewalks
    ax.add_patch(mpatches.Rectangle(
        (cx - half_len, cz - total_half_w), 2 * half_len, 2 * total_half_w,
        linewidth=0, facecolor=_SIDEWALK_COLOR, zorder=0,
    ))
    # Road
    ax.add_patch(mpatches.Rectangle(
        (cx - half_len, cz - road_width / 2), 2 * half_len, road_width,
        linewidth=0, facecolor=_ROAD_COLOR, zorder=1,
    ))


def _draw_placement(ax: Axes, p: Dict[str, Any]) -> None:
    """Draw a single placement as a coloured marker (and optional bbox)."""
    pos = p.get("position_xyz")
    if not pos or len(pos) < 3:
        return
    x, z = float(pos[0]), float(pos[2])
    category = str(p.get("category", "unknown"))
    color = _CATEGORY_COLORS.get(category, _DEFAULT_COLOR)

    ax.plot(x, z, "o", color=color, markersize=4, zorder=5, markeredgewidth=0)

    # Optional: draw bbox rectangle
    bbox = p.get("bbox_xz")
    if bbox and len(bbox) >= 4:
        x0, x1, z0, z1 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
        rect = mpatches.Rectangle(
            (x0, z0), x1 - x0, z1 - z0,
            linewidth=0.3, edgecolor=color, facecolor="none", alpha=0.35, zorder=4,
        )
        ax.add_patch(rect)


def _add_scale_bar(ax: Axes) -> None:
    """Add a 10-metre scale bar in the lower-left corner."""
    xlim = ax.get_xlim()
    ylim = ax.get_ylim()
    span_x = xlim[1] - xlim[0]
    span_y = ylim[1] - ylim[0]
    bar_len = _nice_scale_length(max(span_x, span_y))
    x_start = xlim[0] + span_x * 0.05
    y_pos = ylim[0] + span_y * 0.05
    ax.plot([x_start, x_start + bar_len], [y_pos, y_pos], "k-", linewidth=2, zorder=10)
    ax.text(x_start + bar_len / 2, y_pos + span_y * 0.02, f"{bar_len:.0f} m",
            ha="center", va="bottom", fontsize=7, zorder=10)


def _nice_scale_length(span: float) -> float:
    """Pick a round scale-bar length for a given axis span."""
    raw = span * 0.15
    magnitude = 10 ** int(__import__("math").floor(__import__("math").log10(max(raw, 1e-9))))
    for candidate in (1, 2, 5, 10, 20, 50):
        if candidate * magnitude >= raw:
            return candidate * magnitude
    return raw
