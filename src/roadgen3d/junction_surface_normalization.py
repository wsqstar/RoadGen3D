"""Canonical junction surface normalization for 3D scene generation."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping, Sequence


PLANAR_SURFACE_ROLE_PRIORITY: Sequence[str] = (
    "bike_lane",
    "bus_lane",
    "parking_lane",
    "carriageway",
    "sidewalk",
    "furnishing",
    "context_ground",
)
OVERLAY_SURFACE_ROLES = {"crossing"}


def normalize_junction_surface_geometries(junction_geometries: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Return junction geometry records with canonical normalized surface patches.

    The raw patch buckets remain present for diagnostics/provenance, but consumers
    should render ``normalized_surface_patches`` when it is available.
    """

    return [normalize_junction_surface_geometry(junction) for junction in junction_geometries]


def normalize_junction_surface_geometry(junction: Mapping[str, Any]) -> Dict[str, Any]:
    from shapely.geometry import GeometryCollection
    from shapely.ops import unary_union

    normalized_junction: Dict[str, Any] = dict(junction)
    junction_id = str(junction.get("junction_id", "") or "junction")
    input_counts: Dict[str, int] = defaultdict(int)
    skipped_geometries: List[Dict[str, Any]] = []
    planar_by_role: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    overlays_by_role: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    def add_source(
        source_kind: str,
        source: Mapping[str, Any],
        *,
        geometry: Any | None = None,
        default_role: str = "carriageway",
        source_id: str = "",
    ) -> None:
        input_counts[source_kind] += 1
        raw_geometry = geometry if geometry is not None else source.get("geometry")
        cleaned = _clean_polygonal_geometry(raw_geometry)
        resolved_source_id = source_id or _source_identifier(source_kind, source)
        if cleaned is None or getattr(cleaned, "is_empty", True):
            skipped_geometries.append(
                {
                    "source_kind": source_kind,
                    "source_id": resolved_source_id,
                    "reason": "empty_or_non_polygonal_geometry",
                }
            )
            return
        role = _canonical_surface_role(source, default_role=default_role)
        record = {
            "source_kind": source_kind,
            "source_id": resolved_source_id,
            "surface_role": role,
            "geometry": cleaned,
            "area_m2": float(getattr(cleaned, "area", 0.0) or 0.0),
        }
        horizontal_axes = _coerce_horizontal_axes(source.get("horizontal_axes"))
        if horizontal_axes is not None:
            record["horizontal_axes"] = horizontal_axes
        if role in OVERLAY_SURFACE_ROLES:
            overlays_by_role[role].append(record)
        else:
            planar_by_role[role].append(record)

    canonical_surface_patches = [
        patch
        for patch in (junction.get("canonical_surface_patches", []) or ())
        if isinstance(patch, Mapping)
    ]
    if canonical_surface_patches:
        for patch in canonical_surface_patches:
            add_source("canonical_surface_patch", patch, default_role=str(patch.get("surface_role", "carriageway") or "carriageway"))
    else:
        carriageway_core = junction.get("carriageway_core") or junction.get("junction_core_rect")
        if carriageway_core is not None:
            add_source(
                "carriageway_core" if junction.get("carriageway_core") is not None else "junction_core_rect",
                {},
                geometry=carriageway_core,
                default_role="carriageway",
                source_id="carriageway_core" if junction.get("carriageway_core") is not None else "junction_core_rect",
            )
        for patch in junction.get("turn_lane_patches", []) or ():
            if isinstance(patch, Mapping):
                add_source("turn_lane_patch", patch, default_role="carriageway")
        for patch in junction.get("lane_surface_patches", []) or ():
            if isinstance(patch, Mapping):
                add_source("lane_surface_patch", patch, default_role="carriageway")
        for patch in junction.get("merged_surface_patches", []) or ():
            if isinstance(patch, Mapping):
                add_source("merged_surface_patch", patch, default_role="carriageway")
        for bucket_name, default_role in (
            ("sidewalk_corner_patches", "sidewalk"),
            ("nearroad_corner_patches", "furnishing"),
            ("frontage_corner_patches", "context_ground"),
        ):
            for patch in junction.get(bucket_name, []) or ():
                if isinstance(patch, Mapping):
                    add_source(bucket_name, patch, default_role=default_role)
    for patch in junction.get("crosswalk_patches", []) or ():
        if isinstance(patch, Mapping):
            add_source("crosswalk_patch", patch, default_role="crossing")

    normalized_patches: List[Dict[str, Any]] = []
    total_planar_source_area = sum(
        float(source["area_m2"]) for sources in planar_by_role.values() for source in sources
    )
    total_overlay_source_area = sum(
        float(source["area_m2"]) for sources in overlays_by_role.values() for source in sources
    )
    planar_sources = [source["geometry"] for sources in planar_by_role.values() for source in sources]
    planar_union = _clean_polygonal_geometry(unary_union(planar_sources)) if planar_sources else None
    planar_union_area = float(getattr(planar_union, "area", 0.0) or 0.0)
    same_role_overlap_removed = 0.0
    priority_overlap_removed = 0.0
    occupied_geometry = GeometryCollection()

    for role in PLANAR_SURFACE_ROLE_PRIORITY:
        sources = planar_by_role.get(role, [])
        if not sources:
            continue
        role_source_area = sum(float(source["area_m2"]) for source in sources)
        role_union = _clean_polygonal_geometry(unary_union([source["geometry"] for source in sources]))
        if role_union is None or getattr(role_union, "is_empty", True):
            continue
        same_role_overlap_removed += max(0.0, role_source_area - float(role_union.area))
        visible_geometry = role_union
        if not getattr(occupied_geometry, "is_empty", True):
            visible_geometry = _clean_polygonal_geometry(role_union.difference(occupied_geometry))
            if visible_geometry is None:
                priority_overlap_removed += float(role_union.area)
                continue
        priority_overlap_removed += max(0.0, float(role_union.area) - float(visible_geometry.area))
        components = _polygon_components(visible_geometry)
        for component_index, component in enumerate(components):
            normalized_patches.append(
                _normalized_patch_record(
                    junction_id=junction_id,
                    role=role,
                    component_index=component_index,
                    geometry=component,
                    sources=sources,
                    overlay=False,
                )
            )
        if components:
            occupied_geometry = _clean_polygonal_geometry(unary_union([occupied_geometry, visible_geometry])) or occupied_geometry

    for role in sorted(overlays_by_role):
        sources = overlays_by_role[role]
        if role == "crossing":
            component_index = 0
            for source in sources:
                for component in _polygon_components(source["geometry"]):
                    normalized_patches.append(
                        _normalized_patch_record(
                            junction_id=junction_id,
                            role=role,
                            component_index=component_index,
                            geometry=component,
                            sources=[source],
                            overlay=True,
                        )
                    )
                    component_index += 1
            continue
        role_union = _clean_polygonal_geometry(unary_union([source["geometry"] for source in sources]))
        if role_union is None or getattr(role_union, "is_empty", True):
            continue
        for component_index, component in enumerate(_polygon_components(role_union)):
            normalized_patches.append(
                _normalized_patch_record(
                    junction_id=junction_id,
                    role=role,
                    component_index=component_index,
                    geometry=component,
                    sources=sources,
                    overlay=True,
                )
            )

    normalized_junction["normalized_surface_patches"] = normalized_patches
    normalized_junction["surface_normalization_debug"] = {
        "generation_mode": "junction_surface_normalization_v1",
        "input_counts": dict(sorted(input_counts.items())),
        "normalized_surface_count": int(len(normalized_patches)),
        "skipped_geometry_count": int(len(skipped_geometries)),
        "skipped_geometries": skipped_geometries,
        "planar_source_area_m2": round(float(total_planar_source_area), 3),
        "planar_union_area_m2": round(float(planar_union_area), 3),
        "overlay_source_area_m2": round(float(total_overlay_source_area), 3),
        "normalized_planar_area_m2": round(
            float(sum(patch["geometry"].area for patch in normalized_patches if not patch.get("is_overlay"))),
            3,
        ),
        "normalized_overlay_area_m2": round(
            float(sum(patch["geometry"].area for patch in normalized_patches if patch.get("is_overlay"))),
            3,
        ),
        "same_role_overlap_removed_area_m2": round(float(same_role_overlap_removed), 3),
        "priority_overlap_removed_area_m2": round(float(priority_overlap_removed), 3),
        "overlap_removed_area_m2": round(float(same_role_overlap_removed + priority_overlap_removed), 3),
    }
    return normalized_junction


