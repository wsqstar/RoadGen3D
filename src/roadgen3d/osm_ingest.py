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
_SEMANTIC_POLYGON_KEYS = ("landuse", "amenity", "leisure", "shop", "tourism", "office")
_SEMANTIC_QUERY_KEYS = (*_SEMANTIC_POLYGON_KEYS, "building")
_EDUCATION_AMENITIES = {"kindergarten", "school", "childcare", "college", "university"}
_COMMERCIAL_AMENITIES = {
    "bar",
    "bank",
    "cafe",
    "fast_food",
    "food_court",
    "marketplace",
    "pharmacy",
    "pub",
    "restaurant",
}
_VEHICLE_AMENITIES = {"car_rental", "car_sharing", "car_wash", "fuel", "parking", "parking_entrance"}
_GREEN_LEISURE = {"garden", "park", "pitch", "playground", "recreation_ground"}
_GREEN_LANDUSES = {"forest", "grass", "greenfield", "meadow", "recreation_ground", "village_green"}
_COMMERCIAL_LANDUSES = {"commercial", "retail"}
_RESIDENTIAL_LANDUSES = {"residential"}


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
class OsmLandUsePolygon:
    """One OSM polygon carrying land-use or amenity semantics."""

    osm_id: int
    source_type: str
    coords: List[Tuple[float, float]]
    tags: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "osm_id": int(self.osm_id),
            "source_type": self.source_type,
            "coords": [list(point) for point in self.coords],
            "tags": dict(self.tags),
        }


@dataclass
class OsmSemanticBlock:
    """One classified semantic block used by OSM multiblock generation."""

    block_id: str
    osm_id: int
    source_type: str
    coords: List[Tuple[float, float]]
    centroid: Tuple[float, float]
    tags: Dict[str, str] = field(default_factory=dict)
    semantic_profile_id: str = ""
    semantic_reasons: List[str] = field(default_factory=list)
    confidence: float = 0.0
    poi_counts: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "block_id": self.block_id,
            "osm_id": int(self.osm_id),
            "source_type": self.source_type,
            "coords": [list(point) for point in self.coords],
            "centroid": list(self.centroid),
            "tags": dict(self.tags),
            "semantic_profile_id": self.semantic_profile_id,
            "semantic_reasons": list(self.semantic_reasons),
            "confidence": float(self.confidence),
            "poi_counts": dict(self.poi_counts),
        }


@dataclass
class OsmFeatures:
    """All parsed features from an Overpass response (WGS-84)."""

    roads: List[OsmRoad] = field(default_factory=list)
    buildings: List[OsmBuilding] = field(default_factory=list)
    land_use_polygons: List[OsmLandUsePolygon] = field(default_factory=list)
    semantic_blocks: List[OsmSemanticBlock] = field(default_factory=list)
    entrances: List[Tuple[float, float]] = field(default_factory=list)  # (lon, lat)
    bus_stops: List[Tuple[float, float]] = field(default_factory=list)
    fire_points: List[Tuple[float, float]] = field(default_factory=list)
    poi_points_by_type: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)
    semantic_points_by_type: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)
    bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


