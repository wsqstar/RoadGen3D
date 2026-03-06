"""Placement zone construction from OSM road geometry for M5."""

from __future__ import annotations

import json
import logging
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _require_shapely():
    try:
        import shapely
    except ImportError as exc:
        raise RuntimeError(
            "`shapely` is required for M5 placement zones. Install requirements-m5.txt."
        ) from exc
    return shapely


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class PlacementContext:
    """Geometric context for OSM-based placement."""

    sidewalk_zone: Any  # shapely (Multi)Polygon – region where furniture can be placed
    carriageway: Any  # shapely (Multi)Polygon – road surface
    entrance_points: List[Tuple[float, float]] = field(default_factory=list)  # (x, y) local metres
    bus_stop_points: List[Tuple[float, float]] = field(default_factory=list)
    fire_points: List[Tuple[float, float]] = field(default_factory=list)
    aoi_polygon: Any = None  # shapely Polygon – bounding box polygon
    origin_offset: Tuple[float, float] = (0.0, 0.0)


# ---------------------------------------------------------------------------
# Polygon construction
# ---------------------------------------------------------------------------

def build_carriageway_polygon(roads: list) -> Any:
    """Union of road centreline buffers → carriageway MultiPolygon.

    *roads* is a list of ``OsmRoad`` (from osm_ingest).
    """
    from shapely.geometry import LineString, MultiPolygon
    from shapely.ops import unary_union

    polygons = []
    for road in roads:
        if len(road.coords) < 2:
            continue
        line = LineString(road.coords)
        half_w = max(road.width_m / 2.0, 0.5)
        poly = line.buffer(half_w, cap_style="flat")
        if not poly.is_empty:
            polygons.append(poly)

    if not polygons:
        return MultiPolygon()

    merged = unary_union(polygons)
    if merged.geom_type == "Polygon":
        return MultiPolygon([merged])
    return merged


def build_sidewalk_zone(
    carriageway: Any,
    sidewalk_width_m: float,
    aoi_polygon: Any,
) -> Any:
    """Sidewalk zone = (carriageway outer buffer) – carriageway, clipped to AOI."""
    from shapely.geometry import MultiPolygon

    if carriageway.is_empty:
        return MultiPolygon()

    outer = carriageway.buffer(sidewalk_width_m)
    sidewalk = outer.difference(carriageway)
    sidewalk = sidewalk.intersection(aoi_polygon)

    if sidewalk.is_empty:
        return MultiPolygon()
    if sidewalk.geom_type == "Polygon":
        return MultiPolygon([sidewalk])
    # Filter out non-polygon geometries from collections
    if sidewalk.geom_type == "GeometryCollection":
        from shapely.geometry import Polygon as ShapelyPolygon
        polys = [g for g in sidewalk.geoms if isinstance(g, ShapelyPolygon)]
        return MultiPolygon(polys) if polys else MultiPolygon()
    return sidewalk


def build_placement_context(
    projected_features: Any,
    config: Any,
) -> PlacementContext:
    """Build the full placement context from projected OSM features and config."""
    from shapely.geometry import box

    sidewalk_width = float(config.sidewalk_width_m)
    bbox_m = projected_features.bbox_m
    aoi_polygon = box(bbox_m[0], bbox_m[1], bbox_m[2], bbox_m[3])

    carriageway = build_carriageway_polygon(projected_features.roads)
    sidewalk_zone = build_sidewalk_zone(carriageway, sidewalk_width, aoi_polygon)

    return PlacementContext(
        sidewalk_zone=sidewalk_zone,
        carriageway=carriageway,
        entrance_points=list(projected_features.entrances),
        bus_stop_points=list(projected_features.bus_stops),
        fire_points=list(projected_features.fire_points),
        aoi_polygon=aoi_polygon,
        origin_offset=projected_features.origin_utm,
    )


# ---------------------------------------------------------------------------
# Sampling helpers
# ---------------------------------------------------------------------------

def sample_slot_on_sidewalk(
    zone: Any,
    rng: random.Random,
    max_attempts: int = 100,
) -> Optional[Tuple[float, float]]:
    """Uniformly sample a point inside *zone* via rejection sampling.

    Returns ``(x, y)`` in local metres or ``None`` if all attempts fail.
    """
    if zone.is_empty:
        return None

    from shapely.geometry import Point as ShapelyPoint
    from shapely.prepared import prep

    prepared = prep(zone)
    minx, miny, maxx, maxy = zone.bounds
    for _ in range(max_attempts):
        x = rng.uniform(minx, maxx)
        y = rng.uniform(miny, maxy)
        if prepared.contains(ShapelyPoint(x, y)):
            return (x, y)
    return None


def compute_facing_angle(
    point: Tuple[float, float],
    carriageway: Any,
) -> float:
    """Compute yaw (degrees) of *point* facing the nearest carriageway edge.

    Returns angle in degrees [0, 360).
    """
    from shapely.geometry import Point as ShapelyPoint

    if carriageway.is_empty:
        return 0.0

    sp = ShapelyPoint(point)
    nearest_pt = carriageway.boundary.interpolate(carriageway.boundary.project(sp))
    dx = nearest_pt.x - point[0]
    dy = nearest_pt.y - point[1]
    angle_rad = math.atan2(dy, dx)
    angle_deg = math.degrees(angle_rad)
    return angle_deg % 360.0


# ---------------------------------------------------------------------------
# GeoJSON export (debug / visualisation)
# ---------------------------------------------------------------------------

def export_zones_geojson(context: PlacementContext, out_path: Path) -> Path:
    """Write placement zones and POI to a GeoJSON file for debugging."""
    from shapely.geometry import mapping, Point as ShapelyPoint

    features: List[Dict[str, Any]] = []

    # Carriageway
    if not context.carriageway.is_empty:
        features.append({
            "type": "Feature",
            "properties": {"layer": "carriageway"},
            "geometry": mapping(context.carriageway),
        })

    # Sidewalk zone
    if not context.sidewalk_zone.is_empty:
        features.append({
            "type": "Feature",
            "properties": {"layer": "sidewalk_zone"},
            "geometry": mapping(context.sidewalk_zone),
        })

    # POI points
    for pt in context.entrance_points:
        features.append({
            "type": "Feature",
            "properties": {"layer": "entrance"},
            "geometry": mapping(ShapelyPoint(pt)),
        })
    for pt in context.bus_stop_points:
        features.append({
            "type": "Feature",
            "properties": {"layer": "bus_stop"},
            "geometry": mapping(ShapelyPoint(pt)),
        })
    for pt in context.fire_points:
        features.append({
            "type": "Feature",
            "properties": {"layer": "fire_point"},
            "geometry": mapping(ShapelyPoint(pt)),
        })

    geojson = {
        "type": "FeatureCollection",
        "features": features,
    }

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(geojson, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Exported placement zones GeoJSON to %s (%d features)", out_path, len(features))
    return out_path
