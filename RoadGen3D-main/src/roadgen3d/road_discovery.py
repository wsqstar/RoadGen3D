"""Discover road segments with sufficient nearby POI for training data quality.

Given a set of cities from the china_cities registry, this module expands each
city's small (~500m) bbox to a larger search area (~2km), fetches OSM data, and
identifies roads that are long enough (default >=100m) and have enough nearby
POI (default >=2) to be useful training samples.

The output is a list of :class:`DiscoveredRoad` records whose ``bbox`` field is
a tight WGS-84 bounding box around each qualifying road.  These can be
serialised to JSONL that is directly compatible with
``m6_01_collect_program_data._load_bboxes()``.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from .poi_taxonomy import (
    core_poi_count,
    normalize_poi_counts,
    normalize_poi_points_by_type,
    poi_breakdown_string,
    poi_weighted_score,
    qualifies_poi_counts,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DiscoveredRoad:
    """A road segment that meets the minimum length and POI requirements."""

    city_name_en: str
    osm_id: int
    highway_type: str
    road_length_m: float
    poi_count: int
    poi_score: float = 0.0
    core_poi_count: int = 0
    poi_types: Dict[str, int] = field(default_factory=dict)
    bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # tight WGS-84 (min_lon, min_lat, max_lon, max_lat)

    def __post_init__(self) -> None:
        if (not self.poi_score) and self.poi_types:
            object.__setattr__(self, "poi_score", float(poi_weighted_score(self.poi_types)))
        if (not self.core_poi_count) and self.poi_types:
            object.__setattr__(self, "core_poi_count", int(core_poi_count(self.poi_types)))

    def to_jsonl_record(self) -> Dict[str, Any]:
        """Return a dict whose ``"bbox"`` key is compatible with ``_load_bboxes()``."""
        return {
            "bbox": list(self.bbox),
            "city": self.city_name_en,
            "osm_id": self.osm_id,
            "highway_type": self.highway_type,
            "road_length_m": round(self.road_length_m, 2),
            "poi_count": self.poi_count,
            "poi_score": round(float(self.poi_score), 4),
            "core_poi_count": int(self.core_poi_count),
            "poi_types": dict(self.poi_types),
            "poi_breakdown": poi_breakdown_string(self.poi_types),
        }


# ---------------------------------------------------------------------------
# Bbox helpers
# ---------------------------------------------------------------------------

def expand_city_bbox(
    city_bbox: Tuple[float, float, float, float],
    margin_deg: float = 0.01,
) -> Tuple[float, float, float, float]:
    """Expand a city bbox to a larger search area centred on the original centre.

    Parameters
    ----------
    city_bbox:
        ``(min_lon, min_lat, max_lon, max_lat)`` in WGS-84.
    margin_deg:
        Half-width of the expanded square in degrees.  0.01 ~ 1.1 km in
        latitude, giving a ~2.2 km x ~1.7-2.2 km search window.

    Returns
    -------
    Expanded ``(min_lon, min_lat, max_lon, max_lat)``.
    """
    centre_lon = (city_bbox[0] + city_bbox[2]) / 2.0
    centre_lat = (city_bbox[1] + city_bbox[3]) / 2.0
    return (
        centre_lon - margin_deg,
        centre_lat - margin_deg,
        centre_lon + margin_deg,
        centre_lat + margin_deg,
    )


def compute_road_bbox(
    road_coords_wgs84: Sequence[Tuple[float, float]],
    padding_m: float = 30.0,
) -> Tuple[float, float, float, float]:
    """Compute a tight WGS-84 bbox around a road with metric padding.

    Parameters
    ----------
    road_coords_wgs84:
        Sequence of ``(lon, lat)`` pairs.
    padding_m:
        Padding in metres added to each side.  30 m covers sidewalk (2.5 m) +
        POI buffer (5 m) + safety margin.
    """
    lons = [c[0] for c in road_coords_wgs84]
    lats = [c[1] for c in road_coords_wgs84]

    min_lon, max_lon = min(lons), max(lons)
    min_lat, max_lat = min(lats), max(lats)

    centre_lat = (min_lat + max_lat) / 2.0
    # 1 degree latitude ~ 111 320 m
    lat_pad = padding_m / 111_320.0
    # 1 degree longitude shrinks with cos(lat)
    cos_lat = math.cos(math.radians(centre_lat))
    lon_pad = padding_m / (111_320.0 * max(cos_lat, 1e-6))

    return (
        min_lon - lon_pad,
        min_lat - lat_pad,
        max_lon + lon_pad,
        max_lat + lat_pad,
    )


# ---------------------------------------------------------------------------
# POI counting
# ---------------------------------------------------------------------------

def _count_pois_in_buffer(
    buffer_zone: Any,  # shapely prepared geometry
    poi_points_or_entrances: Dict[str, Sequence[Tuple[float, float]]] | Sequence[Tuple[float, float]],
    bus_stops: Sequence[Tuple[float, float]] | None = None,
    fire_points: Sequence[Tuple[float, float]] | None = None,
) -> Dict[str, int]:
    """Count POI points that fall inside *buffer_zone* (a prepared Shapely geometry)."""
    from shapely.geometry import Point as ShapelyPoint

    if isinstance(poi_points_or_entrances, dict):
        poi_points_by_type = poi_points_or_entrances
    else:
        poi_points_by_type = {
            "entrance": list(poi_points_or_entrances),
            "bus_stop": list(bus_stops or []),
            "fire_hydrant": list(fire_points or []),
        }
    counts = normalize_poi_counts({})
    for poi_type, points in normalize_poi_points_by_type(poi_points_by_type).items():
        for pt in points:
            if buffer_zone.contains(ShapelyPoint(pt)):
                counts[poi_type] += 1
    return counts


# ---------------------------------------------------------------------------
# Per-city discovery
# ---------------------------------------------------------------------------

def discover_poi_roads(
    city: Any,  # CityRecord from china_cities
    cache_dir: Path,
    *,
    min_road_length_m: float = 100.0,
    min_poi_count: int = 2,
    min_poi_score: float = 2.0,
    min_core_poi_count: int = 1,
    road_buffer_m: float = 15.0,
    bbox_padding_m: float = 30.0,
    expand_margin_deg: float = 0.01,
    force_refetch: bool = False,
) -> List[DiscoveredRoad]:
    """Discover roads with sufficient POI context around a single city.

    1. Expand the city bbox to ~2 km x 2 km.
    2. Fetch + parse + project OSM data.
    3. For each road >= *min_road_length_m*, count POIs within a
       *road_buffer_m* buffer. Roads are kept when they satisfy the weighted
       POI score and core-POI thresholds.
    4. Compute a tight WGS-84 bbox per qualifying road.

    Returns a list of :class:`DiscoveredRoad`.
    """
    from shapely.geometry import LineString, Point as ShapelyPoint
    from shapely.prepared import prep

    from .osm_ingest import fetch_osm_data, parse_osm_features, project_to_local

    expanded_bbox = expand_city_bbox(city.bbox, margin_deg=expand_margin_deg)
    raw = fetch_osm_data(bbox=expanded_bbox, cache_dir=Path(cache_dir), force_refetch=force_refetch)
    features = parse_osm_features(raw)

    if not features.roads:
        logger.info("City %s: no roads in expanded bbox", city.name_en)
        return []

    projected = project_to_local(features, expanded_bbox)

    # Build a mapping from osm_id -> original WGS-84 coords for bbox computation
    wgs84_coords_by_id: Dict[int, List[Tuple[float, float]]] = {
        road.osm_id: road.coords for road in features.roads
    }

    results: List[DiscoveredRoad] = []

    for proj_road in projected.roads:
        if len(proj_road.coords) < 2:
            continue

        line = LineString(proj_road.coords)
        road_length = line.length

        if road_length < min_road_length_m:
            continue

        buffer_zone = prep(line.buffer(road_buffer_m))
        poi_types = _count_pois_in_buffer(
            buffer_zone,
            getattr(projected, "poi_points_by_type", {}),
        )
        total_pois = int(sum(poi_types.values()))
        weighted_score = poi_weighted_score(poi_types)
        core_count = core_poi_count(poi_types)

        if total_pois < int(min_poi_count):
            continue
        if not qualifies_poi_counts(
            poi_types,
            min_score=min_poi_score,
            min_core_count=min_core_poi_count,
        ):
            continue

        # Use original WGS-84 coords for the tight bbox
        wgs84_coords = wgs84_coords_by_id.get(proj_road.osm_id)
        if not wgs84_coords:
            continue

        tight_bbox = compute_road_bbox(wgs84_coords, padding_m=bbox_padding_m)

        results.append(DiscoveredRoad(
            city_name_en=city.name_en,
            osm_id=proj_road.osm_id,
            highway_type=proj_road.highway_type,
            road_length_m=round(road_length, 2),
            poi_count=total_pois,
            poi_score=weighted_score,
            core_poi_count=core_count,
            poi_types=poi_types,
            bbox=tight_bbox,
        ))

    logger.info(
        "City %s: %d roads total, %d qualifying (>= %.0fm, score >= %.1f, core >= %d)",
        city.name_en, len(projected.roads), len(results),
        min_road_length_m, min_poi_score, min_core_poi_count,
    )
    return results


# ---------------------------------------------------------------------------
# Batch discovery
# ---------------------------------------------------------------------------

def discover_all_cities(
    cities: Sequence[Any],  # Sequence[CityRecord]
    cache_dir: Path,
    *,
    min_road_length_m: float = 100.0,
    min_poi_count: int = 2,
    min_poi_score: float = 2.0,
    min_core_poi_count: int = 1,
    road_buffer_m: float = 15.0,
    bbox_padding_m: float = 30.0,
    expand_margin_deg: float = 0.01,
    force_refetch: bool = False,
) -> List[DiscoveredRoad]:
    """Run :func:`discover_poi_roads` for every city and deduplicate by ``osm_id``.

    When the same road appears from two cities (rare but possible if expanded
    bboxes overlap), the record with the higher ``poi_count`` is kept.
    """
    seen: Dict[int, DiscoveredRoad] = {}  # osm_id -> best record

    for city in cities:
        roads = discover_poi_roads(
            city, cache_dir,
            min_road_length_m=min_road_length_m,
            min_poi_count=min_poi_count,
            min_poi_score=min_poi_score,
            min_core_poi_count=min_core_poi_count,
            road_buffer_m=road_buffer_m,
            bbox_padding_m=bbox_padding_m,
            expand_margin_deg=expand_margin_deg,
            force_refetch=force_refetch,
        )
        for road in roads:
            existing = seen.get(road.osm_id)
            if existing is None or road.poi_score > existing.poi_score or (
                math.isclose(float(road.poi_score), float(existing.poi_score))
                and road.poi_count > existing.poi_count
            ):
                seen[road.osm_id] = road

    result = list(seen.values())
    logger.info(
        "Discovered %d unique qualifying roads from %d cities",
        len(result), len(cities),
    )
    return result


# ---------------------------------------------------------------------------
# JSONL I/O
# ---------------------------------------------------------------------------

def write_discovered_roads_jsonl(
    roads: Sequence[DiscoveredRoad],
    out_path: Path,
) -> Path:
    """Write discovered roads to a JSONL file compatible with ``_load_bboxes()``."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for road in roads:
            fh.write(json.dumps(road.to_jsonl_record(), ensure_ascii=False) + "\n")
    logger.info("Wrote %d discovered roads to %s", len(roads), out_path)
    return out_path
