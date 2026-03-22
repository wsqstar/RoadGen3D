"""OSM data ingestion: fetch, parse, cache, and project for M5."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .poi_taxonomy import (
    CANONICAL_FIRE_POI,
    detect_poi_types_from_tags,
    extract_poi_points_by_type,
    normalize_poi_points_by_type,
    overpass_poi_clauses,
)

logger = logging.getLogger(__name__)

# Default road widths (metres) by highway type when OSM `width` tag is absent.
_DEFAULT_WIDTH_M: Dict[str, float] = {
    "primary": 12.0,
    "secondary": 9.0,
    "tertiary": 7.0,
    "residential": 6.0,
    "service": 4.0,
    "living_street": 4.0,
    "unclassified": 6.0,
}

# Highway types we query from Overpass.
_HIGHWAY_FILTER = "primary|secondary|tertiary|residential|service|living_street|unclassified"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class OsmRoad:
    """One parsed OSM road (way)."""

    osm_id: int
    highway_type: str
    coords: List[Tuple[float, float]]  # [(lon, lat), ...]
    width_m: float  # estimated or from tag


@dataclass
class OsmBuilding:
    """One parsed OSM building footprint."""

    osm_id: int
    coords: List[Tuple[float, float]]
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass
class OsmFeatures:
    """All parsed features from an Overpass response (WGS-84)."""

    roads: List[OsmRoad] = field(default_factory=list)
    buildings: List[OsmBuilding] = field(default_factory=list)
    entrances: List[Tuple[float, float]] = field(default_factory=list)  # (lon, lat)
    bus_stops: List[Tuple[float, float]] = field(default_factory=list)
    fire_points: List[Tuple[float, float]] = field(default_factory=list)
    poi_points_by_type: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)
    bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


@dataclass
class ProjectedFeatures:
    """Same as OsmFeatures but in local UTM metres, origin at bbox centre."""

    roads: List[OsmRoad] = field(default_factory=list)
    buildings: List[OsmBuilding] = field(default_factory=list)
    entrances: List[Tuple[float, float]] = field(default_factory=list)  # (x, y) metres
    bus_stops: List[Tuple[float, float]] = field(default_factory=list)
    fire_points: List[Tuple[float, float]] = field(default_factory=list)
    poi_points_by_type: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)
    bbox_m: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # in local metres
    origin_utm: Tuple[float, float] = (0.0, 0.0)  # UTM easting/northing of bbox centre
    utm_epsg: int = 0


# ---------------------------------------------------------------------------
# UTM helpers
# ---------------------------------------------------------------------------

def auto_detect_utm_epsg(lon: float, lat: float) -> int:
    """Return the EPSG code of the UTM zone that contains *lon*, *lat*."""
    zone = int(math.floor((lon + 180.0) / 6.0)) + 1
    return 32600 + zone if lat >= 0 else 32700 + zone


# ---------------------------------------------------------------------------
# Overpass fetch + cache
# ---------------------------------------------------------------------------

def _bbox_hash(bbox: Tuple[float, float, float, float]) -> str:
    key = f"{bbox[0]:.6f},{bbox[1]:.6f},{bbox[2]:.6f},{bbox[3]:.6f}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _build_overpass_query(bbox: Tuple[float, float, float, float]) -> str:
    """Build an Overpass QL query for roads and POI within *bbox*."""
    south, west, north, east = bbox[1], bbox[0], bbox[3], bbox[2]
    bb = f"{south},{west},{north},{east}"
    poi_clauses = "\n".join(f"  {clause}" for clause in overpass_poi_clauses(bb))
    return (
        "[out:json][timeout:60];\n"
        "(\n"
        f'  way["highway"~"{_HIGHWAY_FILTER}"]({bb});\n'
        f'  way["building"]({bb});\n'
        f"{poi_clauses}\n"
        ");\n"
        "out body;\n"
        ">;\n"
        "out skel qt;\n"
    )


def fetch_osm_data(
    bbox: Tuple[float, float, float, float],
    cache_dir: Path,
    force_refetch: bool = False,
) -> Dict[str, Any]:
    """Fetch OSM data via Overpass API.  Returns the raw JSON dict.

    Results are cached under *cache_dir* so repeated calls for the same bbox
    skip the network round-trip.
    """
    import requests  # lazily imported – only needed when actually fetching

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"overpass_{_bbox_hash(bbox)}.json"

    if cache_path.exists() and not force_refetch:
        logger.info("OSM cache hit: %s", cache_path)
        return json.loads(cache_path.read_text(encoding="utf-8"))

    query = _build_overpass_query(bbox)
    logger.info("Fetching OSM data from Overpass for bbox=%s ...", bbox)
    url = "https://overpass-api.de/api/interpreter"

    # Simple retry with exponential back-off.
    import time

    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            resp = requests.post(url, data={"data": query}, timeout=90)
            resp.raise_for_status()
            data = resp.json()
            cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            logger.info("OSM data cached to %s (%d elements)", cache_path, len(data.get("elements", [])))
            return data
        except Exception as exc:
            last_exc = exc
            wait = 2 ** attempt
            logger.warning("Overpass request failed (attempt %d): %s – retrying in %ds", attempt + 1, exc, wait)
            time.sleep(wait)
    raise RuntimeError(f"Overpass API failed after 3 attempts: {last_exc}") from last_exc


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_osm_features(raw_data: Dict[str, Any]) -> OsmFeatures:
    """Extract roads and POI from an Overpass JSON response."""
    elements = raw_data.get("elements", [])

    # Build a node-id -> (lon, lat) lookup from all elements with type "node"
    node_coords: Dict[int, Tuple[float, float]] = {}
    for el in elements:
        if el.get("type") == "node" and "lon" in el and "lat" in el:
            node_coords[int(el["id"])] = (float(el["lon"]), float(el["lat"]))

    roads: List[OsmRoad] = []
    buildings: List[OsmBuilding] = []
    poi_points_by_type: Dict[str, List[Tuple[float, float]]] = normalize_poi_points_by_type({})

    for el in elements:
        etype = el.get("type", "")
        tags = el.get("tags", {})

        if etype == "way" and "highway" in tags:
            hw_type = tags["highway"]
            if hw_type not in _DEFAULT_WIDTH_M:
                continue
            # Resolve node refs to coordinates
            nds = el.get("nodes", [])
            coords = [node_coords[nid] for nid in nds if nid in node_coords]
            if len(coords) < 2:
                continue
            # Width: prefer tag, else default
            width_tag = tags.get("width")
            if width_tag is not None:
                try:
                    width_m = float(str(width_tag).replace("m", "").strip())
                except (ValueError, TypeError):
                    width_m = _DEFAULT_WIDTH_M[hw_type]
            else:
                width_m = _DEFAULT_WIDTH_M[hw_type]
            roads.append(OsmRoad(osm_id=int(el["id"]), highway_type=hw_type, coords=coords, width_m=width_m))
        elif etype == "way" and "building" in tags:
            nds = el.get("nodes", [])
            coords = [node_coords[nid] for nid in nds if nid in node_coords]
            if len(coords) < 3:
                continue
            if coords[0] != coords[-1]:
                coords = coords + [coords[0]]
            buildings.append(
                OsmBuilding(
                    osm_id=int(el["id"]),
                    coords=coords,
                    tags={str(key): str(value) for key, value in tags.items()},
                )
            )

        elif etype == "node":
            lon_lat: Optional[Tuple[float, float]] = None
            if "lon" in el and "lat" in el:
                lon_lat = (float(el["lon"]), float(el["lat"]))
            else:
                lon_lat = node_coords.get(int(el["id"]))
            if lon_lat is None:
                continue

            for poi_type in detect_poi_types_from_tags(tags):
                poi_points_by_type.setdefault(poi_type, []).append(lon_lat)

    entrances = list(poi_points_by_type.get("entrance", []))
    bus_stops = list(poi_points_by_type.get("bus_stop", []))
    fire_points = list(poi_points_by_type.get(CANONICAL_FIRE_POI, []))

    logger.info(
        "Parsed OSM: %d roads, poi=%s",
        len(roads),
        {
            poi_type: len(points)
            for poi_type, points in poi_points_by_type.items()
            if points
        },
    )
    return OsmFeatures(
        roads=roads,
        buildings=buildings,
        entrances=entrances,
        bus_stops=bus_stops,
        fire_points=fire_points,
        poi_points_by_type=poi_points_by_type,
    )


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------

def project_to_local(
    features: OsmFeatures,
    bbox: Tuple[float, float, float, float],
) -> ProjectedFeatures:
    """Project WGS-84 features into a local UTM coordinate system centred on the bbox."""
    from pyproj import Transformer

    centre_lon = (bbox[0] + bbox[2]) / 2.0
    centre_lat = (bbox[1] + bbox[3]) / 2.0
    utm_epsg = auto_detect_utm_epsg(centre_lon, centre_lat)

    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{utm_epsg}", always_xy=True)

    def _proj(lon: float, lat: float) -> Tuple[float, float]:
        return transformer.transform(lon, lat)  # type: ignore[return-value]

    origin_e, origin_n = _proj(centre_lon, centre_lat)

    def _to_local(lon: float, lat: float) -> Tuple[float, float]:
        e, n = _proj(lon, lat)
        return (e - origin_e, n - origin_n)

    # Project roads
    proj_roads: List[OsmRoad] = []
    for road in features.roads:
        proj_coords = [_to_local(lon, lat) for lon, lat in road.coords]
        proj_roads.append(OsmRoad(
            osm_id=road.osm_id,
            highway_type=road.highway_type,
            coords=proj_coords,
            width_m=road.width_m,
        ))
    proj_buildings: List[OsmBuilding] = []
    for building in features.buildings:
        proj_buildings.append(
            OsmBuilding(
                osm_id=building.osm_id,
                coords=[_to_local(lon, lat) for lon, lat in building.coords],
                tags=dict(building.tags),
            )
        )

    proj_poi_points_by_type = {
        poi_type: [_to_local(lon, lat) for lon, lat in points]
        for poi_type, points in extract_poi_points_by_type(features).items()
    }
    proj_entrances = list(proj_poi_points_by_type.get("entrance", []))
    proj_bus_stops = list(proj_poi_points_by_type.get("bus_stop", []))
    proj_fire_points = list(proj_poi_points_by_type.get(CANONICAL_FIRE_POI, []))

    # Project bbox corners
    bl = _to_local(bbox[0], bbox[1])
    tr = _to_local(bbox[2], bbox[3])
    bbox_m = (min(bl[0], tr[0]), min(bl[1], tr[1]), max(bl[0], tr[0]), max(bl[1], tr[1]))

    return ProjectedFeatures(
        roads=proj_roads,
        buildings=proj_buildings,
        entrances=proj_entrances,
        bus_stops=proj_bus_stops,
        fire_points=proj_fire_points,
        poi_points_by_type=proj_poi_points_by_type,
        bbox_m=bbox_m,
        origin_utm=(origin_e, origin_n),
        utm_epsg=utm_epsg,
    )