def _normalized_patch_record(
    *,
    junction_id: str,
    role: str,
    component_index: int,
    geometry: Any,
    sources: Sequence[Mapping[str, Any]],
    overlay: bool,
) -> Dict[str, Any]:
    contributing_sources = [
        source
        for source in sources
        if _source_intersects_component(source.get("geometry"), geometry)
    ]
    if not contributing_sources:
        contributing_sources = list(sources)
    source_ids = sorted({str(source.get("source_id", "") or "") for source in contributing_sources if source.get("source_id")})
    source_kinds = sorted({str(source.get("source_kind", "") or "") for source in contributing_sources if source.get("source_kind")})
    surface_id = f"{junction_id}_normalized_{role}_{component_index:02d}"
    record = {
        "surface_id": surface_id,
        "surface_kind": "normalized",
        "surface_role": role,
        "geometry": geometry,
        "component_index": int(component_index),
        "source_ids": source_ids,
        "source_kinds": source_kinds,
        "is_overlay": bool(overlay),
        "area_m2": float(getattr(geometry, "area", 0.0) or 0.0),
    }
    horizontal_axes = _dominant_horizontal_axes(contributing_sources, geometry)
    if horizontal_axes is not None:
        record["horizontal_axes"] = horizontal_axes
    return record


def _coerce_horizontal_axes(value: Any) -> List[List[float]] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    axes: List[List[float]] = []
    for axis in value:
        if not isinstance(axis, (list, tuple)) or len(axis) != 2:
            return None
        try:
            x = float(axis[0])
            y = float(axis[1])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(x) or not math.isfinite(y):
            return None
        length = math.hypot(x, y)
        if length <= 1e-9:
            return None
        axes.append([x / length, y / length])
    return axes


