"""Placement zone construction from OSM road geometry for M5."""

from __future__ import annotations

import json
import logging
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .cross_section_synthesis import synthesize_poi_driven_cross_section
from .poi_taxonomy import (
    CANONICAL_FIRE_POI,
    core_poi_count,
    extract_poi_points_by_type,
    nonempty_poi_points,
    normalize_poi_counts,
    normalize_poi_points_by_type,
    poi_breakdown_string,
    poi_weighted_score,
)

logger = logging.getLogger(__name__)
EFFECTIVE_POI_EVALUATOR_VERSION = "v2"


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
    left_sidewalk_zone: Any = None  # shapely (Multi)Polygon – left-side pedestrian corridor
    right_sidewalk_zone: Any = None  # shapely (Multi)Polygon – right-side pedestrian corridor
    entrance_points: List[Tuple[float, float]] = field(default_factory=list)  # (x, y) local metres
    bus_stop_points: List[Tuple[float, float]] = field(default_factory=list)
    fire_points: List[Tuple[float, float]] = field(default_factory=list)
    poi_points_by_type: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)
    aoi_polygon: Any = None  # shapely Polygon – bounding box polygon
    origin_offset: Tuple[float, float] = (0.0, 0.0)
    carriageway_polygon: Any = None
    road_reference: Any = None
    carriageway_width_m: float = 0.0
    left_clear_path_width_m: float = 0.0
    right_clear_path_width_m: float = 0.0
    left_furnishing_width_m: float = 0.0
    right_furnishing_width_m: float = 0.0
    row_width_m: float = 0.0
    width_expanded: bool = False
    width_reallocation_reason: str = ""
    poi_fit_feasible: bool = True
    poi_fit_report: Dict[str, Any] = field(default_factory=dict)
    required_left_width_m: float = 0.0
    required_right_width_m: float = 0.0


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


def build_carriageway_polygon_with_width(
    roads: list,
    carriageway_width_m: float,
) -> Any:
    """Union of road centreline buffers using an explicit carriageway width."""

    from shapely.geometry import LineString, MultiPolygon
    from shapely.ops import unary_union

    polygons = []
    half_w = max(float(carriageway_width_m) / 2.0, 0.5)
    for road in roads:
        if len(road.coords) < 2:
            continue
        line = LineString(road.coords)
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


def build_sidewalk_zones_from_roads(
    roads: list,
    *,
    carriageway_width_m: float,
    left_sidewalk_width_m: float,
    right_sidewalk_width_m: float,
    aoi_polygon: Any,
) -> Tuple[Any, Any, Any]:
    """Build asymmetric left/right sidewalk corridors from road centerlines."""

    from shapely.geometry import LineString, MultiPolygon
    from shapely.ops import unary_union

    left_polygons = []
    right_polygons = []
    carriageway_half = max(float(carriageway_width_m) / 2.0, 0.5)
    left_total = max(float(left_sidewalk_width_m), 0.0)
    right_total = max(float(right_sidewalk_width_m), 0.0)

    for road in roads:
        if len(road.coords) < 2:
            continue
        line = LineString(road.coords)
        if left_total > 0.0:
            outer_left = line.buffer(carriageway_half + left_total, cap_style="flat", single_sided=True)
            inner_left = line.buffer(carriageway_half, cap_style="flat", single_sided=True)
            left_zone = outer_left.difference(inner_left)
            if not left_zone.is_empty:
                left_polygons.append(left_zone)
        if right_total > 0.0:
            outer_right = line.buffer(-(carriageway_half + right_total), cap_style="flat", single_sided=True)
            inner_right = line.buffer(-carriageway_half, cap_style="flat", single_sided=True)
            right_zone = outer_right.difference(inner_right)
            if not right_zone.is_empty:
                right_polygons.append(right_zone)

    def _merge(polygons: List[Any]) -> Any:
        if not polygons:
            return MultiPolygon()
        merged = unary_union(polygons)
        return _clip_to_aoi(merged, aoi_polygon)

    left_sidewalk = _merge(left_polygons)
    right_sidewalk = _merge(right_polygons)
    union_sidewalk = left_sidewalk.union(right_sidewalk)
    union_sidewalk = _clip_to_aoi(union_sidewalk, aoi_polygon)
    return left_sidewalk, right_sidewalk, union_sidewalk


def _clip_to_aoi(geometry: Any, aoi_polygon: Any) -> Any:
    """Clip a geometry to the AOI polygon, returning a MultiPolygon."""
    from shapely.geometry import MultiPolygon, Polygon as ShapelyPolygon

    if geometry.is_empty:
        return MultiPolygon()
    clipped = geometry.intersection(aoi_polygon)
    if clipped.is_empty:
        return MultiPolygon()
    if clipped.geom_type == "Polygon":
        return MultiPolygon([clipped])
    if clipped.geom_type == "GeometryCollection":
        polys = [g for g in clipped.geoms if isinstance(g, ShapelyPolygon)]
        return MultiPolygon(polys) if polys else MultiPolygon()
    return clipped


