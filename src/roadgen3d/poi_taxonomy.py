"""Shared POI taxonomy for OSM parsing, discovery, constraints, and UI."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


CANONICAL_FIRE_POI = "fire_hydrant"

_ALIASES = {
    "fire": CANONICAL_FIRE_POI,
    "hydrant": CANONICAL_FIRE_POI,
}

_LEGACY_FIELDS = {
    "entrance": "entrance_points_xz",
    "bus_stop": "bus_stop_points_xz",
    CANONICAL_FIRE_POI: "fire_points_xz",
}

_LEGACY_LOCAL_FIELDS = {
    "entrance": "entrance_points",
    "bus_stop": "bus_stop_points",
    CANONICAL_FIRE_POI: "fire_points",
}

_LEGACY_WGS84_FIELDS = {
    "entrance": "entrances",
    "bus_stop": "bus_stops",
    CANONICAL_FIRE_POI: "fire_points",
}


@dataclass(frozen=True)
class PoiTypeSpec:
    normalized_type: str
    display_name: str
    weight: float
    is_core: bool
    asset_category: Optional[str] = None
    cluster_radius_m: Optional[float] = None
    overpass_clauses: Tuple[str, ...] = ()
    marker: str = "o"
    color_hex: str = "#777777"
    zone_fill_rgba: Tuple[float, float, float, float] = (0.5, 0.5, 0.5, 0.10)


_POI_SPECS: Tuple[PoiTypeSpec, ...] = (
    PoiTypeSpec(
        normalized_type="entrance",
        display_name="Entrance",
        weight=1.0,
        is_core=True,
        overpass_clauses=('node["entrance"]({bbox});',),
        marker="^",
        color_hex="#00aaff",
        zone_fill_rgba=(0.0, 0.39, 1.0, 0.10),
    ),
    PoiTypeSpec(
        normalized_type="bus_stop",
        display_name="Bus Stop",
        weight=1.5,
        is_core=True,
        asset_category="bus_stop",
        overpass_clauses=(
            'node["highway"="bus_stop"]({bbox});',
            'node["public_transport"="platform"]({bbox});',
        ),
        marker="D",
        color_hex="#ffd200",
        zone_fill_rgba=(1.0, 0.82, 0.0, 0.10),
    ),
    PoiTypeSpec(
        normalized_type=CANONICAL_FIRE_POI,
        display_name="Fire Hydrant",
        weight=1.3,
        is_core=True,
        asset_category="hydrant",
        overpass_clauses=('node["emergency"="fire_hydrant"]({bbox});',),
        marker="h",
        color_hex="#ff5a00",
        zone_fill_rgba=(1.0, 0.55, 0.0, 0.10),
    ),
    PoiTypeSpec(
        normalized_type="crossing",
        display_name="Crossing",
        weight=1.2,
        is_core=True,
        overpass_clauses=('node["highway"="crossing"]({bbox});',),
        marker="P",
        color_hex="#e6194b",
        zone_fill_rgba=(0.90, 0.10, 0.29, 0.10),
    ),
    PoiTypeSpec(
        normalized_type="traffic_signals",
        display_name="Traffic Signals",
        weight=1.2,
        is_core=True,
        overpass_clauses=('node["highway"="traffic_signals"]({bbox});',),
        marker="X",
        color_hex="#f58231",
        zone_fill_rgba=(0.96, 0.51, 0.19, 0.10),
    ),
    PoiTypeSpec(
        normalized_type="parking_entrance",
        display_name="Parking Entrance",
        weight=1.0,
        is_core=True,
        overpass_clauses=('node["amenity"="parking_entrance"]({bbox});',),
        marker="s",
        color_hex="#8c564b",
        zone_fill_rgba=(0.55, 0.34, 0.29, 0.10),
    ),
    PoiTypeSpec(
        normalized_type="subway_entrance",
        display_name="Subway Entrance",
        weight=1.4,
        is_core=True,
        overpass_clauses=('node["railway"="subway_entrance"]({bbox});',),
        marker="v",
        color_hex="#4363d8",
        zone_fill_rgba=(0.26, 0.39, 0.85, 0.10),
    ),
    PoiTypeSpec(
        normalized_type="post_box",
        display_name="Post Box",
        weight=0.5,
        is_core=False,
        asset_category="mailbox",
        overpass_clauses=('node["amenity"="post_box"]({bbox});',),
        marker="p",
        color_hex="#911eb4",
        zone_fill_rgba=(0.57, 0.12, 0.71, 0.10),
    ),
    PoiTypeSpec(
        normalized_type="waste_basket",
        display_name="Waste Basket",
        weight=0.4,
        is_core=False,
        asset_category="trash",
        cluster_radius_m=8.0,
        overpass_clauses=('node["amenity"="waste_basket"]({bbox});',),
        marker="*",
        color_hex="#808000",
        zone_fill_rgba=(0.50, 0.50, 0.00, 0.10),
    ),
    PoiTypeSpec(
        normalized_type="bollard",
        display_name="Bollard",
        weight=0.3,
        is_core=False,
        asset_category="bollard",
        cluster_radius_m=4.0,
        overpass_clauses=('node["barrier"="bollard"]({bbox});',),
        marker="8",
        color_hex="#f032e6",
        zone_fill_rgba=(0.94, 0.20, 0.90, 0.10),
    ),
)

POI_SPECS: Dict[str, PoiTypeSpec] = {
    spec.normalized_type: spec
    for spec in _POI_SPECS
}
SUPPORTED_POI_TYPES: Tuple[str, ...] = tuple(spec.normalized_type for spec in _POI_SPECS)
CORE_POI_TYPES: Tuple[str, ...] = tuple(spec.normalized_type for spec in _POI_SPECS if spec.is_core)


def canonicalize_poi_type(poi_type: str) -> str:
    key = str(poi_type or "").strip().lower()
    return _ALIASES.get(key, key)


def get_poi_spec(poi_type: str) -> PoiTypeSpec:
    canonical = canonicalize_poi_type(poi_type)
    return POI_SPECS[canonical]


def zero_poi_counts() -> Dict[str, int]:
    return {poi_type: 0 for poi_type in SUPPORTED_POI_TYPES}


def normalize_poi_counts(counts: Mapping[str, int] | None) -> Dict[str, int]:
    normalized = zero_poi_counts()
    if not counts:
        return normalized
    for key, value in counts.items():
        canonical = canonicalize_poi_type(str(key))
        if canonical in normalized:
            normalized[canonical] += int(value)
    return normalized


def poi_weighted_score(counts: Mapping[str, int] | None) -> float:
    normalized = normalize_poi_counts(counts)
    return round(
        sum(float(POI_SPECS[poi_type].weight) * float(normalized.get(poi_type, 0)) for poi_type in SUPPORTED_POI_TYPES),
        4,
    )


def core_poi_count(counts: Mapping[str, int] | None) -> int:
    normalized = normalize_poi_counts(counts)
    return int(sum(int(normalized.get(poi_type, 0)) for poi_type in CORE_POI_TYPES))


def qualifies_poi_counts(
    counts: Mapping[str, int] | None,
    *,
    min_score: float = 2.0,
    min_core_count: int = 1,
) -> bool:
    normalized = normalize_poi_counts(counts)
    return poi_weighted_score(normalized) >= float(min_score) and core_poi_count(normalized) >= int(min_core_count)


def poi_breakdown_string(counts: Mapping[str, int] | None) -> str:
    normalized = normalize_poi_counts(counts)
    active = [
        f"{poi_type}:{normalized[poi_type]}"
        for poi_type in SUPPORTED_POI_TYPES
        if int(normalized[poi_type]) > 0
    ]
    return ", ".join(active) if active else "-"


def asset_backed_poi_types() -> Tuple[str, ...]:
    return tuple(spec.normalized_type for spec in _POI_SPECS if spec.asset_category)


def asset_backed_poi_types_for_category(category: str) -> Tuple[str, ...]:
    cat = str(category)
    return tuple(
        spec.normalized_type
        for spec in _POI_SPECS
        if spec.asset_category == cat
    )


def asset_category_for_poi(poi_type: str) -> Optional[str]:
    return POI_SPECS[canonicalize_poi_type(poi_type)].asset_category


def cluster_radius_for_poi(poi_type: str) -> Optional[float]:
    return POI_SPECS[canonicalize_poi_type(poi_type)].cluster_radius_m


def overpass_poi_clauses(bbox_placeholder: str = "{bbox}") -> Tuple[str, ...]:
    clauses: List[str] = []
    for spec in _POI_SPECS:
        for clause in spec.overpass_clauses:
            clauses.append(clause.replace("{bbox}", bbox_placeholder))
    return tuple(clauses)


def detect_poi_types_from_tags(tags: Mapping[str, object]) -> Tuple[str, ...]:
    matches: List[str] = []
    if "entrance" in tags:
        matches.append("entrance")
    if tags.get("highway") == "bus_stop" or tags.get("public_transport") == "platform":
        matches.append("bus_stop")
    if tags.get("emergency") == "fire_hydrant":
        matches.append(CANONICAL_FIRE_POI)
    if tags.get("highway") == "crossing":
        matches.append("crossing")
    if tags.get("highway") == "traffic_signals":
        matches.append("traffic_signals")
    if tags.get("amenity") == "parking_entrance":
        matches.append("parking_entrance")
    if tags.get("railway") == "subway_entrance":
        matches.append("subway_entrance")
    if tags.get("amenity") == "post_box":
        matches.append("post_box")
    if tags.get("amenity") == "waste_basket":
        matches.append("waste_basket")
    if tags.get("barrier") == "bollard":
        matches.append("bollard")
    return tuple(sorted(set(matches)))


def normalize_poi_points_by_type(
    mapping: Mapping[str, Sequence[Tuple[float, float]]] | None,
) -> Dict[str, List[Tuple[float, float]]]:
    normalized: Dict[str, List[Tuple[float, float]]] = {poi_type: [] for poi_type in SUPPORTED_POI_TYPES}
    if not mapping:
        return normalized
    for key, points in mapping.items():
        canonical = canonicalize_poi_type(str(key))
        if canonical not in normalized:
            continue
        normalized[canonical].extend((float(point[0]), float(point[1])) for point in points)
    return normalized


def extract_poi_points_by_type(obj: object, *, suffix: str = "") -> Dict[str, List[Tuple[float, float]]]:
    field_name = "poi_points_by_type" if not suffix else f"poi_points_by_type_{suffix}"
    raw = getattr(obj, field_name, None)
    if raw:
        return normalize_poi_points_by_type(raw)

    if suffix == "xz":
        field_map = _LEGACY_FIELDS
    elif suffix:
        field_map = {}
    else:
        if hasattr(obj, "entrance_points") or hasattr(obj, "bus_stop_points"):
            field_map = _LEGACY_LOCAL_FIELDS
        elif hasattr(obj, "entrances") or hasattr(obj, "bus_stops"):
            field_map = _LEGACY_WGS84_FIELDS
        else:
            field_map = _LEGACY_LOCAL_FIELDS

    recovered: Dict[str, List[Tuple[float, float]]] = {poi_type: [] for poi_type in SUPPORTED_POI_TYPES}
    for poi_type, legacy_field in field_map.items():
        for point in getattr(obj, legacy_field, []) or []:
            recovered[poi_type].append((float(point[0]), float(point[1])))
    return recovered


def count_poi_points(mapping: Mapping[str, Sequence[Tuple[float, float]]] | None) -> Dict[str, int]:
    normalized = normalize_poi_points_by_type(mapping)
    return {poi_type: len(points) for poi_type, points in normalized.items()}


def points_for_type(mapping: Mapping[str, Sequence[Tuple[float, float]]] | None, poi_type: str) -> Tuple[Tuple[float, float], ...]:
    normalized = normalize_poi_points_by_type(mapping)
    canonical = canonicalize_poi_type(poi_type)
    return tuple(normalized.get(canonical, ()))


def cluster_points(points: Sequence[Tuple[float, float]], radius_m: float) -> Tuple[Tuple[float, float], ...]:
    if not points:
        return ()
    if radius_m <= 0.0:
        return tuple((float(point[0]), float(point[1])) for point in points)

    remaining = [(float(point[0]), float(point[1])) for point in points]
    clusters: List[Tuple[float, float]] = []
    while remaining:
        seed = remaining.pop(0)
        members = [seed]
        changed = True
        while changed:
            changed = False
            centroid_x = sum(point[0] for point in members) / float(len(members))
            centroid_y = sum(point[1] for point in members) / float(len(members))
            leftovers: List[Tuple[float, float]] = []
            for point in remaining:
                if math.hypot(point[0] - centroid_x, point[1] - centroid_y) <= radius_m:
                    members.append(point)
                    changed = True
                else:
                    leftovers.append(point)
            remaining = leftovers
        centroid_x = sum(point[0] for point in members) / float(len(members))
        centroid_y = sum(point[1] for point in members) / float(len(members))
        clusters.append((centroid_x, centroid_y))
    return tuple(clusters)


def cluster_asset_backed_poi_points(
    mapping: Mapping[str, Sequence[Tuple[float, float]]] | None,
) -> Dict[str, Tuple[Tuple[float, float], ...]]:
    normalized = normalize_poi_points_by_type(mapping)
    clustered: Dict[str, Tuple[Tuple[float, float], ...]] = {}
    for poi_type in asset_backed_poi_types():
        radius = cluster_radius_for_poi(poi_type)
        points = normalized.get(poi_type, [])
        if radius is None:
            clustered[poi_type] = tuple(points)
        else:
            clustered[poi_type] = cluster_points(points, float(radius))
    return clustered


def asset_backed_poi_anchor_counts(
    mapping: Mapping[str, Sequence[Tuple[float, float]]] | None,
) -> Dict[str, int]:
    return {
        poi_type: len(points)
        for poi_type, points in cluster_asset_backed_poi_points(mapping).items()
    }


def asset_backed_category_counts(
    mapping: Mapping[str, Sequence[Tuple[float, float]]] | None,
) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for poi_type, count in asset_backed_poi_anchor_counts(mapping).items():
        category = asset_category_for_poi(poi_type)
        if category is None:
            continue
        counts[category] = counts.get(category, 0) + int(count)
    return counts


def poi_plot_config(poi_type: str) -> Dict[str, object]:
    spec = get_poi_spec(poi_type)
    return {
        "marker": spec.marker,
        "color": spec.color_hex,
        "label": spec.display_name,
        "zone_fill_rgba": spec.zone_fill_rgba,
    }


def nonempty_poi_points(mapping: Mapping[str, Sequence[Tuple[float, float]]] | None) -> Dict[str, Tuple[Tuple[float, float], ...]]:
    normalized = normalize_poi_points_by_type(mapping)
    return {
        poi_type: tuple(points)
        for poi_type, points in normalized.items()
        if points
    }