def _dominant_horizontal_axes(
    sources: Sequence[Mapping[str, Any]],
    component_geometry: Any,
) -> List[List[float]] | None:
    candidates: List[tuple[float, List[List[float]]]] = []
    for source in sources:
        horizontal_axes = _coerce_horizontal_axes(source.get("horizontal_axes"))
        if horizontal_axes is None:
            continue
        candidates.append((_source_component_overlap_area(source.get("geometry"), component_geometry), horizontal_axes))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _canonical_surface_role(source: Mapping[str, Any], *, default_role: str) -> str:
    explicit_role = str(source.get("surface_role", "") or "").strip().lower()
    default = str(default_role or "carriageway").strip().lower()
    strip_kind = str(source.get("strip_kind", "") or "").strip().lower()
    stack_kind = str(source.get("stack_kind", "") or "").strip().lower()
    if explicit_role in {"crossing", "crosswalk"}:
        return "crossing"
    if explicit_role in {
        "bike_lane",
        "bus_lane",
        "parking_lane",
        "carriageway",
        "sidewalk",
        "furnishing",
        "context_ground",
    }:
        return explicit_role
    if strip_kind == "bike_lane":
        return "bike_lane"
    if strip_kind == "bus_lane":
        return "bus_lane"
    if strip_kind == "parking_lane":
        return "parking_lane"
    if strip_kind == "clear_sidewalk":
        return "sidewalk"
    if strip_kind == "frontage_reserve":
        return "context_ground"
    if "furnishing" in strip_kind or "buffer" in strip_kind:
        return "furnishing"
    if strip_kind == "drive_lane" or stack_kind == "center":
        return "carriageway"
    if default in {"crossing", "crosswalk"}:
        return "crossing"
    if default in {
        "bike_lane",
        "bus_lane",
        "parking_lane",
        "carriageway",
        "sidewalk",
        "furnishing",
        "context_ground",
    }:
        return default
    return "carriageway"


def _source_identifier(source_kind: str, source: Mapping[str, Any]) -> str:
    for key in ("surface_id", "patch_id", "lane_id", "polyline_id", "connector_id", "id"):
        value = source.get(key)
        if value:
            return str(value)
    return source_kind


def _clean_polygonal_geometry(geometry: Any) -> Any | None:
    if geometry is None or getattr(geometry, "is_empty", True):
        return None
    try:
        from shapely.geometry import GeometryCollection
        from shapely.ops import unary_union
    except ImportError:
        return None

    geom = geometry
    if not getattr(geom, "is_valid", True):
        try:
            from shapely import make_valid

            geom = make_valid(geom)
        except Exception:
            try:
                geom = geom.buffer(0)
            except Exception:
                return None
    polygons = _polygon_components(geom)
    if not polygons:
        return None
    if len(polygons) == 1:
        cleaned = polygons[0]
    else:
        cleaned = unary_union(polygons)
    if not getattr(cleaned, "is_valid", True):
        try:
            cleaned = cleaned.buffer(0)
        except Exception:
            return None
    if getattr(cleaned, "geom_type", "") == "GeometryCollection":
        polygons = _polygon_components(cleaned)
        cleaned = unary_union(polygons) if polygons else GeometryCollection()
    return None if getattr(cleaned, "is_empty", True) else cleaned


def _polygon_components(geometry: Any) -> List[Any]:
    geom_type = getattr(geometry, "geom_type", "")
    if geom_type == "Polygon":
        return [geometry] if not getattr(geometry, "is_empty", True) and float(getattr(geometry, "area", 0.0) or 0.0) > 1e-8 else []
    if geom_type == "MultiPolygon":
        return [
            polygon
            for polygon in geometry.geoms
            if not getattr(polygon, "is_empty", True) and float(getattr(polygon, "area", 0.0) or 0.0) > 1e-8
        ]
    if geom_type == "GeometryCollection":
        polygons: List[Any] = []
        for item in geometry.geoms:
            polygons.extend(_polygon_components(item))
        return polygons
    return []


def _source_intersects_component(source_geometry: Any, component: Any) -> bool:
    if source_geometry is None:
        return False
    try:
        return bool(source_geometry.intersects(component)) and float(source_geometry.intersection(component).area) > 1e-8
    except Exception:
        return False


def _source_component_overlap_area(source_geometry: Any, component: Any) -> float:
    if source_geometry is None:
        return 0.0
    try:
        return float(source_geometry.intersection(component).area)
    except Exception:
        return 0.0