# ---------------------------------------------------------------------------
# Road selection
# ---------------------------------------------------------------------------

_HIERARCHY_RANK: Dict[str, int] = {
    "primary": 0,
    "secondary": 1,
    "tertiary": 2,
    "residential": 3,
    "unclassified": 4,
    "service": 5,
    "living_street": 6,
}


def select_primary_road(
    roads: list,
    bbox_m: Tuple[float, float, float, float],
    strategy: str = "primary_road",
    selected_osm_id: int | None = None,
) -> list:
    """Select a single road from *roads* based on *strategy*.

    Strategies
    ----------
    ``"all"``          – return all roads unchanged.
    ``"primary_road"`` – pick the highest-hierarchy road closest to AOI centre.
    ``"longest"``      – pick the longest road regardless of hierarchy.
    ``selected_osm_id`` – if provided, prefer the exact road match.
    """
    if not roads or strategy == "all":
        return list(roads)

    from shapely.geometry import LineString, Point as ShapelyPoint

    center = ((bbox_m[0] + bbox_m[2]) / 2.0, (bbox_m[1] + bbox_m[3]) / 2.0)
    center_pt = ShapelyPoint(center)

    def _road_length(road) -> float:
        return LineString(road.coords).length if len(road.coords) >= 2 else 0.0

    if selected_osm_id is not None:
        for road in roads:
            if int(getattr(road, "osm_id", -1)) == int(selected_osm_id):
                logger.info(
                    "Road selection (selected_osm_id): %d roads -> osm_id=%d (%s, %.0fm)",
                    len(roads), road.osm_id, road.highway_type, _road_length(road),
                )
                return [road]
        logger.warning(
            "Selected road osm_id=%s not found in %d roads; falling back to strategy=%s",
            selected_osm_id, len(roads), strategy,
        )

    if strategy == "longest":
        best = max(roads, key=_road_length)
        logger.info(
            "Road selection (longest): %d roads -> osm_id=%d (%s, %.0fm)",
            len(roads), best.osm_id, best.highway_type, _road_length(best),
        )
        return [best]

    # strategy == "primary_road"
    def _sort_key(road):
        rank = _HIERARCHY_RANK.get(road.highway_type, 99)
        dist = (
            LineString(road.coords).distance(center_pt)
            if len(road.coords) >= 2
            else 9999.0
        )
        length = _road_length(road)
        return (rank, dist, -length)

    sorted_roads = sorted(roads, key=_sort_key)
    best = sorted_roads[0]
    logger.info(
        "Road selection (primary_road): %d roads -> osm_id=%d (%s, %.0fm)",
        len(roads), best.osm_id, best.highway_type, _road_length(best),
    )
    return [best]


def apply_road_selection(projected_features: Any, config: Any) -> Any:
    """Return a copy of *projected_features* with roads filtered by config.road_selection."""
    from .osm_ingest import ProjectedFeatures

    strategy = str(getattr(config, "road_selection", "primary_road"))
    selected_osm_id = getattr(config, "selected_road_osm_id", None)
    if strategy == "all" and selected_osm_id is None:
        return projected_features

    filtered = select_primary_road(
        projected_features.roads,
        projected_features.bbox_m,
        strategy,
        selected_osm_id=selected_osm_id,
    )
    return ProjectedFeatures(
        roads=filtered,
        buildings=projected_features.buildings,
        entrances=projected_features.entrances,
        bus_stops=projected_features.bus_stops,
        fire_points=projected_features.fire_points,
        poi_points_by_type=extract_poi_points_by_type(projected_features),
        bbox_m=projected_features.bbox_m,
        origin_utm=projected_features.origin_utm,
        utm_epsg=projected_features.utm_epsg,
    )


def count_placement_context_pois(context: PlacementContext) -> Dict[str, int]:
    return normalize_poi_counts({
        poi_type: len(points)
        for poi_type, points in extract_poi_points_by_type(context).items()
    })


def total_poi_count(counts: Dict[str, int]) -> int:
    return int(sum(int(value) for value in counts.values()))


def poi_score(counts: Dict[str, int]) -> float:
    return poi_weighted_score(counts)


def poi_core_count(counts: Dict[str, int]) -> int:
    return core_poi_count(counts)


def evaluate_projected_road_context(
    projected_features: Any,
    config: Any,
) -> Tuple[Any, PlacementContext, Dict[str, int]]:
    filtered_projected = apply_road_selection(projected_features, config)
    placement_ctx = build_placement_context(filtered_projected, config)
    return filtered_projected, placement_ctx, count_placement_context_pois(placement_ctx)


