"""Render a top-down 2D preview from ``scene_layout.json`` using matplotlib."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

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
    annotation: Any | None = None,
    base_map_path: str | Path | None = None,
    image_width: int = 1024,
    image_height: int = 600,
    dpi: int = 120,
    draw_road_region: bool = True,
    base_map_alpha: float = 0.35,
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

    # --- Graph/base-map background ---
    if base_map_path:
        _draw_base_map(ax, base_map_path, annotation, alpha=base_map_alpha)
    if annotation is not None:
        _draw_annotation_context(ax, annotation)
    elif draw_road_region:
        _draw_road_region(ax, config, placements)

    # --- Placements ---
    for p in placements:
        _draw_placement(ax, p)

    # --- Axes limits ---
    all_x, all_z = _collect_layout_extent(placements)
    graph_x, graph_z = _collect_annotation_extent(annotation)
    all_x.extend(graph_x)
    all_z.extend(graph_z)
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


def _draw_base_map(
    ax: Axes,
    base_map_path: str | Path,
    annotation: Any | None,
    *,
    alpha: float,
) -> None:
    """Draw a reference PNG using annotation scale when available."""
    path = Path(base_map_path).expanduser().resolve()
    if not path.exists():
        return
    try:
        image = plt.imread(str(path))
    except Exception:
        return

    width_px = _annotation_number(annotation, "image_width_px", 0.0)
    height_px = _annotation_number(annotation, "image_height_px", 0.0)
    ppm = max(_annotation_number(annotation, "pixels_per_meter", 1.0), 1e-6)
    if width_px <= 0 or height_px <= 0:
        height_px = float(getattr(image, "shape", [0, 0])[0] or 0)
        width_px = float(getattr(image, "shape", [0, 0])[1] or 0)
    if width_px <= 0 or height_px <= 0:
        return

    half_w = width_px / ppm * 0.5
    half_h = height_px / ppm * 0.5
    ax.imshow(
        image,
        extent=(-half_w, half_w, -half_h, half_h),
        origin="upper",
        alpha=max(0.0, min(float(alpha), 1.0)),
        zorder=-10,
    )


def _draw_annotation_context(ax: Axes, annotation: Any) -> None:
    """Draw graph centerlines, junctions, and building regions from annotation."""
    for region in _iter_annotation_items(annotation, "building_regions"):
        _draw_building_region(ax, annotation, region)

    for centerline in _iter_annotation_items(annotation, "centerlines"):
        points = _centerline_local_points(annotation, centerline)
        if len(points) < 2:
            continue
        xs = [p[0] for p in points]
        zs = [p[1] for p in points]
        width_m = _item_number(centerline, "road_width_m", 7.0)
        ax.plot(
            xs,
            zs,
            color="#aeb6bf",
            linewidth=max(2.5, min(width_m * 0.55, 16.0)),
            alpha=0.55,
            solid_capstyle="round",
            zorder=0,
        )
        ax.plot(
            xs,
            zs,
            color="#4b5563",
            linewidth=0.9,
            alpha=0.9,
            linestyle="--",
            zorder=2,
        )

    for junction in _iter_annotation_items(annotation, "junctions"):
        x_px = _item_number(junction, "anchor_x", _item_number(junction, "x", 0.0))
        y_px = _item_number(junction, "anchor_y", _item_number(junction, "y", 0.0))
        x, z = _pixel_to_local(annotation, x=x_px, y=y_px)
        radius = max(1.6, _item_number(junction, "crosswalk_depth_m", 3.0) * 0.6)
        ax.add_patch(
            mpatches.Circle(
                (x, z),
                radius=radius,
                facecolor="#ffffff",
                edgecolor="#111827",
                linewidth=0.8,
                alpha=0.85,
                zorder=3,
            )
        )


def _draw_building_region(ax: Axes, annotation: Any, region: Any) -> None:
    center_x = _item_number(region, "center_x_px", 0.0)
    center_y = _item_number(region, "center_y_px", 0.0)
    if isinstance(region, Mapping) and isinstance(region.get("center_px"), Mapping):
        center = region["center_px"]
        center_x = float(center.get("x", center_x) or center_x)
        center_y = float(center.get("y", center_y) or center_y)

    ppm = max(_annotation_number(annotation, "pixels_per_meter", 1.0), 1e-6)
    cx, cz = _pixel_to_local(annotation, x=center_x, y=center_y)
    width_m = _item_number(region, "width_px", 0.0) / ppm
    height_m = _item_number(region, "height_px", 0.0) / ppm
    if width_m <= 0 or height_m <= 0:
        return
    yaw_deg = _item_number(region, "yaw_deg", 0.0)
    rect = mpatches.Rectangle(
        (cx - width_m * 0.5, cz - height_m * 0.5),
        width_m,
        height_m,
        angle=yaw_deg,
        rotation_point="center",
        facecolor="#d7ccc8",
        edgecolor="#8d6e63",
        linewidth=0.6,
        alpha=0.45,
        zorder=-1,
    )
    ax.add_patch(rect)


def _collect_layout_extent(placements: Sequence[Mapping[str, Any]]) -> Tuple[List[float], List[float]]:
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
    return all_x, all_z


def _collect_annotation_extent(annotation: Any | None) -> Tuple[List[float], List[float]]:
    if annotation is None:
        return [], []
    xs: List[float] = []
    zs: List[float] = []
    width_px = _annotation_number(annotation, "image_width_px", 0.0)
    height_px = _annotation_number(annotation, "image_height_px", 0.0)
    ppm = max(_annotation_number(annotation, "pixels_per_meter", 1.0), 1e-6)
    if width_px > 0 and height_px > 0:
        xs.extend([-width_px / ppm * 0.5, width_px / ppm * 0.5])
        zs.extend([-height_px / ppm * 0.5, height_px / ppm * 0.5])
    for centerline in _iter_annotation_items(annotation, "centerlines"):
        for x, z in _centerline_local_points(annotation, centerline):
            xs.append(x)
            zs.append(z)
    for region in _iter_annotation_items(annotation, "building_regions"):
        center_x = _item_number(region, "center_x_px", 0.0)
        center_y = _item_number(region, "center_y_px", 0.0)
        if isinstance(region, Mapping) and isinstance(region.get("center_px"), Mapping):
            center = region["center_px"]
            center_x = float(center.get("x", center_x) or center_x)
            center_y = float(center.get("y", center_y) or center_y)
        cx, cz = _pixel_to_local(annotation, x=center_x, y=center_y)
        width_m = _item_number(region, "width_px", 0.0) / ppm
        height_m = _item_number(region, "height_px", 0.0) / ppm
        xs.extend([cx - width_m * 0.5, cx + width_m * 0.5])
        zs.extend([cz - height_m * 0.5, cz + height_m * 0.5])
    return xs, zs


def _centerline_local_points(annotation: Any, centerline: Any) -> List[Tuple[float, float]]:
    points: List[Tuple[float, float]] = []
    for point in _item_sequence(centerline, "points"):
        x_px = _item_number(point, "x", 0.0)
        y_px = _item_number(point, "y", 0.0)
        xy = _pixel_to_local(annotation, x=x_px, y=y_px)
        if not points or points[-1] != xy:
            points.append(xy)
    return points


def _pixel_to_local(annotation: Any, *, x: float, y: float) -> Tuple[float, float]:
    width_px = _annotation_number(annotation, "image_width_px", 0.0)
    height_px = _annotation_number(annotation, "image_height_px", 0.0)
    ppm = max(_annotation_number(annotation, "pixels_per_meter", 1.0), 1e-6)
    return (
        (float(x) - width_px * 0.5) / ppm,
        (height_px * 0.5 - float(y)) / ppm,
    )


def _iter_annotation_items(annotation: Any, field_name: str) -> Sequence[Any]:
    if annotation is None:
        return ()
    value = annotation.get(field_name) if isinstance(annotation, Mapping) else getattr(annotation, field_name, ())
    if value is None:
        return ()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _item_sequence(item: Any, field_name: str) -> Sequence[Any]:
    value = item.get(field_name) if isinstance(item, Mapping) else getattr(item, field_name, ())
    if value is None:
        return ()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _annotation_number(annotation: Any, field_name: str, default: float) -> float:
    if annotation is None:
        return float(default)
    return _item_number(annotation, field_name, default)


def _item_number(item: Any, field_name: str, default: float) -> float:
    value = item.get(field_name, default) if isinstance(item, Mapping) else getattr(item, field_name, default)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(parsed):
        return float(default)
    return parsed


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
