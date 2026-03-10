"""Spatial distance visualization for street layout scenes.

Provides matplotlib-based visualizations of:
  - Scene overview with junction / entrance markers
  - Distance heatmaps (road edge, junction, entrance)
  - Distance distribution histograms across placed assets
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .poi_taxonomy import nonempty_poi_points, poi_plot_config
from .spatial_features import SpatialContext, compute_slot_distances

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.figure import Figure
except ImportError:
    plt = None  # type: ignore[assignment]
    Figure = None  # type: ignore[assignment,misc]


# Category colour map (consistent with street_layout categories)
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
_THEME_COLORS: Dict[str, str] = {
    "residential": "#7fb069",
    "commercial": "#e07a5f",
    "transit": "#4d96ff",
    "green": "#2a9d8f",
}
_ROLE_ALPHA: Dict[str, float] = {
    "left_building_buffer": 0.16,
    "left_sidewalk": 0.24,
    "carriageway": 0.34,
    "right_sidewalk": 0.24,
    "right_building_buffer": 0.16,
}


def _require_matplotlib() -> None:
    if plt is None:
        raise RuntimeError("matplotlib is required for spatial visualization")


# ---------------------------------------------------------------------------
# Scene overview
# ---------------------------------------------------------------------------


def plot_scene_with_markers(
    spatial_ctx: SpatialContext,
    placements: Sequence[Any],
    config: Any,
    *,
    osm_geometry: Optional[Dict[str, Any]] = None,
    poi_exclusion_zones: Optional[Sequence[Dict[str, Any]]] = None,
    poi_conflicts: Optional[Sequence[Dict[str, Any]]] = None,
    figsize: Tuple[float, float] = (10, 4),
) -> Any:
    """Bird's-eye XZ plot with junction/entrance markers and placed assets.

    When *osm_geometry* is provided (dict with ``carriageway_rings``,
    ``sidewalk_rings``, ``aoi_bbox_m``), actual OSM polygon shapes are drawn
    instead of the simple template-mode rectangles.
    """
    _require_matplotlib()
    from matplotlib.patches import Circle as MplCircle
    from matplotlib.patches import Polygon as MplPolygon
    from matplotlib.collections import PatchCollection

    fig, ax = plt.subplots(1, 1, figsize=figsize)

    half_len = spatial_ctx.length_m / 2.0
    rw = spatial_ctx.road_half_width_m
    sw = float(getattr(config, "sidewalk_width_m", 2.5))

    # ---- ground geometry ----
    if osm_geometry and "carriageway_rings" in osm_geometry:
        # OSM mode – draw actual polygon shapes
        cw_patches = [MplPolygon(ring, closed=True) for ring in osm_geometry["carriageway_rings"]]
        if cw_patches:
            cw_col = PatchCollection(
                cw_patches, facecolor="#cccccc", edgecolor="#999999",
                alpha=0.6, linewidth=0.5, zorder=1,
            )
            ax.add_collection(cw_col)

        sw_rings = osm_geometry.get("sidewalk_rings", [])
        sw_patches = [MplPolygon(ring, closed=True) for ring in sw_rings]
        if sw_patches:
            sw_col = PatchCollection(
                sw_patches, facecolor="#e8e8e8", edgecolor="#bbbbbb",
                alpha=0.6, linewidth=0.5, zorder=1,
            )
            ax.add_collection(sw_col)

        # Axis limits from AOI
        if "aoi_bbox_m" in osm_geometry:
            minx, miny, maxx, maxy = osm_geometry["aoi_bbox_m"]
            margin = 5.0
            ax.set_xlim(minx - margin, maxx + margin)
            ax.set_ylim(miny - margin, maxy + margin)
    else:
        # Template mode – simple rectangles
        ax.fill_between(
            [-half_len, half_len], -rw, rw,
            color="#cccccc", alpha=0.5, label="Road",
        )
        ax.fill_between(
            [-half_len, half_len], rw, rw + sw,
            color="#e8e8e8", alpha=0.5, label="Sidewalk (L)",
        )
        ax.fill_between(
            [-half_len, half_len], -rw - sw, -rw,
            color="#e8e8e8", alpha=0.5,
        )

    # Junction markers
    for jx, jz in spatial_ctx.junction_points_xz:
        ax.plot(jx, jz, marker="*", color="red", markersize=14, zorder=5)
        ax.annotate(
            f"J({jx:.1f},{jz:.1f})", (jx, jz),
            textcoords="offset points", xytext=(4, 6),
            fontsize=7, color="red",
        )

    # POI markers
    for poi_type, points in _poi_sources(spatial_ctx).items():
        cfg = _poi_marker_cfg(poi_type)
        for px, pz in points:
            ax.plot(px, pz, marker=cfg["marker"], color=cfg["color"], markersize=10, zorder=5)
            ax.annotate(
                f"{poi_type}({px:.1f},{pz:.1f})", (px, pz),
                textcoords="offset points", xytext=(4, 6),
                fontsize=7, color=cfg["color"],
            )

    # Exclusion zone circles
    if poi_exclusion_zones:
        _seen_circles: set = set()
        for zone in poi_exclusion_zones:
            cx, cz = zone["position_xz"]
            r = zone["radius_m"]
            key = (zone["poi_type"], round(cx, 3), round(cz, 3))
            if key in _seen_circles:
                continue
            _seen_circles.add(key)
            fill = _poi_marker_cfg(zone["poi_type"]).get("zone_fill_rgba", (0.5, 0.5, 0.5, 0.10))
            circle = MplCircle(
                (cx, cz), r, fill=True,
                facecolor=fill, edgecolor="red",
                linewidth=1.2, linestyle="--", zorder=3,
            )
            ax.add_patch(circle)

    # Placed assets
    by_cat: Dict[str, List[Tuple[float, float]]] = {}
    for p in placements:
        pos = getattr(p, "position_xyz", None)
        cat = getattr(p, "category", "unknown")
        if pos is not None and len(pos) >= 3:
            by_cat.setdefault(cat, []).append((float(pos[0]), float(pos[2])))
    for cat, pts in sorted(by_cat.items()):
        xs, zs = zip(*pts)
        color = _CATEGORY_COLORS.get(cat, _DEFAULT_COLOR)
        ax.scatter(xs, zs, c=color, s=24, label=cat, zorder=4, edgecolors="k", linewidths=0.3)

    # Highlight assets that violate exclusion zones
    if poi_conflicts:
        for conflict in poi_conflicts:
            cx, cz = conflict["position_xz"]
            ax.plot(cx, cz, marker="X", color="red", markersize=14, zorder=6,
                    markeredgewidth=2)
            rules_str = ", ".join(conflict.get("violated_rules", []))
            ax.annotate(
                rules_str, (cx, cz),
                textcoords="offset points", xytext=(6, -8),
                fontsize=6, color="darkred", fontstyle="italic",
            )

    if osm_geometry:
        ax.set_xlabel("X (easting, m)")
        ax.set_ylabel("Y (northing, m)")
    else:
        ax.set_xlabel("X (along street, m)")
        ax.set_ylabel("Z (lateral, m)")
    ax.set_title("Scene Spatial Overview")
    ax.set_aspect("equal")
    ax.legend(loc="upper right", fontsize=7, ncol=2)
    fig.tight_layout()
    return fig


def plot_zoning_grid_preview(
    zoning_grid: Sequence[Dict[str, Any]],
    *,
    building_footprints: Optional[Sequence[Dict[str, Any]]] = None,
    osm_geometry: Optional[Dict[str, Any]] = None,
    figsize: Tuple[float, float] = (10, 4.4),
) -> Any:
    """Render a bird's-eye zoning grid with theme coloring and footprint overlays."""
    _require_matplotlib()
    from matplotlib.patches import Patch as MplPatch
    from matplotlib.patches import Polygon as MplPolygon
    from matplotlib.collections import PatchCollection

    cells = [dict(cell) for cell in zoning_grid or [] if (cell.get("polygon_xz") or [])]
    if not cells:
        return None

    fig, ax = plt.subplots(1, 1, figsize=figsize)
    cell_xs: List[float] = []
    cell_zs: List[float] = []
    theme_label_points: Dict[str, List[Tuple[float, float]]] = {}
    legend_theme_names: List[str] = []

    for cell in cells:
        polygon = [(float(point[0]), float(point[1])) for point in cell.get("polygon_xz", []) if len(point) >= 2]
        if len(polygon) < 4:
            continue
        theme_name = str(cell.get("theme_name", "") or "commercial")
        lane_role = str(cell.get("lane_role", "") or "")
        face_color = _THEME_COLORS.get(theme_name, "#999999")
        alpha = float(_ROLE_ALPHA.get(lane_role, 0.2))
        patch = MplPolygon(polygon, closed=True)
        patch.set_facecolor(face_color)
        patch.set_edgecolor("#22303a")
        patch.set_linewidth(0.75)
        patch.set_alpha(alpha)
        ax.add_patch(patch)
        cell_xs.extend(float(point[0]) for point in polygon)
        cell_zs.extend(float(point[1]) for point in polygon)
        if theme_name not in legend_theme_names:
            legend_theme_names.append(theme_name)
        if lane_role == "carriageway":
            center = cell.get("center_xz", []) or []
            if len(center) >= 2:
                theme_label_points.setdefault(str(cell.get("theme_id", "") or theme_name), []).append(
                    (float(center[0]), float(center[1]))
                )

    if osm_geometry and "carriageway_rings" in osm_geometry:
        road_patches = [MplPolygon(ring, closed=True) for ring in osm_geometry.get("carriageway_rings", []) or []]
        if road_patches:
            overlay = PatchCollection(
                road_patches,
                facecolor="none",
                edgecolor="#56636a",
                linewidth=1.0,
                alpha=0.45,
                zorder=5,
            )
            ax.add_collection(overlay)

    footprint_list = [dict(item) for item in building_footprints or []]
    for footprint in footprint_list:
        polygon = [(float(point[0]), float(point[1])) for point in footprint.get("polygon_xz", []) if len(point) >= 2]
        if len(polygon) < 4:
            continue
        source = str(footprint.get("source", "") or "osm")
        patch = MplPolygon(polygon, closed=True)
        patch.set_facecolor("#111111")
        patch.set_alpha(0.10 if source == "osm" else 0.04)
        patch.set_edgecolor("#111111")
        patch.set_linewidth(1.0 if source == "osm" else 1.2)
        patch.set_linestyle("--" if source != "osm" else "-")
        ax.add_patch(patch)
        cell_xs.extend(float(point[0]) for point in polygon)
        cell_zs.extend(float(point[1]) for point in polygon)

    for theme_id, points in theme_label_points.items():
        if not points:
            continue
        cx = sum(float(point[0]) for point in points) / len(points)
        cz = sum(float(point[1]) for point in points) / len(points)
        label_theme = next(
            (
                str(cell.get("theme_name", "") or "")
                for cell in cells
                if str(cell.get("theme_id", "") or "") == theme_id
            ),
            "",
        )
        if label_theme:
            ax.text(
                float(cx),
                float(cz),
                label_theme,
                fontsize=8,
                fontweight="bold",
                color="#14213d",
                ha="center",
                va="center",
                bbox={"boxstyle": "round,pad=0.22", "facecolor": "white", "alpha": 0.55, "edgecolor": "none"},
                zorder=8,
            )

    legend_items = [
        MplPatch(facecolor=_THEME_COLORS.get(theme_name, "#999999"), edgecolor="#22303a", alpha=0.30, label=theme_name)
        for theme_name in legend_theme_names
    ]
    if legend_items:
        legend_items.append(MplPatch(facecolor="#111111", edgecolor="#111111", alpha=0.10, label="building footprint"))
        ax.legend(handles=legend_items, loc="upper right", fontsize=7, ncol=min(3, max(len(legend_items), 1)))

    if cell_xs and cell_zs:
        pad_x = max((max(cell_xs) - min(cell_xs)) * 0.04, 4.0)
        pad_z = max((max(cell_zs) - min(cell_zs)) * 0.04, 4.0)
        ax.set_xlim(min(cell_xs) - pad_x, max(cell_xs) + pad_x)
        ax.set_ylim(min(cell_zs) - pad_z, max(cell_zs) + pad_z)
    ax.set_xlabel("X (m)")
    ax.set_ylabel("Z (m)")
    ax.set_title("Theme / Building Zoning Preview")
    ax.set_aspect("equal")
    ax.grid(alpha=0.16, linewidth=0.5)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Distance heatmap
