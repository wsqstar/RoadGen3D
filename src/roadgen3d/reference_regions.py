"""Region-first derivation helpers for reference annotations."""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from .reference_annotation import AnnotatedRegion, ReferenceAnnotation, parse_reference_annotation

DEFAULT_MIN_DERIVED_REGION_AREA_M2 = 12.0
ROAD_CUT_BUFFER_EPSILON_M = 0.25


def pixel_to_local(annotation: ReferenceAnnotation, *, x: float, y: float) -> Tuple[float, float]:
    center_x = float(annotation.image_width_px) * 0.5
    center_y = float(annotation.image_height_px) * 0.5
    ppm = max(float(annotation.pixels_per_meter), 1e-6)
    return ((float(x) - center_x) / ppm, (center_y - float(y)) / ppm)


def local_to_pixel(annotation: ReferenceAnnotation, *, x: float, z: float) -> Dict[str, float]:
    center_x = float(annotation.image_width_px) * 0.5
    center_y = float(annotation.image_height_px) * 0.5
    ppm = max(float(annotation.pixels_per_meter), 1e-6)
    return {"x": float(center_x + float(x) * ppm), "y": float(center_y - float(z) * ppm)}


def region_to_local_points(region: AnnotatedRegion, annotation: ReferenceAnnotation) -> List[Tuple[float, float]]:
    return [pixel_to_local(annotation, x=point.x, y=point.y) for point in region.points]


def _clean_polygon(geometry: Any) -> Any:
    from shapely.geometry import MultiPolygon, Polygon

    if geometry is None:
        return MultiPolygon()
    if getattr(geometry, "is_empty", True):
        return MultiPolygon()
    if not getattr(geometry, "is_valid", True):
        try:
            geometry = geometry.buffer(0)
        except Exception:
            return MultiPolygon()
    if getattr(geometry, "is_empty", True):
        return MultiPolygon()
    if isinstance(geometry, Polygon):
        return geometry
    if isinstance(geometry, MultiPolygon):
        return geometry
    if getattr(geometry, "geom_type", "") == "GeometryCollection":
        polygons = [item for item in geometry.geoms if isinstance(item, Polygon) and not item.is_empty]
        if not polygons:
            return MultiPolygon()
        return MultiPolygon(polygons)
    return geometry


def _iter_polygons(geometry: Any) -> Iterable[Any]:
    from shapely.geometry import MultiPolygon, Polygon

    clean = _clean_polygon(geometry)
    if isinstance(clean, Polygon):
        yield clean
    elif isinstance(clean, MultiPolygon):
        for item in clean.geoms:
            if not item.is_empty and item.area > 0:
                yield item
    elif getattr(clean, "geom_type", "") == "GeometryCollection":
        for item in clean.geoms:
            if isinstance(item, Polygon) and not item.is_empty and item.area > 0:
                yield item


def polygon_from_region(region: AnnotatedRegion, annotation: ReferenceAnnotation) -> Any | None:
    from shapely.geometry import Polygon

    points = region_to_local_points(region, annotation)
    if len(points) < 3:
        return None
    polygon = Polygon(points)
    polygon = _clean_polygon(polygon)
    if getattr(polygon, "is_empty", True):
        return None
    return polygon


def scene_region_polygon_from_annotation(annotation: ReferenceAnnotation) -> Any | None:
    scene_regions = [item for item in annotation.regions if item.region_role == "scene_region"]
    if not scene_regions:
        return None
    polygons = [
        (region, polygon_from_region(region, annotation))
        for region in scene_regions
    ]
    polygons = [(region, polygon) for region, polygon in polygons if polygon is not None and not getattr(polygon, "is_empty", True)]
    if not polygons:
        return None
    _, largest = max(polygons, key=lambda item: float(getattr(item[1], "area", 0.0) or 0.0))
    return largest


def _collect_local_centerlines(annotation: ReferenceAnnotation) -> List[Tuple[str, Any, List[Tuple[float, float]]]]:
    items: List[Tuple[str, Any, List[Tuple[float, float]]]] = []
    for centerline in annotation.centerlines:
        points: List[Tuple[float, float]] = []
        for point in centerline.points:
            xy = pixel_to_local(annotation, x=point.x, y=point.y)
            if not points or points[-1] != xy:
                points.append(xy)
        if len(points) >= 2:
            items.append((str(centerline.feature_id), centerline, points))
    return items


def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _point_at_station(points: Sequence[Tuple[float, float]], station_m: float) -> Tuple[float, float]:
    remaining = max(float(station_m), 0.0)
    for start, end in zip(points[:-1], points[1:]):
        segment_length = _distance(start, end)
        if segment_length <= 1e-9:
            continue
        if remaining <= segment_length:
            ratio = remaining / segment_length
            return (
                float(start[0]) + (float(end[0]) - float(start[0])) * ratio,
                float(start[1]) + (float(end[1]) - float(start[1])) * ratio,
            )
        remaining -= segment_length
    return tuple(float(value) for value in points[-1]) if points else (0.0, 0.0)