def build_placement_context(
    projected_features: Any,
    config: Any,
) -> PlacementContext:
    """Build the full placement context from projected OSM features and config."""
    from shapely.geometry import box
    from shapely.prepared import prep

    from .street_program import profile_defaults

    bbox_m = projected_features.bbox_m
    aoi_polygon = box(bbox_m[0], bbox_m[1], bbox_m[2], bbox_m[3])
    poi_points_by_type = extract_poi_points_by_type(projected_features)
    defaults = profile_defaults(str(getattr(config, "design_rule_profile", "balanced_complete_street_v1")))
    min_clear_path_width_m = float(defaults["min_clear_path_width_m"])
    min_furnishing_width_m = float(defaults["furnishing_width_m"])
    right_edge_width_m = float(defaults.get("right_edge_width_m", min_furnishing_width_m))

    cross_section = synthesize_poi_driven_cross_section(
        roads=projected_features.roads,
        poi_points_by_type=poi_points_by_type,
        road_width_m=float(config.road_width_m),
        lane_count=int(getattr(config, "lane_count", 1)),
        sidewalk_seed_width_m=float(config.sidewalk_width_m),
        base_lane_width_m=getattr(config, "base_lane_width_m", None),
        min_clear_path_width_m=min_clear_path_width_m,
        left_furnishing_min_width_m=min_furnishing_width_m,
        right_furnishing_min_width_m=right_edge_width_m,
    )

    carriageway_raw = build_carriageway_polygon_with_width(
        projected_features.roads,
        cross_section.carriageway_width_m,
    )
    # Clip carriageway to AOI so roads don't extend far beyond the scene
    carriageway = _clip_to_aoi(carriageway_raw, aoi_polygon)
    left_sidewalk_zone, right_sidewalk_zone, sidewalk_zone = build_sidewalk_zones_from_roads(
        projected_features.roads,
        carriageway_width_m=cross_section.carriageway_width_m,
        left_sidewalk_width_m=cross_section.left_sidewalk_width_m,
        right_sidewalk_width_m=cross_section.right_sidewalk_width_m,
        aoi_polygon=aoi_polygon,
    )

    # Filter POIs to those retained by the synthesized road corridor.
    if not carriageway.is_empty or not sidewalk_zone.is_empty:
        from shapely.geometry import Point as ShapelyPoint

        corridor = carriageway.union(sidewalk_zone)
        relevance_zone = prep(corridor.buffer(0.25))
        filtered_poi_points_by_type = {
            poi_type: [pt for pt in points if relevance_zone.contains(ShapelyPoint(pt))]
            for poi_type, points in cross_section.candidate_poi_points_by_type.items()
        }
    else:
        filtered_poi_points_by_type = {
            poi_type: list(points)
            for poi_type, points in cross_section.candidate_poi_points_by_type.items()
        }

    filtered_entrances = list(filtered_poi_points_by_type.get("entrance", []))
    filtered_bus_stops = list(filtered_poi_points_by_type.get("bus_stop", []))
    filtered_fire_points = list(filtered_poi_points_by_type.get(CANONICAL_FIRE_POI, []))

    logger.info(
        "POI filtering: %s -> %s",
        poi_breakdown_string({
            poi_type: len(points)
            for poi_type, points in cross_section.candidate_poi_points_by_type.items()
        }),
        poi_breakdown_string({
            poi_type: len(points)
            for poi_type, points in filtered_poi_points_by_type.items()
        }),
    )

    return PlacementContext(
        sidewalk_zone=sidewalk_zone,
        carriageway=carriageway,
        left_sidewalk_zone=left_sidewalk_zone,
        right_sidewalk_zone=right_sidewalk_zone,
        entrance_points=filtered_entrances,
        bus_stop_points=filtered_bus_stops,
        fire_points=filtered_fire_points,
        poi_points_by_type=filtered_poi_points_by_type,
        aoi_polygon=aoi_polygon,
        origin_offset=projected_features.origin_utm,
        carriageway_polygon=carriageway,
        road_reference=projected_features.roads[0] if projected_features.roads else None,
        carriageway_width_m=float(cross_section.carriageway_width_m),
        left_clear_path_width_m=float(cross_section.left_clear_path_width_m),
        right_clear_path_width_m=float(cross_section.right_clear_path_width_m),
        left_furnishing_width_m=float(cross_section.left_furnishing_width_m),
        right_furnishing_width_m=float(cross_section.right_furnishing_width_m),
        row_width_m=float(cross_section.row_width_m),
        width_expanded=bool(cross_section.width_expanded),
        width_reallocation_reason=str(cross_section.width_reallocation_reason),
        poi_fit_feasible=bool(cross_section.poi_fit_feasible),
        poi_fit_report=dict(cross_section.poi_fit_report),
        required_left_width_m=float(cross_section.required_left_width_m),
        required_right_width_m=float(cross_section.required_right_width_m),
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
    for poi_type, points in nonempty_poi_points(getattr(context, "poi_points_by_type", {})).items():
        for pt in points:
            features.append({
                "type": "Feature",
                "properties": {"layer": poi_type},
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