# ---------------------------------------------------------------------------


def plot_distance_heatmap(
    spatial_ctx: SpatialContext,
    placements: Sequence[Any],
    distance_type: str,
    config: Any,
    *,
    resolution: float = 0.5,
    figsize: Tuple[float, float] = (10, 4),
) -> Any:
    """Render a distance heatmap over the street XZ plane.

    Parameters
    ----------
    distance_type : one of "road_edge", "junction", "entrance"
    """
    _require_matplotlib()

    half_len = spatial_ctx.length_m / 2.0
    rw = spatial_ctx.road_half_width_m
    sw = float(getattr(config, "sidewalk_width_m", 2.5))
    z_extent = rw + sw + 1.0

    xs = np.arange(-half_len, half_len + resolution, resolution)
    zs = np.arange(-z_extent, z_extent + resolution, resolution)
    grid = np.zeros((len(zs), len(xs)), dtype=np.float32)

    for iz, z in enumerate(zs):
        for ix, x in enumerate(xs):
            sd = compute_slot_distances((float(x), float(z)), spatial_ctx)
            if distance_type == "road_edge":
                grid[iz, ix] = sd.dist_to_road_edge_m
            elif distance_type == "junction":
                v = sd.dist_to_nearest_junction_m
                grid[iz, ix] = v if math.isfinite(v) else float(np.max(xs) * 2)
            elif distance_type == "entrance":
                v = sd.dist_to_nearest_entrance_m
                grid[iz, ix] = v if math.isfinite(v) else float(np.max(xs) * 2)
            else:
                grid[iz, ix] = 0.0

    fig, ax = plt.subplots(1, 1, figsize=figsize)
    extent = [float(xs[0]), float(xs[-1]), float(zs[0]), float(zs[-1])]
    im = ax.imshow(
        grid, origin="lower", aspect="auto", extent=extent,
        cmap="viridis_r", interpolation="bilinear",
    )
    fig.colorbar(im, ax=ax, label="Distance (m)")

    # Overlay markers
    for jx, jz in spatial_ctx.junction_points_xz:
        ax.plot(jx, jz, marker="*", color="red", markersize=12, zorder=5)
    for ex, ez in spatial_ctx.entrance_points_xz:
        ax.plot(ex, ez, marker="^", color="blue", markersize=9, zorder=5)

    # Overlay placed assets
    for p in placements:
        pos = getattr(p, "position_xyz", None)
        cat = getattr(p, "category", "unknown")
        if pos is not None and len(pos) >= 3:
            color = _CATEGORY_COLORS.get(cat, _DEFAULT_COLOR)
            ax.plot(float(pos[0]), float(pos[2]), "o", color=color, markersize=4, zorder=4)

    label_map = {"road_edge": "Road Edge", "junction": "Junction", "entrance": "Entrance"}
    ax.set_title(f"Distance to {label_map.get(distance_type, distance_type)}")
    ax.set_xlabel("X (m)")
    ax.set_ylabel("Z (m)")
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Distance distribution histograms
# ---------------------------------------------------------------------------