@dataclass
class ProjectedFeatures:
    """Same as OsmFeatures but in local UTM metres, origin at bbox centre."""

    roads: List[OsmRoad] = field(default_factory=list)
    buildings: List[OsmBuilding] = field(default_factory=list)
    land_use_polygons: List[OsmLandUsePolygon] = field(default_factory=list)
    semantic_blocks: List[OsmSemanticBlock] = field(default_factory=list)
    entrances: List[Tuple[float, float]] = field(default_factory=list)  # (x, y) metres
    bus_stops: List[Tuple[float, float]] = field(default_factory=list)
    fire_points: List[Tuple[float, float]] = field(default_factory=list)
    poi_points_by_type: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)
    semantic_points_by_type: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)
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
    semantic_clauses = "\n".join(
        f'  node["{key}"]({bb});\n'
        f'  way["{key}"]({bb});\n'
        f'  relation["{key}"]({bb});'
        for key in _SEMANTIC_QUERY_KEYS
    )
    return (
        "[out:json][timeout:60];\n"
        "(\n"
        f'  way["highway"~"{_HIGHWAY_FILTER}"]({bb});\n'
        f'  way["building"]({bb});\n'
        f"{poi_clauses}\n"
        f"{semantic_clauses}\n"
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
            resp = requests.post(
                url,
                data={"data": query},
                headers={
                    "Accept": "application/json",
                    "User-Agent": "RoadGen3D OSM semantic preview",
                },
                timeout=90,
            )
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

def _string_tags(tags: Dict[str, Any]) -> Dict[str, str]:
    return {str(key): str(value) for key, value in dict(tags or {}).items()}


def _closed_polygon_coords(coords: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    if len(coords) < 3:
        return []
    if coords[0] != coords[-1]:
        coords = coords + [coords[0]]
    return coords


def _coords_for_way(el: Dict[str, Any], node_coords: Dict[int, Tuple[float, float]], *, close: bool = False) -> List[Tuple[float, float]]:
    coords = [node_coords[nid] for nid in el.get("nodes", []) if nid in node_coords]
    return _closed_polygon_coords(coords) if close else coords


def _is_semantic_polygon(tags: Dict[str, str]) -> bool:
    if any(key in tags for key in ("landuse", "leisure", "shop", "tourism", "office")):
        return True
    amenity = str(tags.get("amenity", "")).strip().lower()
    if amenity in _EDUCATION_AMENITIES | _COMMERCIAL_AMENITIES | _VEHICLE_AMENITIES:
        return True
    return bool("building" in tags and amenity)


def _semantic_point_types_from_tags(tags: Dict[str, Any]) -> List[str]:
    normalized = _string_tags(tags)
    result: List[str] = []
    amenity = normalized.get("amenity", "").strip().lower()
    landuse = normalized.get("landuse", "").strip().lower()
    leisure = normalized.get("leisure", "").strip().lower()
    if amenity in _EDUCATION_AMENITIES:
        result.append("education")
    if normalized.get("shop") or normalized.get("office") or normalized.get("tourism") or amenity in _COMMERCIAL_AMENITIES or landuse in _COMMERCIAL_LANDUSES:
        result.append("commercial")
    if amenity in _VEHICLE_AMENITIES:
        result.append("vehicle_access")
    if (
        normalized.get("highway") == "bus_stop"
        or normalized.get("public_transport") == "platform"
        or normalized.get("railway") in {"station", "subway_entrance"}
    ):
        result.append("transit")
    if landuse in _GREEN_LANDUSES or leisure in _GREEN_LEISURE:
        result.append("green")
    if landuse in _RESIDENTIAL_LANDUSES:
        result.append("residential")
    return sorted(set(result))


def _centroid(coords: List[Tuple[float, float]]) -> Tuple[float, float]:
    if not coords:
        return (0.0, 0.0)
    ring = coords[:-1] if len(coords) > 1 and coords[0] == coords[-1] else coords
    return (
        sum(float(point[0]) for point in ring) / float(len(ring)),
        sum(float(point[1]) for point in ring) / float(len(ring)),
    )


def _join_relation_outer_coords(
    el: Dict[str, Any],
    way_coords_by_id: Dict[int, List[Tuple[float, float]]],
) -> List[Tuple[float, float]]:
    pieces: List[List[Tuple[float, float]]] = []
    for member in el.get("members", []) or []:
        if member.get("type") != "way":
            continue
        role = str(member.get("role", "") or "").strip().lower()
        if role not in {"", "outer"}:
            continue
        coords = way_coords_by_id.get(int(member.get("ref", 0)))
        if coords and len(coords) >= 2:
            pieces.append(list(coords))
    if not pieces:
        return []
    coords = list(pieces[0])
    for piece in pieces[1:]:
        if coords[-1] == piece[0]:
            coords.extend(piece[1:])
        elif coords[-1] == piece[-1]:
            coords.extend(reversed(piece[:-1]))
        elif coords[0] == piece[-1]:
            coords = piece[:-1] + coords
        elif coords[0] == piece[0]:
            coords = list(reversed(piece[1:])) + coords
        else:
            coords.extend(piece)
    return _closed_polygon_coords(coords)


def _semantic_block_from_polygon(index: int, polygon: OsmLandUsePolygon) -> OsmSemanticBlock:
    return OsmSemanticBlock(
        block_id=f"osm_block_{index:03d}",
        osm_id=int(polygon.osm_id),
        source_type=polygon.source_type,
        coords=list(polygon.coords),
        centroid=_centroid(list(polygon.coords)),
        tags=dict(polygon.tags),
    )


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
    land_use_polygons: List[OsmLandUsePolygon] = []
    semantic_points_by_type: Dict[str, List[Tuple[float, float]]] = {}
    poi_points_by_type: Dict[str, List[Tuple[float, float]]] = normalize_poi_points_by_type({})
    way_coords_by_id: Dict[int, List[Tuple[float, float]]] = {}
    for el in elements:
        if el.get("type") == "way":
            coords = _coords_for_way(el, node_coords)
            if coords:
                way_coords_by_id[int(el["id"])] = coords

    for el in elements:
        etype = el.get("type", "")
        tags = _string_tags(el.get("tags", {}))

        if etype == "way" and "highway" in tags:
            hw_type = tags["highway"]
            if hw_type in _DEFAULT_WIDTH_M:
                # Resolve node refs to coordinates
                coords = _coords_for_way(el, node_coords)
                if len(coords) >= 2:
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

        if etype == "way" and "building" in tags:
            coords = _coords_for_way(el, node_coords)
            if len(coords) >= 3:
                coords = _closed_polygon_coords(coords)
                buildings.append(
                    OsmBuilding(
                        osm_id=int(el["id"]),
                        coords=coords,
                        tags=dict(tags),
                    )
                )

        if etype == "way" and _is_semantic_polygon(tags):
            coords = _coords_for_way(el, node_coords, close=True)
            if len(coords) >= 4:
                land_use_polygons.append(
                    OsmLandUsePolygon(
                        osm_id=int(el["id"]),
                        source_type="way",
                        coords=coords,
                        tags=dict(tags),
                    )
                )

        if etype == "relation" and _is_semantic_polygon(tags):
            coords = _join_relation_outer_coords(el, way_coords_by_id)
            if len(coords) >= 4:
                land_use_polygons.append(
                    OsmLandUsePolygon(
                        osm_id=int(el["id"]),
                        source_type="relation",
                        coords=coords,
                        tags=dict(tags),
                    )
                )

        if etype == "node":
            lon_lat: Optional[Tuple[float, float]] = None
            if "lon" in el and "lat" in el:
                lon_lat = (float(el["lon"]), float(el["lat"]))
            else:
                lon_lat = node_coords.get(int(el["id"]))
            if lon_lat is None:
                continue

            for poi_type in detect_poi_types_from_tags(tags):
                poi_points_by_type.setdefault(poi_type, []).append(lon_lat)
            for semantic_type in _semantic_point_types_from_tags(tags):
                semantic_points_by_type.setdefault(semantic_type, []).append(lon_lat)

    entrances = list(poi_points_by_type.get("entrance", []))
    bus_stops = list(poi_points_by_type.get("bus_stop", []))
    fire_points = list(poi_points_by_type.get(CANONICAL_FIRE_POI, []))
    semantic_blocks = [
        _semantic_block_from_polygon(index, polygon)
        for index, polygon in enumerate(land_use_polygons)
    ]

    logger.info(
        "Parsed OSM: %d roads, %d semantic polygons, poi=%s",
        len(roads),
        len(land_use_polygons),
        {
            poi_type: len(points)
            for poi_type, points in poi_points_by_type.items()
            if points
        },
    )
    return OsmFeatures(
        roads=roads,
        buildings=buildings,
        land_use_polygons=land_use_polygons,
        semantic_blocks=semantic_blocks,
        entrances=entrances,
        bus_stops=bus_stops,
        fire_points=fire_points,
        poi_points_by_type=poi_points_by_type,
        semantic_points_by_type=semantic_points_by_type,
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
    proj_land_use_polygons: List[OsmLandUsePolygon] = []
    for polygon in features.land_use_polygons:
        proj_land_use_polygons.append(
            OsmLandUsePolygon(
                osm_id=polygon.osm_id,
                source_type=polygon.source_type,
                coords=[_to_local(lon, lat) for lon, lat in polygon.coords],
                tags=dict(polygon.tags),
            )
        )
    proj_semantic_blocks: List[OsmSemanticBlock] = []
    for block in features.semantic_blocks:
        coords = [_to_local(lon, lat) for lon, lat in block.coords]
        centroid = _to_local(block.centroid[0], block.centroid[1])
        proj_semantic_blocks.append(
            OsmSemanticBlock(
                block_id=block.block_id,
                osm_id=block.osm_id,
                source_type=block.source_type,
                coords=coords,
                centroid=centroid,
                tags=dict(block.tags),
                semantic_profile_id=block.semantic_profile_id,
                semantic_reasons=list(block.semantic_reasons),
                confidence=float(block.confidence),
                poi_counts=dict(block.poi_counts),
            )
        )

    proj_poi_points_by_type = {
        poi_type: [_to_local(lon, lat) for lon, lat in points]
        for poi_type, points in extract_poi_points_by_type(features).items()
    }
    proj_semantic_points_by_type = {
        point_type: [_to_local(lon, lat) for lon, lat in points]
        for point_type, points in getattr(features, "semantic_points_by_type", {}).items()
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
        land_use_polygons=proj_land_use_polygons,
        semantic_blocks=proj_semantic_blocks,
        entrances=proj_entrances,
        bus_stops=proj_bus_stops,
        fire_points=proj_fire_points,
        poi_points_by_type=proj_poi_points_by_type,
        semantic_points_by_type=proj_semantic_points_by_type,
        bbox_m=bbox_m,
        origin_utm=(origin_e, origin_n),
        utm_epsg=utm_epsg,
    )