def _polyline_points_between_stations(
    points: Sequence[Tuple[float, float]],
    station_start_m: float,
    station_end_m: float,
) -> List[Tuple[float, float]]:
    if len(points) < 2:
        return list(points)
    station_start_m = max(float(station_start_m), 0.0)
    station_end_m = max(float(station_end_m), station_start_m)
    result: List[Tuple[float, float]] = [_point_at_station(points, station_start_m)]
    cumulative = 0.0
    for start, end in zip(points[:-1], points[1:]):
        cumulative += _distance(start, end)
        if station_start_m < cumulative < station_end_m:
            result.append((float(end[0]), float(end[1])))
    result.append(_point_at_station(points, station_end_m))
    deduped: List[Tuple[float, float]] = []
    for point in result:
        if not deduped or _distance(deduped[-1], point) > 1e-6:
            deduped.append(point)
    return deduped


def _offset_polyline_by_lateral(
    points: Sequence[Tuple[float, float]],
    lateral_offset_m: float,
) -> List[Tuple[float, float]]:
    if len(points) < 2:
        return list(points)
    tangents: List[Tuple[float, float]] = []
    for start, end in zip(points[:-1], points[1:]):
        dx = float(end[0]) - float(start[0])
        dy = float(end[1]) - float(start[1])
        length = math.hypot(dx, dy)
        tangents.append((dx / length, dy / length) if length > 1e-9 else (1.0, 0.0))
    result: List[Tuple[float, float]] = []
    for index, point in enumerate(points):
        if index == 0:
            tangent = tangents[0]
        elif index == len(points) - 1:
            tangent = tangents[-1]
        else:
            prev_tangent = tangents[index - 1]
            next_tangent = tangents[index]
            tx = prev_tangent[0] + next_tangent[0]
            ty = prev_tangent[1] + next_tangent[1]
            length = math.hypot(tx, ty)
            tangent = (tx / length, ty / length) if length > 1e-9 else next_tangent
        normal = (float(tangent[1]), -float(tangent[0]))
        result.append(
            (
                float(point[0]) + normal[0] * float(lateral_offset_m),
                float(point[1]) + normal[1] * float(lateral_offset_m),
            )
        )
    return result


def _surface_annotation_polygon(
    centerline_points: Sequence[Tuple[float, float]],
    *,
    station_start_m: float,
    station_end_m: float,
    lateral_start_m: float,
    lateral_end_m: float,
) -> Any | None:
    from shapely.geometry import Polygon

    spine = _polyline_points_between_stations(centerline_points, station_start_m, station_end_m)
    if len(spine) < 2:
        return None
    edge_a = _offset_polyline_by_lateral(spine, lateral_start_m)
    edge_b = _offset_polyline_by_lateral(spine, lateral_end_m)
    ring = [*edge_a, *reversed(edge_b)]
    if len(ring) < 3:
        return None
    polygon = _clean_polygon(Polygon(ring))
    if getattr(polygon, "is_empty", True):
        return None
    return polygon


def _region_building_record(
    *,
    region_id: str,
    label: str,
    order_index: int,
    polygon: Any,
    source: str,
    source_region_id: str = "",
    land_use_type: str = "",
    side: str = "",
    nearest_centerline_id: str = "",
) -> Dict[str, Any]:
    bounds = polygon.bounds
    centroid = polygon.representative_point()
    exterior = list(polygon.exterior.coords)
    return {
        "region_id": str(region_id),
        "label": str(label),
        "order_index": int(order_index),
        "center_xz": (float(centroid.x), float(centroid.y)),
        "width_m": float(bounds[2] - bounds[0]),
        "height_m": float(bounds[3] - bounds[1]),
        "yaw_deg": 0.0,
        "polygon_xz": tuple((float(x), float(z)) for x, z in exterior),
        "source": str(source),
        "source_region_id": str(source_region_id),
        "land_use_type": str(land_use_type),
        "side": str(side),
        "nearest_centerline_id": str(nearest_centerline_id),
    }


def explicit_building_region_records_from_regions(annotation: ReferenceAnnotation) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for index, region in enumerate(item for item in annotation.regions if item.region_role == "building_region"):
        polygon = polygon_from_region(region, annotation)
        if polygon is None or getattr(polygon, "is_empty", True):
            continue
        for polygon_index, part in enumerate(_iter_polygons(polygon)):
            suffix = "" if polygon_index == 0 else f"_{polygon_index + 1:02d}"
            records.append(
                _region_building_record(
                    region_id=f"{region.feature_id}{suffix}",
                    label=region.label,
                    order_index=len(records),
                    polygon=part,
                    source="region",
                    source_region_id=region.source_region_id,
                    land_use_type=region.land_use_type,
                )
            )
    return records