def plot_distance_histograms(
    placements: Sequence[Any],
    spatial_ctx: SpatialContext,
    *,
    figsize: Tuple[float, float] = (10, 3),
) -> Any:
    """Three-subplot histogram of distances across all placed assets."""
    _require_matplotlib()

    edge_dists: List[float] = []
    junc_dists: List[float] = []
    ent_dists: List[float] = []

    for p in placements:
        pos = getattr(p, "position_xyz", None)
        if pos is None or len(pos) < 3:
            continue
        sd = compute_slot_distances((float(pos[0]), float(pos[2])), spatial_ctx)
        edge_dists.append(sd.dist_to_road_edge_m)
        if math.isfinite(sd.dist_to_nearest_junction_m):
            junc_dists.append(sd.dist_to_nearest_junction_m)
        if math.isfinite(sd.dist_to_nearest_entrance_m):
            ent_dists.append(sd.dist_to_nearest_entrance_m)

    fig, axes = plt.subplots(1, 3, figsize=figsize)
    titles = ["Dist to Road Edge", "Dist to Junction", "Dist to Entrance"]
    data = [edge_dists, junc_dists, ent_dists]
    colors = ["#4caf50", "#f44336", "#2196f3"]

    for ax, title, vals, color in zip(axes, titles, data, colors):
        if vals:
            ax.hist(vals, bins=15, color=color, alpha=0.7, edgecolor="k", linewidth=0.5)
        ax.set_title(title, fontsize=9)
        ax.set_xlabel("m", fontsize=8)
        ax.set_ylabel("Count", fontsize=8)
        ax.tick_params(labelsize=7)

    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# POI exclusion zone dedicated overview
