"""High-quality raster top-down renderer for presentation views."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from .types import BuildingFootprint, GeneratedLot, StreetComposeConfig

_ASSET_ROOT = Path(__file__).resolve().parents[2] / "assets" / "topdown"
_TILE_PATHS = {
    "context_ground": _ASSET_ROOT / "tiles" / "plaza_concrete.png",
    "carriageway": _ASSET_ROOT / "tiles" / "asphalt_base.png",
    "sidewalk": _ASSET_ROOT / "tiles" / "sidewalk_pavers.png",
    "furnishing": _ASSET_ROOT / "tiles" / "plaza_concrete.png",
    "clear_path": _ASSET_ROOT / "tiles" / "sidewalk_pavers.png",
    "grass": _ASSET_ROOT / "tiles" / "grass_soft.png",
    "tree_pit": _ASSET_ROOT / "tiles" / "tree_pit.png",
}
_OVERLAY_PATHS = {
    "crosswalk": _ASSET_ROOT / "overlays" / "crosswalk_stripes.png",
    "lane_dashes": _ASSET_ROOT / "overlays" / "lane_dashes.png",
}
_SPRITE_PATHS = {
    "tree": _ASSET_ROOT / "sprites" / "tree_canopy_01.png",
    "bench": _ASSET_ROOT / "sprites" / "bench_top_01.png",
    "lamp": _ASSET_ROOT / "sprites" / "lamp_top_01.png",
    "bus_stop": _ASSET_ROOT / "sprites" / "bus_stop_top_01.png",
    "trash": _ASSET_ROOT / "sprites" / "trash_top_01.png",
    "mailbox": _ASSET_ROOT / "sprites" / "trash_top_01.png",
    "hydrant": _ASSET_ROOT / "sprites" / "trash_top_01.png",
    "bollard": _ASSET_ROOT / "sprites" / "lamp_top_01.png",
}
_SPRITE_SIZE_M = {
    "tree": (3.6, 3.6),
    "bench": (2.1, 0.9),
    "lamp": (0.55, 0.55),
    "bus_stop": (4.6, 1.8),
    "trash": (0.8, 0.8),
    "mailbox": (0.9, 0.9),
    "hydrant": (0.7, 0.7),
    "bollard": (0.45, 0.45),
}
_BAND_ROLE_TILES = {
    "left_furnishing": "furnishing",
    "right_furnishing": "furnishing",
    "left_clear_path": "clear_path",
    "right_clear_path": "clear_path",
    "left_transit_edge": "furnishing",
    "right_transit_edge": "furnishing",
    "left_building_buffer": "grass",
    "right_building_buffer": "grass",
}


@dataclass(frozen=True)
class _Viewport:
    min_x: float
    min_z: float
    max_x: float
    max_z: float
    canvas_px: int
    margin_px: int
    scale_px_per_m: float

    def world_to_pixel(self, x: float, z: float) -> Tuple[float, float]:
        px = self.margin_px + (float(x) - self.min_x) * self.scale_px_per_m
        py = self.canvas_px - self.margin_px - (float(z) - self.min_z) * self.scale_px_per_m
        return float(px), float(py)

    def size_to_px(self, value_m: float) -> int:
        return max(1, int(round(float(value_m) * self.scale_px_per_m)))


def _require_pillow():
    try:
        from PIL import Image, ImageChops, ImageDraw, ImageFilter
    except Exception:
        return None
    return Image, ImageChops, ImageDraw, ImageFilter


def _coerce_rgba(color: Sequence[int] | None, default: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    if not color:
        return default
    values = list(color)[:4]
    while len(values) < 4:
        values.append(255)
    return tuple(max(0, min(255, int(round(v)))) for v in values)  # type: ignore[return-value]


def _layout_bounds(layout_payload: Mapping[str, Any]) -> Tuple[float, float, float, float]:
    summary = dict(layout_payload.get("summary", {}) or {})
    osm_geometry = dict(summary.get("osm_geometry", {}) or {})
    bbox = osm_geometry.get("aoi_bbox_m")
    if bbox and len(bbox) == 4:
        return float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])

    xs: List[float] = []
    zs: List[float] = []
    for polygon_payload in layout_payload.get("building_footprints", []) or []:
        for point in polygon_payload.get("polygon_xz", []) or []:
            if len(point) >= 2:
                xs.append(float(point[0]))
                zs.append(float(point[1]))
    for polygon_payload in layout_payload.get("generated_lots", []) or []:
        for point in polygon_payload.get("polygon_xz", []) or []:
            if len(point) >= 2:
                xs.append(float(point[0]))
                zs.append(float(point[1]))
    for placement in layout_payload.get("placements", []) or []:
        pos = placement.get("position_xyz", []) or []
        if len(pos) >= 3:
            xs.append(float(pos[0]))
            zs.append(float(pos[2]))
    if xs and zs:
        return min(xs) - 6.0, min(zs) - 6.0, max(xs) + 6.0, max(zs) + 6.0

    length_m = float(summary.get("length_m", 80.0))
    road_width_m = float(summary.get("road_width_m", 8.0))
    sidewalk_width_m = float(summary.get("sidewalk_width_m", 2.5))
    return (
        -length_m / 2.0 - 4.0,
        -(road_width_m / 2.0 + sidewalk_width_m + 4.0),
        length_m / 2.0 + 4.0,
        road_width_m / 2.0 + sidewalk_width_m + 4.0,
    )


def _viewport_from_layout(layout_payload: Mapping[str, Any], *, canvas_px: int) -> _Viewport:
    min_x, min_z, max_x, max_z = _layout_bounds(layout_payload)
    width_m = max(max_x - min_x, 1.0)
    depth_m = max(max_z - min_z, 1.0)
    margin_px = max(48, int(round(canvas_px * 0.05)))
    scale = min(
        float(canvas_px - margin_px * 2) / width_m,
        float(canvas_px - margin_px * 2) / depth_m,
    )
    return _Viewport(
        min_x=float(min_x),
        min_z=float(min_z),
        max_x=float(max_x),
        max_z=float(max_z),
        canvas_px=int(canvas_px),
        margin_px=int(margin_px),
        scale_px_per_m=float(max(scale, 1.0)),
    )


def _polygon_to_pixels(viewport: _Viewport, polygon_xz: Sequence[Sequence[float]]) -> List[Tuple[int, int]]:
    pixels: List[Tuple[int, int]] = []
    for point in polygon_xz:
        if len(point) < 2:
            continue
        px, py = viewport.world_to_pixel(float(point[0]), float(point[1]))
        pixels.append((int(round(px)), int(round(py))))
    return pixels


def _tile_image(base_tile, size: Tuple[int, int]):
    width, height = size
    tile_width, tile_height = base_tile.size
    out = base_tile.copy().resize((max(tile_width, 2), max(tile_height, 2)))
    reps_x = max(1, int(math.ceil(width / max(out.size[0], 1))))
    reps_y = max(1, int(math.ceil(height / max(out.size[1], 1))))
    tiled = np.tile(np.asarray(out), (reps_y, reps_x, 1))
    return tiled[:height, :width]


def _load_image(path: Path):
    pillow = _require_pillow()
    if pillow is None:
        return None
    Image, _, _, _ = pillow
    if not path.exists():
        return None
    return Image.open(path).convert("RGBA")


def _build_polygon_mask(size: Tuple[int, int], polygons: Sequence[Sequence[Tuple[int, int]]]):
    pillow = _require_pillow()
    if pillow is None:
        return None
    Image, _, ImageDraw, _ = pillow
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for polygon in polygons:
        if len(polygon) >= 3:
            draw.polygon(list(polygon), fill=255)
    return mask


def _blend_tiled_polygons(canvas, *, tile_name: str, polygons: Sequence[Sequence[Tuple[int, int]]], tint: Tuple[int, int, int, int], alpha: float = 1.0) -> bool:
    pillow = _require_pillow()
    if pillow is None or not polygons:
        return False
    Image, _, _, _ = pillow
    tile = _load_image(_TILE_PATHS[tile_name])
    if tile is None:
        return False
    mask = _build_polygon_mask(canvas.size, polygons)
    if mask is None:
        return False
    tiled = _tile_image(tile, canvas.size)
    layer = Image.fromarray(tiled)
    if tint != (255, 255, 255, 255):
        tint_layer = Image.new("RGBA", canvas.size, tint)
        layer = Image.blend(layer, tint_layer, max(0.0, min(1.0, 1.0 - alpha * 0.55)))
    canvas.alpha_composite(Image.composite(layer, Image.new("RGBA", canvas.size, (0, 0, 0, 0)), mask))
    return True


def _draw_soft_polygons(canvas, *, polygons: Sequence[Sequence[Tuple[int, int]]], fill: Tuple[int, int, int, int], blur_radius: float = 0.0, offset_px: Tuple[int, int] = (0, 0)) -> None:
    pillow = _require_pillow()
    if pillow is None or not polygons:
        return
    Image, _, ImageDraw, ImageFilter = pillow
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    ox, oy = offset_px
    for polygon in polygons:
        if len(polygon) < 3:
            continue
        shifted = [(int(x + ox), int(y + oy)) for x, y in polygon]
        draw.polygon(shifted, fill=fill)
    if blur_radius > 0.0:
        layer = layer.filter(ImageFilter.GaussianBlur(radius=float(blur_radius)))
    canvas.alpha_composite(layer)


def _draw_overlay_strip(canvas, *, tile_name: str, polygon: Sequence[Tuple[int, int]], opacity: int = 180) -> bool:
    pillow = _require_pillow()
    if pillow is None or len(polygon) < 3:
        return False
    Image, _, _, _ = pillow
    overlay = _load_image(_OVERLAY_PATHS[tile_name])
    if overlay is None:
        return False
    mask = _build_polygon_mask(canvas.size, [polygon])
    if mask is None:
        return False
    layer = Image.fromarray(_tile_image(overlay, canvas.size))
    alpha = np.asarray(layer.getchannel("A"), dtype=np.uint8)
    alpha = np.minimum(alpha, np.full_like(alpha, max(0, min(255, opacity))))
    layer.putalpha(Image.fromarray(alpha))
    canvas.alpha_composite(Image.composite(layer, Image.new("RGBA", canvas.size, (0, 0, 0, 0)), mask))
    return True


def _road_and_sidewalk_polygons(layout_payload: Mapping[str, Any], viewport: _Viewport) -> Dict[str, List[List[Tuple[int, int]]]]:
    summary = dict(layout_payload.get("summary", {}) or {})
    osm_geometry = dict(summary.get("osm_geometry", {}) or {})
    result: Dict[str, List[List[Tuple[int, int]]]] = {
        "carriageway": [],
        "sidewalk": [],
        "left_sidewalk": [],
        "right_sidewalk": [],
    }
    if osm_geometry.get("carriageway_rings"):
        for ring in osm_geometry.get("carriageway_rings", []) or []:
            result["carriageway"].append(_polygon_to_pixels(viewport, ring))
        for ring in osm_geometry.get("sidewalk_rings", []) or []:
            result["sidewalk"].append(_polygon_to_pixels(viewport, ring))
        for ring in osm_geometry.get("left_sidewalk_rings", []) or []:
            result["left_sidewalk"].append(_polygon_to_pixels(viewport, ring))
        for ring in osm_geometry.get("right_sidewalk_rings", []) or []:
            result["right_sidewalk"].append(_polygon_to_pixels(viewport, ring))
        return result

    bounds = _layout_bounds(layout_payload)
    road_width = float(summary.get("road_width_m", 8.0))
    sidewalk_width = float(summary.get("sidewalk_width_m", 2.5))
    result["carriageway"].append(
        _polygon_to_pixels(
            viewport,
            [
                (bounds[0], -road_width / 2.0),
                (bounds[2], -road_width / 2.0),
                (bounds[2], road_width / 2.0),
                (bounds[0], road_width / 2.0),
            ],
        )
    )
    left_polygon = [
        (bounds[0], road_width / 2.0),
        (bounds[2], road_width / 2.0),
        (bounds[2], road_width / 2.0 + sidewalk_width),
        (bounds[0], road_width / 2.0 + sidewalk_width),
    ]
    right_polygon = [
        (bounds[0], -road_width / 2.0 - sidewalk_width),
        (bounds[2], -road_width / 2.0 - sidewalk_width),
        (bounds[2], -road_width / 2.0),
        (bounds[0], -road_width / 2.0),
    ]
    result["sidewalk"].append(_polygon_to_pixels(viewport, left_polygon))
    result["sidewalk"].append(_polygon_to_pixels(viewport, right_polygon))
    result["left_sidewalk"].append(_polygon_to_pixels(viewport, left_polygon))
    result["right_sidewalk"].append(_polygon_to_pixels(viewport, right_polygon))
    return result


def _zone_polygons(layout_payload: Mapping[str, Any], viewport: _Viewport) -> Dict[str, List[List[Tuple[int, int]]]]:
    polygons_by_role: Dict[str, List[List[Tuple[int, int]]]] = {}
    for cell in layout_payload.get("zoning_grid", []) or []:
        polygon = _polygon_to_pixels(viewport, cell.get("polygon_xz", []) or [])
        if len(polygon) < 3:
            continue
        lane_role = str(cell.get("lane_role", "") or "")
        if lane_role in _BAND_ROLE_TILES:
            polygons_by_role.setdefault(lane_role, []).append(polygon)
        if str(cell.get("land_use_type", "") or "") == "green":
            polygons_by_role.setdefault("green_land_use", []).append(polygon)
    return polygons_by_role


def _building_polygons(layout_payload: Mapping[str, Any], viewport: _Viewport) -> List[List[Tuple[int, int]]]:
    polygons: List[List[Tuple[int, int]]] = []
    for item in layout_payload.get("building_footprints", []) or []:
        polygon = _polygon_to_pixels(viewport, item.get("polygon_xz", []) or [])
        if len(polygon) >= 3:
            polygons.append(polygon)
    if not polygons:
        for item in layout_payload.get("generated_lots", []) or []:
            polygon = _polygon_to_pixels(viewport, item.get("polygon_xz", []) or [])
            if len(polygon) >= 3:
                polygons.append(polygon)
    return polygons


def _draw_buildings(canvas, *, polygons: Sequence[Sequence[Tuple[int, int]]]) -> None:
    if not polygons:
        return
    _draw_soft_polygons(
        canvas,
        polygons=polygons,
        fill=(35, 42, 51, 55),
        blur_radius=6.0,
        offset_px=(8, 8),
    )
    _draw_soft_polygons(
        canvas,
        polygons=polygons,
        fill=(226, 223, 214, 214),
    )
    _draw_soft_polygons(
        canvas,
        polygons=polygons,
        fill=(248, 247, 243, 120),
        blur_radius=0.0,
        offset_px=(-2, -2),
    )


def _sprite_size_px(viewport: _Viewport, category: str) -> Tuple[int, int]:
    width_m, depth_m = _SPRITE_SIZE_M.get(category, (1.0, 1.0))
    return max(12, viewport.size_to_px(width_m)), max(12, viewport.size_to_px(depth_m))


def _draw_tree_pit(canvas, *, viewport: _Viewport, x: float, z: float) -> None:
    pillow = _require_pillow()
    if pillow is None:
        return
    Image, _, _, _ = pillow
    tile = _load_image(_TILE_PATHS["tree_pit"])
    if tile is None:
        return
    pit_diameter_px = max(18, viewport.size_to_px(1.4))
    px, py = viewport.world_to_pixel(x, z)
    left = int(round(px - pit_diameter_px / 2.0))
    top = int(round(py - pit_diameter_px / 2.0))
    mask = Image.new("L", canvas.size, 0)
    _, _, ImageDraw, _ = pillow
    ImageDraw.Draw(mask).ellipse(
        [left, top, left + pit_diameter_px, top + pit_diameter_px],
        fill=255,
    )
    layer = Image.fromarray(_tile_image(tile, canvas.size))
    canvas.alpha_composite(Image.composite(layer, Image.new("RGBA", canvas.size, (0, 0, 0, 0)), mask))


def _draw_sprite(canvas, *, viewport: _Viewport, category: str, x: float, z: float, yaw_deg: float) -> bool:
    pillow = _require_pillow()
    if pillow is None:
        return False
    Image, _, _, ImageFilter = pillow
    sprite = _load_image(_SPRITE_PATHS.get(category, _SPRITE_PATHS["trash"]))
    if sprite is None:
        return False
    target_size = _sprite_size_px(viewport, category)
    sprite = sprite.resize(target_size, resample=Image.Resampling.LANCZOS)
    sprite = sprite.rotate(-float(yaw_deg), resample=Image.Resampling.BICUBIC, expand=True)

    px, py = viewport.world_to_pixel(x, z)
    left = int(round(px - sprite.size[0] / 2.0))
    top = int(round(py - sprite.size[1] / 2.0))

    shadow = Image.new("RGBA", sprite.size, (0, 0, 0, 0))
    alpha = sprite.getchannel("A").point(lambda value: int(value * 0.35))
    shadow.putalpha(alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=max(2.0, min(sprite.size) * 0.04)))
    canvas.alpha_composite(shadow, dest=(left + 4, top + 5))
    canvas.alpha_composite(sprite, dest=(left, top))
    return True


def _draw_furniture(canvas, *, layout_payload: Mapping[str, Any], viewport: _Viewport) -> None:
    for placement in layout_payload.get("placements", []) or []:
        pos = placement.get("position_xyz", []) or []
        if len(pos) < 3:
            continue
        category = str(placement.get("category", "") or "")
        if category == "tree":
            _draw_tree_pit(canvas, viewport=viewport, x=float(pos[0]), z=float(pos[2]))
        _draw_sprite(
            canvas,
            viewport=viewport,
            category=category,
            x=float(pos[0]),
            z=float(pos[2]),
            yaw_deg=float(placement.get("yaw_deg", 0.0) or 0.0),
        )


def _draw_template_markings(canvas, *, layout_payload: Mapping[str, Any], viewport: _Viewport) -> None:
    summary = dict(layout_payload.get("summary", {}) or {})
    if dict(summary.get("osm_geometry", {}) or {}).get("carriageway_rings"):
        return
    road_width = float(summary.get("road_width_m", 8.0))
    length_m = float(summary.get("length_m", layout_payload.get("config", {}).get("length_m", 80.0)))
    lane_strip_half = max(0.1, road_width * 0.03)
    dash_polygon = _polygon_to_pixels(
        viewport,
        [
            (-length_m / 2.0, -lane_strip_half),
            (length_m / 2.0, -lane_strip_half),
            (length_m / 2.0, lane_strip_half),
            (-length_m / 2.0, lane_strip_half),
        ],
    )
    _draw_overlay_strip(canvas, tile_name="lane_dashes", polygon=dash_polygon, opacity=210)

    poi_points = (((summary.get("spatial_context", {}) or {}).get("poi_points_by_type_xz", {}) or {}).get("crossing", []) or [])
    for point in poi_points:
        if len(point) < 2:
            continue
        x = float(point[0])
        crosswalk = _polygon_to_pixels(
            viewport,
            [
                (x - 1.8, -road_width / 2.0 - 0.6),
                (x + 1.8, -road_width / 2.0 - 0.6),
                (x + 1.8, road_width / 2.0 + 0.6),
                (x - 1.8, road_width / 2.0 + 0.6),
            ],
        )
        _draw_overlay_strip(canvas, tile_name="crosswalk", polygon=crosswalk, opacity=220)


def _assets_available() -> bool:
    required = [
        *_TILE_PATHS.values(),
        *_OVERLAY_PATHS.values(),
        _SPRITE_PATHS["tree"],
        _SPRITE_PATHS["bench"],
        _SPRITE_PATHS["lamp"],
        _SPRITE_PATHS["bus_stop"],
        _SPRITE_PATHS["trash"],
    ]
    return all(path.exists() for path in required)


def render_design_topdown(
    layout_payload: Mapping[str, Any],
    *,
    out_dir: Path,
    config: StreetComposeConfig,
    palette: Mapping[str, Tuple[int, int, int, int]],
) -> Optional[Dict[str, str]]:
    """Render a textured top-down overview or return ``None`` when unavailable."""
    pillow = _require_pillow()
    if pillow is None or not _assets_available():
        return None
    Image, ImageChops, _, ImageFilter = pillow

    canvas_px = int(max(512, getattr(config, "topdown_canvas_px", 2048)))
    viewport = _viewport_from_layout(layout_payload, canvas_px=canvas_px)
    out_dir = Path(out_dir).resolve()
    view_dir = out_dir / "presentation_views"
    view_dir.mkdir(parents=True, exist_ok=True)

    background = Image.new("RGBA", (canvas_px, canvas_px), _coerce_rgba(palette.get("context_ground"), (227, 223, 214, 255)))
    if not _blend_tiled_polygons(
        background,
        tile_name="context_ground",
        polygons=[[(0, 0), (canvas_px, 0), (canvas_px, canvas_px), (0, canvas_px)]],
        tint=(255, 255, 255, 255),
        alpha=1.0,
    ):
        return None

    road_polygons = _road_and_sidewalk_polygons(layout_payload, viewport)
    zone_polygons = _zone_polygons(layout_payload, viewport)

    _blend_tiled_polygons(
        background,
        tile_name="carriageway",
        polygons=road_polygons["carriageway"],
        tint=_coerce_rgba(palette.get("carriageway"), (79, 84, 93, 255)),
        alpha=0.92,
    )
    _draw_soft_polygons(
        background,
        polygons=road_polygons["carriageway"],
        fill=(30, 35, 41, 32),
        blur_radius=7.0,
        offset_px=(0, 3),
    )

    _blend_tiled_polygons(
        background,
        tile_name="sidewalk",
        polygons=road_polygons["sidewalk"],
        tint=_coerce_rgba(palette.get("sidewalk"), (214, 210, 201, 255)),
        alpha=0.96,
    )
    for lane_role, tile_name in _BAND_ROLE_TILES.items():
        _blend_tiled_polygons(
            background,
            tile_name=tile_name,
            polygons=zone_polygons.get(lane_role, []),
            tint=_coerce_rgba(
                palette.get("furnishing" if tile_name == "furnishing" else "clear_path"),
                (209, 204, 195, 255),
            ),
            alpha=0.94 if tile_name == "clear_path" else 0.90,
        )
    _blend_tiled_polygons(
        background,
        tile_name="grass",
        polygons=zone_polygons.get("green_land_use", []),
        tint=(197, 212, 174, 255),
        alpha=0.98,
    )

    _draw_template_markings(background, layout_payload=layout_payload, viewport=viewport)

    buildings = _building_polygons(layout_payload, viewport)
    _draw_buildings(background, polygons=buildings)
    _draw_furniture(background, layout_payload=layout_payload, viewport=viewport)

    # Subtle vignette so the drawing reads more like a design board than a GIS export.
    gradient = Image.linear_gradient("L").resize((canvas_px, canvas_px))
    gradient = gradient.rotate(90, expand=False)
    vignette = ImageChops.screen(gradient, gradient.transpose(Image.Transpose.FLIP_TOP_BOTTOM))
    vignette = vignette.filter(ImageFilter.GaussianBlur(radius=max(12.0, canvas_px / 120.0)))
    vignette_rgba = Image.new("RGBA", background.size, (255, 248, 240, 0))
    vignette_rgba.putalpha(vignette.point(lambda value: int(value * 0.16)))
    background.alpha_composite(vignette_rgba)

    out_path = (view_dir / "overview_top_design.png").resolve()
    background.save(out_path)
    return {"name": "overview_top_design", "title": "Overview Top Design", "path": str(out_path)}


def render_design_zoning_companion(
    *,
    out_path: Path,
    config: StreetComposeConfig,
    palette: Mapping[str, Tuple[int, int, int, int]],
    zoning_grid: Sequence[Mapping[str, Any]],
    building_footprints: Sequence[BuildingFootprint],
    generated_lots: Sequence[GeneratedLot],
    osm_geometry: Mapping[str, object] | None,
) -> Optional[str]:
    """Render a textured zoning-stage companion image or return ``None``."""
    pillow = _require_pillow()
    if pillow is None or not _assets_available():
        return None
    Image, _, ImageDraw, ImageFilter = pillow

    layout_payload: Dict[str, Any] = {
        "summary": {
            "road_width_m": float(getattr(config, "road_width_m", 8.0)),
            "sidewalk_width_m": float(getattr(config, "sidewalk_width_m", 2.5)),
            "length_m": float(getattr(config, "length_m", 80.0)),
            "osm_geometry": dict(osm_geometry or {}),
        },
        "zoning_grid": [dict(cell) for cell in zoning_grid],
        "building_footprints": [footprint.to_dict() for footprint in building_footprints],
        "generated_lots": [lot.to_dict() for lot in generated_lots],
        "placements": [],
    }
    canvas_px = int(max(512, getattr(config, "topdown_canvas_px", 2048)))
    viewport = _viewport_from_layout(layout_payload, canvas_px=canvas_px)
    background = Image.new("RGBA", (canvas_px, canvas_px), _coerce_rgba(palette.get("context_ground"), (227, 223, 214, 255)))
    _blend_tiled_polygons(
        background,
        tile_name="context_ground",
        polygons=[[(0, 0), (canvas_px, 0), (canvas_px, canvas_px), (0, canvas_px)]],
        tint=(255, 255, 255, 255),
        alpha=1.0,
    )
    road_polygons = _road_and_sidewalk_polygons(layout_payload, viewport)
    zone_polygons = _zone_polygons(layout_payload, viewport)

    _blend_tiled_polygons(
        background,
        tile_name="carriageway",
        polygons=road_polygons["carriageway"],
        tint=_coerce_rgba(palette.get("carriageway"), (79, 84, 93, 255)),
        alpha=0.92,
    )
    _blend_tiled_polygons(
        background,
        tile_name="sidewalk",
        polygons=road_polygons["sidewalk"],
        tint=_coerce_rgba(palette.get("sidewalk"), (214, 210, 201, 255)),
        alpha=0.96,
    )
    for lane_role, tile_name in _BAND_ROLE_TILES.items():
        _blend_tiled_polygons(
            background,
            tile_name=tile_name,
            polygons=zone_polygons.get(lane_role, []),
            tint=_coerce_rgba(
                palette.get("furnishing" if tile_name == "furnishing" else "clear_path"),
                (209, 204, 195, 255),
            ),
            alpha=0.92 if tile_name == "clear_path" else 0.88,
        )
    _blend_tiled_polygons(
        background,
        tile_name="grass",
        polygons=zone_polygons.get("green_land_use", []),
        tint=(198, 213, 176, 255),
        alpha=0.98,
    )

    lot_polygons = [
        _polygon_to_pixels(viewport, lot.get("polygon_xz", []) or [])
        for lot in layout_payload.get("generated_lots", []) or []
    ]
    lot_polygons = [polygon for polygon in lot_polygons if len(polygon) >= 3]
    if lot_polygons:
        _draw_soft_polygons(
            background,
            polygons=lot_polygons,
            fill=(55, 109, 162, 28),
        )
        overlay = Image.new("RGBA", background.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        for polygon in lot_polygons:
            draw.line(list(polygon) + [polygon[0]], fill=(55, 109, 162, 110), width=4)
        background.alpha_composite(overlay)

    building_polygons = _building_polygons(layout_payload, viewport)
    _draw_soft_polygons(
        background,
        polygons=building_polygons,
        fill=(35, 42, 51, 44),
        blur_radius=5.0,
        offset_px=(6, 6),
    )
    _draw_soft_polygons(
        background,
        polygons=building_polygons,
        fill=(238, 235, 227, 172),
    )

    label_layer = Image.new("RGBA", background.size, (0, 0, 0, 0))
    label_draw = ImageDraw.Draw(label_layer)
    for cell in layout_payload.get("zoning_grid", []) or []:
        if str(cell.get("lane_role", "") or "") != "carriageway":
            continue
        center = cell.get("center_xz", []) or []
        if len(center) < 2:
            continue
        theme_name = str(cell.get("theme_name", "") or "").strip().lower()
        if not theme_name:
            continue
        px, py = viewport.world_to_pixel(float(center[0]), float(center[1]))
        label = theme_name[:1].upper()
        label_draw.ellipse(
            [px - 18, py - 18, px + 18, py + 18],
            fill=(255, 255, 255, 170),
            outline=(78, 88, 98, 90),
            width=2,
        )
        label_draw.text((px - 5, py - 9), label, fill=(40, 49, 58, 220))
    label_layer = label_layer.filter(ImageFilter.GaussianBlur(radius=0.2))
    background.alpha_composite(label_layer)

    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    background.save(out_path)
    return str(out_path)


__all__ = ["render_design_topdown", "render_design_zoning_companion", "_viewport_from_layout"]