def functional_region_records_from_regions(annotation: ReferenceAnnotation) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for region in annotation.regions:
        if region.region_role != "functional_zone":
            continue
        records.append(
            {
                "id": region.feature_id,
                "label": region.label,
                "kind": region.kind or "plaza",
                "points": region_to_local_points(region, annotation),
                "furniture_instances": [],
                "region_role": "functional_zone",
                "source": "region",
            }
        )
    return records


def _build_road_occupancy(annotation: ReferenceAnnotation, scene_polygon: Any) -> Tuple[Any, float]:
    from shapely.geometry import LineString, MultiPolygon, Point
    from shapely.ops import unary_union

    polygons: List[Any] = []
    for _, centerline, points in _collect_local_centerlines(annotation):
        if len(points) < 2:
            continue
        line = LineString(points)
        half_width_m = max(float(centerline.cross_section_width_m()) * 0.5 + ROAD_CUT_BUFFER_EPSILON_M, 0.5)
        polygon = _clean_polygon(line.buffer(half_width_m, cap_style="flat"))
        if not getattr(polygon, "is_empty", True):
            polygons.append(polygon)

    for roundabout in annotation.roundabouts:
        center = pixel_to_local(annotation, x=roundabout.x, y=roundabout.y)
        radius_m = max(float(roundabout.radius_px) / max(float(annotation.pixels_per_meter), 1e-6), 0.5)
        polygons.append(Point(center).buffer(radius_m + ROAD_CUT_BUFFER_EPSILON_M))

    default_junction_radius_m = max(
        [float(centerline.cross_section_width_m()) * 0.5 for centerline in annotation.centerlines] or [3.0]
    ) + 3.0
    for junction in annotation.junctions:
        center = pixel_to_local(annotation, x=junction.anchor_x, y=junction.anchor_y)
        polygons.append(Point(center).buffer(default_junction_radius_m))

    centerline_points_by_id = {
        centerline_id: tuple(points)
        for centerline_id, _, points in _collect_local_centerlines(annotation)
    }
    for surface in annotation.surface_annotations:
        centerline_points = centerline_points_by_id.get(str(surface.centerline_id))
        if not centerline_points:
            continue
        polygon = _surface_annotation_polygon(
            centerline_points,
            station_start_m=surface.station_start_m,
            station_end_m=surface.station_end_m,
            lateral_start_m=surface.lateral_start_m,
            lateral_end_m=surface.lateral_end_m,
        )
        if polygon is not None and not getattr(polygon, "is_empty", True):
            polygons.append(polygon)

    if not polygons:
        return MultiPolygon(), 0.0
    occupancy = _clean_polygon(unary_union(polygons))
    clipped = _clean_polygon(occupancy.intersection(scene_polygon))
    return clipped, float(getattr(clipped, "area", 0.0) or 0.0)


def _nearest_centerline_side(
    point: Tuple[float, float],
    local_centerlines: Sequence[Tuple[str, Any, Sequence[Tuple[float, float]]]],
) -> Tuple[str, str, float]:
    best_id = ""
    best_side = ""
    best_distance = float("inf")
    px, py = float(point[0]), float(point[1])
    for centerline_id, _, points in local_centerlines:
        for start, end in zip(points[:-1], points[1:]):
            ax, ay = float(start[0]), float(start[1])
            bx, by = float(end[0]), float(end[1])
            dx = bx - ax
            dy = by - ay
            length_sq = dx * dx + dy * dy
            if length_sq <= 1e-9:
                continue
            ratio = max(0.0, min(((px - ax) * dx + (py - ay) * dy) / length_sq, 1.0))
            qx = ax + dx * ratio
            qy = ay + dy * ratio
            distance = math.hypot(px - qx, py - qy)
            if distance < best_distance:
                cross = dx * (py - ay) - dy * (px - ax)
                best_id = str(centerline_id)
                best_side = "left" if cross > 0.0 else "right"
                best_distance = float(distance)
    return best_id, best_side, best_distance


def _pixel_ring_from_polygon(annotation: ReferenceAnnotation, polygon: Any) -> List[Dict[str, float]]:
    coords = list(polygon.exterior.coords)
    if len(coords) > 1 and coords[0] == coords[-1]:
        coords = coords[:-1]
    return [local_to_pixel(annotation, x=x, z=z) for x, z in coords]