# ---------------------------------------------------------------------------

# Marker / color config for POI types
def _poi_marker_cfg(poi_type: str) -> Dict[str, Any]:
    cfg = poi_plot_config(poi_type)
    return {
        "marker": cfg["marker"],
        "color": cfg["color"],
        "label": cfg["label"],
        "zone_fill_rgba": cfg["zone_fill_rgba"],
    }


def _poi_sources(spatial_ctx: SpatialContext) -> Dict[str, Tuple[Tuple[float, float], ...]]:
    mapping = nonempty_poi_points(getattr(spatial_ctx, "poi_points_by_type_xz", {}) or {})
    if mapping:
        return mapping
    fallback: Dict[str, Tuple[Tuple[float, float], ...]] = {}
    if getattr(spatial_ctx, "entrance_points_xz", ()):
        fallback["entrance"] = tuple(spatial_ctx.entrance_points_xz)
    if getattr(spatial_ctx, "bus_stop_points_xz", ()):
        fallback["bus_stop"] = tuple(spatial_ctx.bus_stop_points_xz)
    if getattr(spatial_ctx, "fire_points_xz", ()):
        fallback["fire_hydrant"] = tuple(spatial_ctx.fire_points_xz)
    return fallback


def plot_poi_exclusion_overview(
    spatial_ctx: SpatialContext,
    placements: Sequence[Any],
    config: Any,
    poi_exclusion_zones: Sequence[Dict[str, Any]],
    poi_conflicts: Sequence[Dict[str, Any]],
    *,
    osm_geometry: Optional[Dict[str, Any]] = None,
    figsize: Tuple[float, float] = (10, 6),
) -> Any:
    """Dedicated POI exclusion zone visualization with markers, radii, and conflict highlights."""
    _require_matplotlib()
    from matplotlib.patches import Circle as MplCircle
    from matplotlib.patches import Polygon as MplPolygon
    from matplotlib.collections import PatchCollection

    fig, ax = plt.subplots(1, 1, figsize=figsize)

    half_len = spatial_ctx.length_m / 2.0
    rw = spatial_ctx.road_half_width_m
    sw = float(getattr(config, "sidewalk_width_m", 2.5))

    # Ground geometry
    if osm_geometry and "carriageway_rings" in osm_geometry:
        cw_patches = [MplPolygon(ring, closed=True) for ring in osm_geometry["carriageway_rings"]]
        if cw_patches:
            ax.add_collection(PatchCollection(
                cw_patches, facecolor="#cccccc", edgecolor="#999999",
                alpha=0.4, linewidth=0.5, zorder=1,
            ))
        sw_patches = [MplPolygon(ring, closed=True) for ring in osm_geometry.get("sidewalk_rings", [])]
        if sw_patches:
            ax.add_collection(PatchCollection(
                sw_patches, facecolor="#e8e8e8", edgecolor="#bbbbbb",
                alpha=0.4, linewidth=0.5, zorder=1,
            ))
        if "aoi_bbox_m" in osm_geometry:
            minx, miny, maxx, maxy = osm_geometry["aoi_bbox_m"]
            margin = 5.0
            ax.set_xlim(minx - margin, maxx + margin)
            ax.set_ylim(miny - margin, maxy + margin)
    else:
        ax.fill_between([-half_len, half_len], -rw, rw, color="#cccccc", alpha=0.3)
        ax.fill_between([-half_len, half_len], rw, rw + sw, color="#e8e8e8", alpha=0.3)
        ax.fill_between([-half_len, half_len], -rw - sw, -rw, color="#e8e8e8", alpha=0.3)

    # Draw exclusion zone circles (under markers)
    seen_circles: set = set()
    radii_legend: Dict[str, float] = {}
    for zone in poi_exclusion_zones:
        cx, cz = zone["position_xz"]
        r = zone["radius_m"]
        poi_type = zone["poi_type"]
        key = (poi_type, round(cx, 3), round(cz, 3))
        if key in seen_circles:
            continue
        seen_circles.add(key)
        radii_legend[poi_type] = r
        fill = _poi_marker_cfg(poi_type).get("zone_fill_rgba", (0.5, 0.5, 0.5, 0.10))
        circle = MplCircle(
            (cx, cz), r, fill=True,
            facecolor=fill, edgecolor="red",
            linewidth=1.5, linestyle="--", zorder=2,
        )
        ax.add_patch(circle)
        ax.annotate(
            f"{r:.1f}m", (cx + r * 0.7, cz + r * 0.7),
            fontsize=6, color="red", alpha=0.8,
        )

    # POI markers (all types)
    poi_sources = _poi_sources(spatial_ctx)
    for poi_type, points in poi_sources.items():
        cfg = _poi_marker_cfg(poi_type)
        for px, pz in points:
            ax.plot(px, pz, marker=cfg["marker"], color=cfg["color"],
                    markersize=12, zorder=5)

    # Placed assets (dimmed)
    by_cat: Dict[str, List[Tuple[float, float]]] = {}
    for p in placements:
        pos = getattr(p, "position_xyz", None)
        cat = getattr(p, "category", "unknown")
        if pos is not None and len(pos) >= 3:
            by_cat.setdefault(cat, []).append((float(pos[0]), float(pos[2])))
    for cat, pts in sorted(by_cat.items()):
        xs, zs = zip(*pts)
        color = _CATEGORY_COLORS.get(cat, _DEFAULT_COLOR)
        ax.scatter(xs, zs, c=color, s=18, alpha=0.6, zorder=4,
                   edgecolors="k", linewidths=0.2)

    # Conflict highlights
    conflict_positions = set()
    for conflict in poi_conflicts:
        cx, cz = conflict["position_xz"]
        conflict_positions.add((round(cx, 3), round(cz, 3)))
        ax.plot(cx, cz, marker="X", color="red", markersize=16, zorder=6,
                markeredgewidth=2.5)
        rules = ", ".join(conflict.get("violated_rules", []))
        ax.annotate(
            f"{conflict.get('category', '')} [{rules}]",
            (cx, cz), textcoords="offset points", xytext=(8, -10),
            fontsize=6, color="darkred", fontstyle="italic",
            bbox=dict(boxstyle="round,pad=0.2", fc="white", alpha=0.7, ec="red", lw=0.5),
        )

    # Build legend
    from matplotlib.lines import Line2D
    handles = []
    for poi_type in sorted(set(poi_sources.keys()) | set(radii_legend.keys())):
        cfg = _poi_marker_cfg(poi_type)
        r_val = radii_legend.get(poi_type)
        lbl = cfg["label"]
        if r_val is not None:
            lbl += f" (r={r_val:.1f}m)"
        handles.append(Line2D([0], [0], marker=cfg["marker"], color="w",
                              markerfacecolor=cfg["color"], markersize=8, label=lbl))
    if conflict_positions:
        handles.append(Line2D([0], [0], marker="X", color="w",
                              markerfacecolor="red", markersize=8,
                              label=f"Violation ({len(conflict_positions)})"))
    ax.legend(handles=handles, loc="upper right", fontsize=7, framealpha=0.9)

    ax.set_title("POI Exclusion Zone Analysis")
    ax.set_aspect("equal")
    if osm_geometry:
        ax.set_xlabel("X (easting, m)")
        ax.set_ylabel("Y (northing, m)")
    else:
        ax.set_xlabel("X (along street, m)")
        ax.set_ylabel("Z (lateral, m)")
    fig.tight_layout()
    return fig