def derive_regions_from_annotation(
    annotation_input: ReferenceAnnotation | Mapping[str, Any],
    *,
    options: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    annotation = annotation_input if isinstance(annotation_input, ReferenceAnnotation) else parse_reference_annotation(annotation_input)
    options = dict(options or {})
    min_area_m2 = max(
        0.0,
        float(options.get("min_area_m2", DEFAULT_MIN_DERIVED_REGION_AREA_M2) or DEFAULT_MIN_DERIVED_REGION_AREA_M2),
    )
    warnings: List[str] = []
    scene_regions = [item for item in annotation.regions if item.region_role == "scene_region"]
    if not scene_regions:
        warnings.append("No scene_region found; draw a Scene Region before auto splitting building regions.")
        return {
            "derived_regions": [],
            "building_regions": [],
            "summary": {
                "scene_region_count": 0,
                "derived_region_count": 0,
                "building_region_count": 0,
                "min_area_m2": float(min_area_m2),
                "warnings": warnings,
            },
            "warnings": warnings,
        }

    scene_polygons = [
        (region, polygon_from_region(region, annotation))
        for region in scene_regions
    ]
    scene_polygons = [
        (region, polygon)
        for region, polygon in scene_polygons
        if polygon is not None and not getattr(polygon, "is_empty", True)
    ]
    if not scene_polygons:
        warnings.append("Scene regions did not form a valid polygon.")
        return {
            "derived_regions": [],
            "building_regions": [],
            "summary": {
                "scene_region_count": len(scene_regions),
                "derived_region_count": 0,
                "building_region_count": 0,
                "min_area_m2": float(min_area_m2),
                "warnings": warnings,
            },
            "warnings": warnings,
        }

    source_region, scene_polygon = max(scene_polygons, key=lambda item: float(getattr(item[1], "area", 0.0) or 0.0))
    scene_polygon = _clean_polygon(scene_polygon)
    road_occupancy, road_cut_area_m2 = _build_road_occupancy(annotation, scene_polygon)
    remaining = _clean_polygon(scene_polygon.difference(road_occupancy))
    local_centerlines = _collect_local_centerlines(annotation)
    derived_regions: List[Dict[str, Any]] = []
    building_regions: List[Dict[str, Any]] = []
    sliver_removed_count = 0
    for polygon in sorted(_iter_polygons(remaining), key=lambda item: float(item.area), reverse=True):
        area_m2 = float(getattr(polygon, "area", 0.0) or 0.0)
        if area_m2 < min_area_m2:
            sliver_removed_count += 1
            continue
        representative = polygon.representative_point()
        nearest_centerline_id, side, distance_m = _nearest_centerline_side(
            (float(representative.x), float(representative.y)),
            local_centerlines,
        )
        land_use_type = "commercial" if side == "left" else "residential"
        derived_id = f"derived_building_region_{len(derived_regions) + 1:02d}"
        pixel_points = _pixel_ring_from_polygon(annotation, polygon)
        derived_region = {
            "id": derived_id,
            "label": f"Building Region {len(derived_regions) + 1:02d}",
            "region_role": "building_region",
            "derived": True,
            "source_region_id": source_region.feature_id,
            "points": pixel_points,
            "polygon_xz": [[float(x), float(z)] for x, z in polygon.exterior.coords],
            "area_m2": area_m2,
            "nearest_centerline_id": nearest_centerline_id,
            "nearest_centerline_distance_m": float(distance_m),
            "side": side,
            "land_use_type": land_use_type,
            "material": {"preset": "building_region_auto"},
            "derivation_status": "derived",
        }
        derived_regions.append(derived_region)
        building_regions.append(
            _region_building_record(
                region_id=derived_id,
                label=derived_region["label"],
                order_index=len(building_regions),
                polygon=polygon,
                source="derived_region",
                source_region_id=source_region.feature_id,
                land_use_type=land_use_type,
                side=side,
                nearest_centerline_id=nearest_centerline_id,
            )
        )

    summary = {
        "scene_region_count": len(scene_regions),
        "source_region_id": source_region.feature_id,
        "scene_region_area_m2": float(getattr(scene_polygon, "area", 0.0) or 0.0),
        "road_cut_area_m2": float(road_cut_area_m2),
        "remaining_area_m2": float(getattr(remaining, "area", 0.0) or 0.0),
        "derived_region_count": len(derived_regions),
        "building_region_count": len(building_regions),
        "sliver_removed_count": int(sliver_removed_count),
        "min_area_m2": float(min_area_m2),
        "warnings": warnings,
    }
    return {
        "derived_regions": derived_regions,
        "building_regions": building_regions,
        "summary": summary,
        "warnings": warnings,
    }


__all__ = [
    "DEFAULT_MIN_DERIVED_REGION_AREA_M2",
    "derive_regions_from_annotation",
    "explicit_building_region_records_from_regions",
    "functional_region_records_from_regions",
    "local_to_pixel",
    "pixel_to_local",
    "polygon_from_region",
    "region_to_local_points",
    "scene_region_polygon_from_annotation",
]
