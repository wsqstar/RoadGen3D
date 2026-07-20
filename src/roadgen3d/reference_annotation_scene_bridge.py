"""Bridge reference annotations into the corridor scene/export pipeline."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .osm_ingest import OsmBuilding, OsmRoad, ProjectedFeatures
from .placement_zones import (
    PlacementContext,
    build_placement_context,
    build_sidewalk_zones_from_roads,
    trim_center_planting_strips_for_junctions,
)
from .junction_surface_normalization import normalize_junction_surface_geometries
from .reference_annotation import (
    BezierCurve3,
    JunctionComposition,
    JunctionLaneSurface,
    JunctionMergedSurface,
    ReferenceAnnotation,
    build_reference_annotation_compose_config,
    build_reference_annotation_graph_payload,
    build_segment_graph_from_annotation,
    functional_zone_to_local_coords,
    parse_reference_annotation,
)
from .reference_regions import (
    _bus_bay_uses_taper as _reference_bus_bay_uses_taper,
    _surface_annotation_polygon as _reference_surface_annotation_polygon,
    derive_regions_from_annotation,
    explicit_building_region_records_from_regions,
    functional_region_records_from_regions,
    scene_region_polygon_from_annotation,
)
from .types import RoadSegmentGraph, StreetComposeConfig

ANNOTATION_SCENE_BBOX_PADDING_M = 36.0


@dataclass(frozen=True)
class ReferenceAnnotationSceneBridgeResult:
    annotation: ReferenceAnnotation
    road_segment_graph: RoadSegmentGraph
    projected_features: ProjectedFeatures
    placement_context: PlacementContext
    summary_metadata: Dict[str, Any]


def _collect_local_centerlines(annotation: ReferenceAnnotation) -> List[Tuple[int, Any, List[Tuple[float, float]]]]:
    center_x = float(annotation.image_width_px) * 0.5
    center_y = float(annotation.image_height_px) * 0.5
    ppm = max(float(annotation.pixels_per_meter), 1e-6)
    items: List[Tuple[int, Any, List[Tuple[float, float]]]] = []
    road_id = 1
    for centerline in annotation.centerlines:
        points: List[Tuple[float, float]] = []
        for point in centerline.points:
            xy = (
                (float(point.x) - center_x) / ppm,
                (center_y - float(point.y)) / ppm,
            )
            if not points or points[-1] != xy:
                points.append(xy)
        if len(points) >= 2:
            items.append((road_id, centerline, points))
            road_id += 1
    return items


def _graph_bbox(
    local_centerlines: Sequence[Tuple[int, Any, Sequence[Tuple[float, float]]]],
    *,
    padding_m: float,
) -> Tuple[float, float, float, float]:
    xs: List[float] = []
    ys: List[float] = []
    for _, _, points in local_centerlines:
        for point in points:
            xs.append(float(point[0]))
            ys.append(float(point[1]))
    if not xs or not ys:
        pad = max(float(padding_m), 10.0)
        return (-pad, -pad, pad, pad)
    return (
        float(min(xs) - padding_m),
        float(min(ys) - padding_m),
        float(max(xs) + padding_m),
        float(max(ys) + padding_m),
    )


def _annotation_building_region_records(annotation: ReferenceAnnotation) -> List[Dict[str, Any]]:
    ppm = max(float(annotation.pixels_per_meter), 1e-6)
    regions: List[Dict[str, Any]] = []
    for order_index, region in enumerate(annotation.building_regions):
        center_x, center_y = (
            (float(region.center_x_px) - float(annotation.image_width_px) * 0.5) / ppm,
            (float(annotation.image_height_px) * 0.5 - float(region.center_y_px)) / ppm,
        )
        half_width_m = float(region.width_px) / ppm * 0.5
        half_height_m = float(region.height_px) / ppm * 0.5
        yaw_rad = math.radians(float(region.yaw_deg))
        axis_x = (math.cos(yaw_rad), math.sin(yaw_rad))
        axis_y = (-math.sin(yaw_rad), math.cos(yaw_rad))

        def _offset(local_x: float, local_y: float) -> Tuple[float, float]:
            return (
                float(center_x + axis_x[0] * local_x + axis_y[0] * local_y),
                float(center_y + axis_x[1] * local_x + axis_y[1] * local_y),
            )

        polygon_xz = (
            _offset(-half_width_m, -half_height_m),
            _offset(half_width_m, -half_height_m),
            _offset(half_width_m, half_height_m),
            _offset(-half_width_m, half_height_m),
            _offset(-half_width_m, -half_height_m),
        )
        regions.append(
            {
                "region_id": str(region.feature_id),
                "label": str(region.label),
                "order_index": int(order_index),
                "center_xz": (float(center_x), float(center_y)),
                "width_m": float(half_width_m * 2.0),
                "height_m": float(half_height_m * 2.0),
                "yaw_deg": float(region.yaw_deg),
                "polygon_xz": tuple((float(x), float(z)) for x, z in polygon_xz),
            }
        )
    return regions


def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _point_at_station(points: Sequence[Tuple[float, float]], station_m: float) -> Tuple[float, float]:
    if len(points) < 2:
        return tuple(points[0]) if points else (0.0, 0.0)
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
    return tuple(float(value) for value in points[-1])


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
        if length <= 1e-9:
            tangents.append((1.0, 0.0))
        else:
            tangents.append((dx / length, dy / length))

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
    polygon = Polygon(ring)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    if getattr(polygon, "is_empty", True):
        return None
    return polygon


def _annotation_surface_records(
    annotation: ReferenceAnnotation,
    local_centerlines: Sequence[Tuple[int, Any, Sequence[Tuple[float, float]]]],
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    centerline_points_by_id = {
        str(centerline.feature_id): tuple((float(x), float(y)) for x, y in points)
        for _, centerline, points in local_centerlines
    }
    centerline_by_id = {
        str(centerline.feature_id): centerline
        for _, centerline, _points in local_centerlines
    }
    for order_index, surface in enumerate(annotation.surface_annotations):
        centerline_points = centerline_points_by_id.get(str(surface.centerline_id))
        if not centerline_points:
            continue
        centerline = centerline_by_id.get(str(surface.centerline_id))
        road_half_width_m = (
            float(centerline.carriageway_width_m()) * 0.5
            if centerline is not None and hasattr(centerline, "carriageway_width_m")
            else None
        )
        geometry = _reference_surface_annotation_polygon(
            centerline_points,
            station_start_m=float(surface.station_start_m),
            station_end_m=float(surface.station_end_m),
            lateral_start_m=float(surface.lateral_start_m),
            lateral_end_m=float(surface.lateral_end_m),
            surface_kind=str(surface.kind),
            road_half_width_m=road_half_width_m,
        )
        if geometry is None or getattr(geometry, "is_empty", True):
            continue
        uses_taper = _reference_bus_bay_uses_taper(
            surface_kind=str(surface.kind),
            road_half_width_m=road_half_width_m,
            lateral_start_m=float(surface.lateral_start_m),
            lateral_end_m=float(surface.lateral_end_m),
        )
        length_m = max(float(surface.station_end_m) - float(surface.station_start_m), 0.0)
        records.append(
            {
                "surface_id": str(surface.feature_id),
                "annotation_id": str(surface.feature_id),
                "label": str(surface.label),
                "kind": str(surface.kind),
                "surface_kind": str(surface.kind),
                "surface_role": str(surface.surface_role),
                "centerline_id": str(surface.centerline_id),
                "station_start_m": float(surface.station_start_m),
                "station_end_m": float(surface.station_end_m),
                "lateral_start_m": float(surface.lateral_start_m),
                "lateral_end_m": float(surface.lateral_end_m),
                "material": surface.material.to_dict(),
                "skeleton_design_profile": str(surface.skeleton_design_profile),
                "skeleton_design_profile_source": str(surface.skeleton_design_profile_source),
                "skeleton_design_profile_confidence": float(surface.skeleton_design_profile_confidence),
                "skeleton_design_profile_reasons": list(surface.skeleton_design_profile_reasons),
                "geometry": geometry,
                "area_m2": float(getattr(geometry, "area", 0.0) or 0.0),
                "order_index": int(order_index),
                **(
                    {
                        "derived_shape": "tapered_bus_bay_v1",
                        "taper_length_m": float(min(8.0, length_m * 0.25)),
                        "road_half_width_m": float(road_half_width_m or 0.0),
                        "sidewalk_intrusion_m": float(
                            max(abs(float(surface.lateral_start_m)), abs(float(surface.lateral_end_m)))
                            - float(road_half_width_m or 0.0)
                        ),
                    }
                    if uses_taper
                    else {}
                ),
            }
        )
    return records


def _parametric_bus_stop_surface_records(
    *,
    config: StreetComposeConfig,
    local_centerlines: Sequence[Tuple[int, Any, Sequence[Tuple[float, float]]]],
    existing_records: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Create one deterministic, geometry-owned bus bay for v2 parameters.

    The bay and its waiting pad enter the same semantic surface partition as
    authored reference surfaces.  Curbside stops need no extra road geometry
    and are handled by the explicit transit-edge placement band.
    """

    if not bool(getattr(config, "bus_stop_enabled", False)):
        return []
    if str(getattr(config, "bus_stop_placement", "curbside") or "curbside").strip().lower() != "bay":
        return []
    if any(
        str(record.get("surface_role", "") or "").strip().lower() in {"bus_lane", "transit_pad"}
        for record in existing_records
    ):
        return []

    candidates: List[Tuple[float, int, Any, Sequence[Tuple[float, float]]]] = []
    for road_id, centerline, points in local_centerlines:
        length_m = sum(
            math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))
            for a, b in zip(points, points[1:])
        )
        if length_m >= 18.0:
            candidates.append((float(length_m), int(road_id), centerline, points))
    if not candidates:
        return []
    length_m, road_id, centerline, points = max(candidates, key=lambda item: (item[0], -item[1]))
    bay_length_m = min(30.0, max(18.0, length_m * 0.28))
    station_start_m = max(1.0, (length_m - bay_length_m) * 0.5)
    station_end_m = min(length_m - 1.0, station_start_m + bay_length_m)
    road_half_width_m = max(float(centerline.carriageway_width_m()) * 0.5, 1.0)
    bay_depth_m = max(2.5, float(getattr(config, "furnishing_width_m", 0.0) or 0.0))
    pad_depth_m = max(1.8, float(getattr(config, "sidewalk_width_m", 1.8) or 1.8))
    bay_inner = -road_half_width_m
    bay_outer = -(road_half_width_m + bay_depth_m)
    pad_inner = bay_outer
    pad_outer = bay_outer - pad_depth_m
    bay_geometry = _reference_surface_annotation_polygon(
        points,
        station_start_m=station_start_m,
        station_end_m=station_end_m,
        lateral_start_m=bay_inner,
        lateral_end_m=bay_outer,
        surface_kind="bus_lane_widening",
        road_half_width_m=road_half_width_m,
    )
    pad_geometry = _reference_surface_annotation_polygon(
        points,
        station_start_m=station_start_m,
        station_end_m=station_end_m,
        lateral_start_m=pad_inner,
        lateral_end_m=pad_outer,
        surface_kind="transit_pad",
        road_half_width_m=road_half_width_m,
    )
    if any(geometry is None or getattr(geometry, "is_empty", True) for geometry in (bay_geometry, pad_geometry)):
        return []

    base = {
        "centerline_id": str(centerline.feature_id),
        "station_start_m": float(station_start_m),
        "station_end_m": float(station_end_m),
        "skeleton_design_profile": "",
        "skeleton_design_profile_source": "manual",
        "skeleton_design_profile_confidence": 1.0,
        "skeleton_design_profile_reasons": ["street_design_parameters_v2"],
        "derived_shape": "tapered_bus_bay_v1",
        "taper_length_m": float(min(8.0, bay_length_m * 0.25)),
        "road_half_width_m": float(road_half_width_m),
        "generated_from_parameters": True,
        "road_id": int(road_id),
    }
    return [
        {
            **base,
            "surface_id": "parametric_bus_bay_0",
            "annotation_id": "parametric_bus_bay_0",
            "label": "Parametric bus bay",
            "kind": "bus_lane_widening",
            "surface_kind": "bus_lane_widening",
            "surface_role": "bus_lane",
            "lateral_start_m": float(bay_inner),
            "lateral_end_m": float(bay_outer),
            "material": {},
            "geometry": bay_geometry,
            "area_m2": float(bay_geometry.area),
            "order_index": int(len(existing_records)),
        },
        {
            **base,
            "surface_id": "parametric_transit_pad_0",
            "annotation_id": "parametric_transit_pad_0",
            "label": "Parametric transit pad",
            "kind": "transit_pad",
            "surface_kind": "transit_pad",
            "surface_role": "transit_pad",
            "lateral_start_m": float(pad_inner),
            "lateral_end_m": float(pad_outer),
            "material": {},
            "geometry": pad_geometry,
            "area_m2": float(pad_geometry.area),
            "order_index": int(len(existing_records) + 1),
        },
    ]


def _apply_parametric_median_zone(
    *,
    config: StreetComposeConfig,
    placement_context: PlacementContext,
    local_centerlines: Sequence[Tuple[int, Any, Sequence[Tuple[float, float]]]],
) -> Dict[str, Any]:
    """Materialize a v2 center median into the existing 3D strip renderer."""

    if not bool(getattr(config, "median_enabled", False)):
        return {"enabled": False, "area_m2": 0.0}
    width_m = max(float(getattr(config, "median_width_m", 0.0) or 0.0), 0.0)
    if width_m <= 0.0:
        return {"enabled": False, "area_m2": 0.0}
    try:
        from shapely.geometry import LineString
        from shapely.ops import unary_union
    except ImportError:
        return {"enabled": False, "area_m2": 0.0, "warning": "shapely_unavailable"}
    polygons = []
    for _road_id, _centerline, points in local_centerlines:
        if len(points) < 2:
            continue
        polygon = LineString(points).buffer(width_m * 0.5, cap_style="flat", join_style="round")
        if not getattr(polygon, "is_empty", True):
            polygons.append(polygon)
    if not polygons:
        return {"enabled": False, "area_m2": 0.0, "warning": "no_centerline_geometry"}
    zone = unary_union(polygons)
    carriageway = getattr(placement_context, "carriageway", None)
    if carriageway is not None and not getattr(carriageway, "is_empty", True):
        zone = zone.intersection(carriageway)
    junction_cores = [
        item.get("carriageway_core") or item.get("junction_core_rect")
        for item in list(getattr(placement_context, "junction_geometries", []) or [])
        if isinstance(item, Mapping)
    ]
    junction_cores = [item for item in junction_cores if item is not None and not getattr(item, "is_empty", True)]
    if junction_cores:
        zone = zone.difference(unary_union(junction_cores))
    if not getattr(zone, "is_valid", True):
        zone = zone.buffer(0)
    if getattr(zone, "is_empty", True):
        return {"enabled": False, "area_m2": 0.0, "warning": "median_clipped_empty"}
    strip_zones = dict(getattr(placement_context, "strip_zones", {}) or {})
    kind = "center_median_green" if str(getattr(config, "median_kind", "raised")) == "planted" else "center_median"
    strip_zones[kind] = zone
    placement_context.strip_zones = strip_zones
    return {
        "enabled": True,
        "kind": str(getattr(config, "median_kind", "raised")),
        "width_m": float(width_m),
        "surface_key": kind,
        "area_m2": float(zone.area),
    }


def _aligned_building_records(
    values: Sequence[Mapping[str, Any]] | None,
    source_alignment: Mapping[str, Any] | None,
) -> Tuple[List[OsmBuilding], Dict[str, Any]]:
    requested = len(values or ())
    alignment = dict(source_alignment or {})
    if requested and str(alignment.get("status", "")).lower() != "aligned":
        return [], {
            "requested": requested,
            "accepted": 0,
            "skipped": requested,
            "status": "n/a",
            "reason": str(alignment.get("reason") or "missing_alignment"),
        }
    buildings: List[OsmBuilding] = []
    skipped = 0
    for index, value in enumerate(values or ()):
        if not isinstance(value, Mapping):
            skipped += 1
            continue
        raw_ring = value.get("polygon_xz")
        if not isinstance(raw_ring, Sequence) or isinstance(raw_ring, (str, bytes)):
            skipped += 1
            continue
        ring: List[Tuple[float, float]] = []
        try:
            for point in raw_ring:
                if not isinstance(point, Sequence) or isinstance(point, (str, bytes)) or len(point) < 2:
                    raise ValueError
                x, z = float(point[0]), float(point[1])
                if not math.isfinite(x) or not math.isfinite(z):
                    raise ValueError
                ring.append((x, z))
        except (TypeError, ValueError):
            skipped += 1
            continue
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])
        if len(ring) < 4 or len(set(ring[:-1])) < 3:
            skipped += 1
            continue
        raw_id = value.get("osm_id") or value.get("source_id") or f"aligned-building-{index + 1}"
        try:
            osm_id = int(raw_id)
        except (TypeError, ValueError):
            osm_id = int(hashlib.sha256(str(raw_id).encode("utf-8")).hexdigest()[:15], 16)
        buildings.append(
            OsmBuilding(
                osm_id=osm_id,
                coords=ring,
                tags={
                    **{str(key): str(item) for key, item in dict(value.get("tags") or {}).items()},
                    "roadgen3d_context_massing": "white",
                    "roadgen3d_editable": "false",
                },
            )
        )
    accepted = len(buildings)
    return buildings, {
        "requested": requested,
        "accepted": accepted,
        "skipped": skipped,
        "status": "aligned" if accepted or not requested else "aligned_empty",
        "reason": "" if accepted or not requested else "all_buildings_invalid",
    }


def build_reference_annotation_scene_bridge(
    annotation_input: ReferenceAnnotation | Mapping[str, Any],
    *,
    compose_config: StreetComposeConfig | Mapping[str, Any] | None = None,
    aligned_buildings: Sequence[Mapping[str, Any]] | None = None,
    source_alignment: Mapping[str, Any] | None = None,
) -> ReferenceAnnotationSceneBridgeResult:
    annotation = (
        annotation_input
        if isinstance(annotation_input, ReferenceAnnotation)
        else parse_reference_annotation(annotation_input)
    )
    resolved_config = (
        compose_config
        if isinstance(compose_config, StreetComposeConfig)
        else build_reference_annotation_compose_config(compose_config or {})
    )
    road_segment_graph = build_segment_graph_from_annotation(annotation, config=resolved_config)
    local_centerlines = _collect_local_centerlines(annotation)
    scene_region_polygon = scene_region_polygon_from_annotation(annotation)
    synthetic_roads: List[OsmRoad] = []
    for road_id, centerline, points in local_centerlines:
        synthetic_roads.append(
            OsmRoad(
                osm_id=int(road_id),
                highway_type=str(getattr(centerline, "highway_type", "") or "annotated_centerline"),
                coords=list(points),
                width_m=float(centerline.carriageway_width_m()),
            )
        )
    if scene_region_polygon is not None and not getattr(scene_region_polygon, "is_empty", True):
        min_x, min_y, max_x, max_y = scene_region_polygon.bounds
        bbox_m = (float(min_x), float(min_y), float(max_x), float(max_y))
    else:
        bbox_m = _graph_bbox(local_centerlines, padding_m=float(ANNOTATION_SCENE_BBOX_PADDING_M))
    source_buildings, source_building_summary = _aligned_building_records(
        aligned_buildings,
        source_alignment,
    )
    projected_features = ProjectedFeatures(
        roads=synthetic_roads,
        buildings=source_buildings,
        entrances=[],
        bus_stops=[],
        fire_points=[],
        poi_points_by_type={},
        bbox_m=bbox_m,
        origin_utm=(0.0, 0.0),
        utm_epsg=0,
    )
    placement_context = build_placement_context(
        projected_features,
        resolved_config,
        road_segment_graph=road_segment_graph,
        aoi_polygon=scene_region_polygon,
    )
    placement_context.junction_geometries = _apply_manual_junction_compositions(
        annotation,
        list(getattr(placement_context, "junction_geometries", []) or []),
        road_segment_graph=road_segment_graph,
        roads=synthetic_roads,
        compose_config=resolved_config,
    )
    _refresh_road_surfaces_for_junction_geometries(
        placement_context,
        synthetic_roads,
        list(getattr(placement_context, "junction_geometries", []) or []),
    )
    placement_context.junction_geometries = normalize_junction_surface_geometries(
        list(getattr(placement_context, "junction_geometries", []) or [])
    )
    trim_center_planting_strips_for_junctions(
        placement_context,
        list(getattr(placement_context, "junction_geometries", []) or []),
    )
    parametric_median_summary = _apply_parametric_median_zone(
        config=resolved_config,
        placement_context=placement_context,
        local_centerlines=local_centerlines,
    )
    derived_region_payload = derive_regions_from_annotation(annotation)
    explicit_region_building_records = explicit_building_region_records_from_regions(annotation)
    if explicit_region_building_records:
        placement_context.building_regions = explicit_region_building_records
    elif annotation.building_regions:
        placement_context.building_regions = _annotation_building_region_records(annotation)
    else:
        placement_context.building_regions = list(derived_region_payload.get("building_regions", []) or [])
    placement_context.regions = [region.to_dict() for region in annotation.regions]
    placement_context.derived_regions = list(derived_region_payload.get("derived_regions", []) or [])
    placement_context.region_derivation_summary = dict(derived_region_payload.get("summary", {}) or {})
    placement_context.surface_annotations = _annotation_surface_records(annotation, local_centerlines)
    placement_context.surface_annotations.extend(
        _parametric_bus_stop_surface_records(
            config=resolved_config,
            local_centerlines=local_centerlines,
            existing_records=placement_context.surface_annotations,
        )
    )
    center_x = float(annotation.image_width_px) * 0.5
    center_y = float(annotation.image_height_px) * 0.5
    ppm = max(float(annotation.pixels_per_meter), 1e-6)
    placement_context.functional_zones = [
        {
            "id": zone.feature_id,
            "label": zone.label,
            "kind": zone.kind,
            "points": functional_zone_to_local_coords(zone, annotation),
            "furniture_instances": [
                {
                    "instance_id": inst.instance_id,
                    "kind": inst.kind,
                    "x": (inst.x_px - center_x) / ppm,
                    "y": (center_y - inst.y_px) / ppm,
                    "yaw_deg": inst.yaw_deg,
                }
                for inst in zone.furniture_instances
            ],
            "skeleton_design_profile": str(zone.skeleton_design_profile),
            "skeleton_design_profile_source": str(zone.skeleton_design_profile_source),
            "skeleton_design_profile_confidence": float(zone.skeleton_design_profile_confidence),
            "skeleton_design_profile_reasons": list(zone.skeleton_design_profile_reasons),
        }
        for zone in annotation.functional_zones
    ] + functional_region_records_from_regions(annotation)
    payload = build_reference_annotation_graph_payload(annotation, config=resolved_config)
    summary_metadata = {
        **dict(payload.get("summary", {}) or {}),
        "layout_mode": "annotation",
        "generator": "reference_annotation_bridge_v1",
        "synthetic_road_count": int(len(projected_features.roads)),
        "junction_geometry_count": int(len(getattr(placement_context, "junction_geometries", []) or [])),
        "surface_annotation_count": int(len(getattr(placement_context, "surface_annotations", []) or [])),
        "parametric_median": parametric_median_summary,
        "region_count": int(len(getattr(placement_context, "regions", []) or [])),
        "derived_region_count": int(len(getattr(placement_context, "derived_regions", []) or [])),
        "derived_building_region_count": int(len(derived_region_payload.get("building_regions", []) or [])),
        "region_derivation_summary": dict(derived_region_payload.get("summary", {}) or {}),
        "osm_context_massing": source_building_summary,
        "source_alignment": dict(source_alignment or {}),
    }
    return ReferenceAnnotationSceneBridgeResult(
        annotation=annotation,
        road_segment_graph=road_segment_graph,
        projected_features=projected_features,
        placement_context=placement_context,
        summary_metadata=summary_metadata,
    )


def _refresh_road_surfaces_for_junction_geometries(
    placement_context: PlacementContext,
    roads: Sequence[OsmRoad],
    junction_geometries: Sequence[Mapping[str, Any]],
) -> None:
    """Re-trim road arm surfaces after derived junction geometry is replaced.

    ``build_placement_context`` initially trims roads against its own junction
    geometry. The reference bridge may then replace cross junctions with the
    RoadPen-style fusion geometry, so the rendered road arms must be clipped
    again against the final carriageway core. Otherwise 3D still shows the old
    diagonal cuts from the pre-fusion geometry.
    """

    if not junction_geometries:
        return
    try:
        from shapely.geometry import LineString, MultiPolygon
        from shapely.ops import unary_union
    except ImportError:
        return

    def clean(geometry: Any) -> Any:
        if geometry is None or getattr(geometry, "is_empty", True):
            return MultiPolygon()
        if not getattr(geometry, "is_valid", True):
            try:
                geometry = geometry.buffer(0)
            except Exception:
                return MultiPolygon()
        return geometry

    aoi_polygon = getattr(placement_context, "aoi_polygon", None)
    road_polygons: List[Any] = []
    for road in roads:
        coords = list(getattr(road, "coords", ()) or ())
        if len(coords) < 2:
            continue
        line = LineString(coords)
        if getattr(line, "is_empty", True):
            continue
        half_width_m = max(float(getattr(road, "width_m", 0.0) or 0.0) * 0.5, 0.5)
        polygon = clean(line.buffer(half_width_m, cap_style="flat"))
        if aoi_polygon is not None and not getattr(aoi_polygon, "is_empty", True):
            polygon = clean(polygon.intersection(aoi_polygon))
        if not getattr(polygon, "is_empty", True):
            road_polygons.append(polygon)

    if not road_polygons:
        return

    trim_sources = [
        junction.get("carriageway_core") or junction.get("junction_core_rect")
        for junction in junction_geometries
    ]
    trim_sources = [clean(item) for item in trim_sources if item is not None]
    trim_geometry = clean(unary_union(trim_sources)) if trim_sources else MultiPolygon()

    trimmed_road_polygons: List[Any] = []
    for polygon in road_polygons:
        trimmed = polygon
        if not getattr(trim_geometry, "is_empty", True):
            trimmed = clean(trimmed.difference(trim_geometry))
        if aoi_polygon is not None and not getattr(aoi_polygon, "is_empty", True):
            trimmed = clean(trimmed.intersection(aoi_polygon))
        if not getattr(trimmed, "is_empty", True):
            trimmed_road_polygons.append(trimmed)

    carriageway = clean(unary_union(trimmed_road_polygons)) if trimmed_road_polygons else MultiPolygon()
    placement_context.carriageway = carriageway
    placement_context.carriageway_polygon = carriageway
    placement_context.road_arm_geometries = trimmed_road_polygons

    carriageway_width_m = max(float(getattr(placement_context, "carriageway_width_m", 0.0) or 0.0), 1.0)
    left_sidewalk_width_m = max(
        float(getattr(placement_context, "required_left_width_m", 0.0) or 0.0),
        float(getattr(placement_context, "left_clear_path_width_m", 0.0) or 0.0)
        + float(getattr(placement_context, "left_furnishing_width_m", 0.0) or 0.0),
    )
    right_sidewalk_width_m = max(
        float(getattr(placement_context, "required_right_width_m", 0.0) or 0.0),
        float(getattr(placement_context, "right_clear_path_width_m", 0.0) or 0.0)
        + float(getattr(placement_context, "right_furnishing_width_m", 0.0) or 0.0),
    )
    if aoi_polygon is not None and not getattr(aoi_polygon, "is_empty", True):
        # Rebuild each straight approach from its own real cross-section.  A
        # global half-width shifts the sidewalk's inner edge on narrower arms;
        # a global 3.4 m sidewalk also truncates a 6 m furnishing/sidewalk/
        # frontage profile before it reaches the junction curve.  Both errors
        # appear in the final GLB as a triangular background-ground patch.
        side_widths_by_road: Dict[int, Dict[str, float]] = {}
        for junction in junction_geometries:
            for profile in junction.get("junction_arm_profiles", []) or ():
                road_id = int(profile.get("road_id", 0) or 0)
                if road_id <= 0:
                    continue
                resolved = side_widths_by_road.setdefault(
                    road_id,
                    {"left": left_sidewalk_width_m, "right": right_sidewalk_width_m},
                )
                half_width = max(
                    float(profile.get("carriageway_width_m", 0.0) or 0.0) * 0.5,
                    0.5,
                )
                for strip in profile.get("side_strips", []) or ():
                    zone = str(strip.get("zone", "") or "").strip().lower()
                    if zone not in {"left", "right"}:
                        continue
                    outer_extent = max(
                        abs(float(strip.get("inner_offset_m", 0.0) or 0.0)),
                        abs(float(strip.get("outer_offset_m", 0.0) or 0.0)),
                    )
                    resolved[zone] = max(float(resolved[zone]), outer_extent - half_width)

        left_polygons: List[Any] = []
        right_polygons: List[Any] = []
        road_side_profile_debug: List[Dict[str, Any]] = []
        for road in roads:
            coords = list(getattr(road, "coords", ()) or ())
            if len(coords) < 2:
                continue
            road_id = int(getattr(road, "osm_id", 0) or 0)
            half_width = max(float(getattr(road, "width_m", 0.0) or 0.0) * 0.5, 0.5)
            side_widths = side_widths_by_road.get(
                road_id,
                {"left": left_sidewalk_width_m, "right": right_sidewalk_width_m},
            )
            line = LineString(coords)
            for zone, direction in (("left", 1.0), ("right", -1.0)):
                side_width = max(float(side_widths.get(zone, 0.0) or 0.0), 0.0)
                if side_width <= 0.0:
                    continue
                outer = line.buffer(
                    direction * (half_width + side_width),
                    cap_style="flat",
                    single_sided=True,
                )
                inner = line.buffer(
                    direction * half_width,
                    cap_style="flat",
                    single_sided=True,
                )
                band = clean(outer.difference(inner).intersection(aoi_polygon))
                if not getattr(band, "is_empty", True):
                    (left_polygons if zone == "left" else right_polygons).append(band)
            road_side_profile_debug.append({
                "road_id": road_id,
                "carriageway_width_m": half_width * 2.0,
                "left_width_m": float(side_widths.get("left", 0.0) or 0.0),
                "right_width_m": float(side_widths.get("right", 0.0) or 0.0),
            })

        left_sidewalk = clean(unary_union(left_polygons)) if left_polygons else MultiPolygon()
        right_sidewalk = clean(unary_union(right_polygons)) if right_polygons else MultiPolygon()
        sidewalk = clean(unary_union([left_sidewalk, right_sidewalk]))
        sidewalk_trim_sources = [
            junction.get("sidewalk_trim_zone")
            for junction in junction_geometries
            if junction.get("sidewalk_trim_zone") is not None
        ]
        sidewalk_trim = clean(unary_union([clean(item) for item in sidewalk_trim_sources])) if sidewalk_trim_sources else MultiPolygon()
        if not getattr(sidewalk_trim, "is_empty", True):
            left_sidewalk = clean(left_sidewalk.difference(sidewalk_trim))
            right_sidewalk = clean(right_sidewalk.difference(sidewalk_trim))
            sidewalk = clean(sidewalk.difference(sidewalk_trim))
        placement_context.left_sidewalk_zone = left_sidewalk
        placement_context.right_sidewalk_zone = right_sidewalk
        placement_context.sidewalk_zone = sidewalk
        placement_context.road_side_profile_debug = road_side_profile_debug


def _sample_bezier_points(curve: BezierCurve3, steps: int = 16) -> Sequence[Tuple[float, float]]:
    points: List[Tuple[float, float]] = []
    n = max(2, steps)
    for i in range(n + 1):
        t = i / n
        u = 1.0 - t
        u2 = u * u
        u3 = u2 * u
        t2 = t * t
        t3 = t2 * t
        x = u3 * curve.start.x + 3 * u2 * t * curve.control1.x + 3 * u * t2 * curve.control2.x + t3 * curve.end.x
        y = u3 * curve.start.y + 3 * u2 * t * curve.control1.y + 3 * u * t2 * curve.control2.y + t3 * curve.end.y
        points.append((float(x), float(y)))
    return points


def _surface_boundary_points(surface: JunctionLaneSurface | JunctionMergedSurface, steps: int = 12) -> List[Tuple[float, float]]:
    sampled_points: List[Tuple[float, float]] = []
    for edge in getattr(surface, "edges", ()) or ():
        edge_points = _sample_bezier_points(edge.curve, steps=steps)
        if not sampled_points:
            sampled_points.extend(edge_points)
        else:
            sampled_points.extend(edge_points[1:])
    if not sampled_points:
        for node in getattr(surface, "nodes", ()) or ():
            sampled_points.append((float(node.point.x), float(node.point.y)))
    if sampled_points and sampled_points[0] != sampled_points[-1]:
        sampled_points.append(sampled_points[0])
    return sampled_points


def _surface_polygon_record(surface: JunctionLaneSurface | JunctionMergedSurface) -> Dict[str, Any] | None:
    from shapely.geometry import Polygon

    points = _surface_boundary_points(surface, steps=10)
    if len(points) < 4:
        return None
    polygon = Polygon(points)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    if getattr(polygon, "is_empty", True):
        return None
    record: Dict[str, Any] = {
        "surface_id": str(surface.surface_id),
        "provenance": str(getattr(surface, "provenance", "generated") or "generated"),
        "geometry": polygon,
        "node_count": len(getattr(surface, "nodes", ()) or ()),
        "edge_count": len(getattr(surface, "edges", ()) or ()),
    }
    if isinstance(surface, JunctionLaneSurface):
        record.update(
            {
                "surface_kind": "lane",
                "lane_id": str(surface.lane_id),
                "arm_key": str(surface.arm_key),
                "flow": str(surface.flow),
                "lane_index": int(surface.lane_index),
                "skeleton_id": str(surface.skeleton_id),
            }
        )
    else:
        record.update(
            {
                "surface_kind": "merged",
                "merged_from_surface_ids": list(surface.merged_from_surface_ids),
                "merged_from_lane_ids": list(surface.merged_from_lane_ids),
            }
        )
    return record


def _build_cross_fusion_junction_geometry(
    junction: Any,
    arms: List[Dict[str, Any]],
    *,
    crosswalk_depth_m: float = 3.0,
) -> Dict[str, Any]:
    """Build junction geometry using cross strip fusion for cross_junction.

    This uses the new angle-bisector approach where:
    - Vehicle lanes go straight through as carriageway core
    - Non-vehicle strips (nearroad_furnishing, clear_sidewalk, frontage_reserve)
      bend along angle bisectors and are merged into continuous surfaces
    """
    from roadgen3d.cross_strip_fusion import build_cross_strip_fusion, cross_strip_fusion_to_junction_geometry

    try:
        fusion_result = build_cross_strip_fusion(
            junction_id=str(junction.feature_id),
            anchor_xy=(float(junction.anchor_x), float(junction.anchor_y)),
            arms=arms,
            crosswalk_depth_m=crosswalk_depth_m,
        )
        geometry = cross_strip_fusion_to_junction_geometry(fusion_result)
        # Mark as auto-generated
        geometry["generation_mode"] = "cross_strip_fusion_auto"
        return geometry
    except Exception:
        # Fallback to None, will be handled by existing logic
        return None


def _build_manual_junction_geometry(
    composition: JunctionComposition,
) -> Dict[str, Any]:
    from shapely.geometry import Polygon

    sidewalk_corner_patches: List[Dict[str, Any]] = []
    nearroad_corner_patches: List[Dict[str, Any]] = []
    frontage_corner_patches: List[Dict[str, Any]] = []
    lane_surface_patches: List[Dict[str, Any]] = []
    merged_surface_patches: List[Dict[str, Any]] = []
    sidewalk_corner_polylines: List[Dict[str, Any]] = []
    nearroad_corner_polylines: List[Dict[str, Any]] = []
    frontage_corner_polylines: List[Dict[str, Any]] = []
    quadrant_corner_kernels: List[Dict[str, Any]] = []

    strip_to_patch_bucket: Dict[str, List[List[Dict[str, Any]]]] = {
        "clear_sidewalk": sidewalk_corner_patches,
        "nearroad_furnishing": nearroad_corner_patches,
        "frontage_reserve": frontage_corner_patches,
    }
    strip_to_polyline_bucket: Dict[str, List[List[Dict[str, Any]]]] = {
        "clear_sidewalk": sidewalk_corner_polylines,
        "nearroad_furnishing": nearroad_corner_polylines,
        "frontage_reserve": frontage_corner_polylines,
    }

    for lane_surface in composition.lane_surfaces:
        record = _surface_polygon_record(lane_surface)
        if record is not None:
            lane_surface_patches.append(record)

    for merged_surface in composition.merged_surfaces:
        record = _surface_polygon_record(merged_surface)
        if record is not None:
            merged_surface_patches.append(record)

    for quadrant in composition.quadrants:
        # Build patches from bezier curves
        for patch in quadrant.patches:
            inner_pts = _sample_bezier_points(patch.inner_curve, steps=12)
            outer_pts = _sample_bezier_points(patch.outer_curve, steps=12)
            # Closed ring: inner from start->end, then outer from end->start
            ring = list(inner_pts) + list(reversed(outer_pts))
            if len(ring) >= 3:
                poly = Polygon(ring)
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if not getattr(poly, "is_empty", True):
                    bucket = strip_to_patch_bucket.get(patch.strip_kind)
                    if bucket is not None:
                        bucket.append({"patch_id": patch.patch_id, "geometry": poly})

        # Build polylines from skeleton lines
        for skeleton in quadrant.skeleton_lines:
            pts = _sample_bezier_points(skeleton.curve, steps=12)
            bucket = strip_to_polyline_bucket.get(skeleton.strip_kind)
            if bucket is not None:
                bucket.append({
                    "polyline_id": skeleton.line_id,
                    "quadrant_id": quadrant.quadrant_id,
                    "kernel_id": f"{quadrant.quadrant_id}_kernel",
                    "points_xy": [[float(x), float(y)] for x, y in pts],
                    "width_m": round(float(skeleton.width_m), 3),
                })

        # Build a simplified quadrant_corner_kernel from the first patch (sidewalk preferred)
        canonical_patch = next(
            (p for p in quadrant.patches if p.strip_kind == "clear_sidewalk"),
            quadrant.patches[0] if quadrant.patches else None,
        )
        if canonical_patch is not None:
            inner_pts = _sample_bezier_points(canonical_patch.inner_curve, steps=8)
            quadrant_corner_kernels.append({
                "kernel_id": f"{quadrant.quadrant_id}_kernel",
                "quadrant_id": quadrant.quadrant_id,
                "road_a_id": 0,
                "road_b_id": 0,
                "centerline_a_id": quadrant.arm_a_id,
                "centerline_b_id": quadrant.arm_b_id,
                "kernel_kind": "polyline_fallback",
                "center_xy": [0.0, 0.0],
                "radius_m": 0.0,
                "start_xy": [float(canonical_patch.inner_curve.start.x), float(canonical_patch.inner_curve.start.y)],
                "end_xy": [float(canonical_patch.inner_curve.end.x), float(canonical_patch.inner_curve.end.y)],
                "start_heading_deg": 0.0,
                "end_heading_deg": 0.0,
                "clockwise": None,
                "sampled_points_xy": [[float(x), float(y)] for x, y in inner_pts],
            })

    geometry: Dict[str, Any] = {
        "junction_id": composition.junction_id,
        "kind": composition.kind,
        "quadrant_corner_kernels": quadrant_corner_kernels,
        "sidewalk_corner_polylines": sidewalk_corner_polylines,
        "nearroad_corner_polylines": nearroad_corner_polylines,
        "frontage_corner_polylines": frontage_corner_polylines,
        "sidewalk_corner_patches": sidewalk_corner_patches,
        "nearroad_corner_patches": nearroad_corner_patches,
        "frontage_corner_patches": frontage_corner_patches,
        "lane_surface_patches": lane_surface_patches,
        "merged_surface_patches": merged_surface_patches,
    }
    return geometry


def _try_build_cross_fusion_for_junction(
    annotation: ReferenceAnnotation,
    junction_geom: Dict[str, Any],
    road_segment_graph: Any | None,
    roads: Sequence[OsmRoad] = (),
    compose_config: StreetComposeConfig | None = None,
) -> Dict[str, Any] | None:
    """Try to build cross fusion geometry for a junction.

    Returns None if fusion fails, in which case the caller should use the original geometry.
    """
    from roadgen3d.cross_strip_fusion import build_cross_strip_fusion, cross_strip_fusion_to_junction_geometry

    junction_id = str(junction_geom.get("junction_id", "") or "")
    anchor_xy = junction_geom.get("anchor_xy", [0.0, 0.0])
    if len(anchor_xy) < 2:
        return None

    # Try to get arms data from road_segment_graph
    arms = _extract_junction_arms_from_graph(
        road_segment_graph,
        junction_id,
        anchor_xy,
        roads=roads,
        approach_boundaries=junction_geom.get("approach_boundaries", ()) or (),
    )
    if arms is None or len(arms) < 3:
        return None

    # The placement pass has already solved the exact arm cut stations. Keep
    # them when rebuilding the role-aware corner partition; recomputing a
    # shorter fillet station here was the source of the rectangular gaps that
    # later normalization mislabeled as carriageway.
    skeletons_by_road_id = {
        int(item.get("road_id", 0) or 0): item
        for item in junction_geom.get("arm_skeletons", []) or ()
        if int(item.get("road_id", 0) or 0) > 0
    }
    for arm in arms:
        if float(arm.get("split_distance_m", 0.0) or 0.0) > 0.0:
            continue
        skeleton = skeletons_by_road_id.get(int(arm.get("road_id", 0) or 0))
        if not skeleton:
            continue
        arm["split_distance_m"] = float(
            skeleton.get("split_distance_m", skeleton.get("core_exit_distance_m", 0.0))
            or 0.0
        )
        arm["core_exit_distance_m"] = float(
            skeleton.get("core_exit_distance_m", 0.0) or 0.0
        )

    try:
        fusion_result = build_cross_strip_fusion(
            junction_id=junction_id,
            anchor_xy=(float(anchor_xy[0]), float(anchor_xy[1])),
            arms=arms,
            crosswalk_depth_m=3.0,
            corner_radius_mode=str(getattr(compose_config, "junction_corner_radius_mode", "auto") or "auto"),
            fixed_corner_radius_m=getattr(compose_config, "junction_corner_radius_m", None),
            min_corner_radius_m=float(getattr(compose_config, "junction_corner_min_radius_m", 3.0) or 3.0),
            max_corner_radius_m=float(getattr(compose_config, "junction_corner_max_radius_m", 8.0) or 8.0),
            precision_grid_m=float(getattr(compose_config, "junction_precision_grid_m", 0.001) or 0.001),
            seam_extension_m=float(getattr(compose_config, "junction_seam_extension_m", 0.02) or 0.02),
            max_curve_angle_deg=float(getattr(compose_config, "junction_curve_max_angle_deg", 2.0) or 2.0),
            max_curve_chord_m=float(getattr(compose_config, "junction_curve_max_chord_m", 0.25) or 0.25),
        )
        geometry = cross_strip_fusion_to_junction_geometry(fusion_result)
        # Preserve the original junction_id and kind
        geometry["junction_id"] = junction_id
        geometry["kind"] = "cross_junction" if len(arms) == 4 else "t_junction"
        geometry["generation_mode"] = "continuous_junction_fusion_auto"
        # Preserve arm count from original
        geometry["arm_count"] = len(arms)
        return geometry
    except Exception:
        return None


def _extract_junction_arms_from_graph(
    road_segment_graph: Any | None,
    junction_id: str,
    anchor_xy: List[float],
    *,
    roads: Sequence[OsmRoad] = (),
    approach_boundaries: Sequence[Mapping[str, Any]] = (),
) -> List[Dict[str, Any]] | None:
    """Extract arms data from road_segment_graph for a specific junction."""
    if road_segment_graph is None:
        return None

    from roadgen3d.placement_zones import _road_profile_widths_from_graph

    # Find the junction in the graph
    junctions = list(getattr(road_segment_graph, "junctions", ()) or ())
    junction_obj = None
    for j in junctions:
        if str(getattr(j, "junction_id", "") or "") == junction_id:
            junction_obj = j
            break
    if junction_obj is None:
        anchor = (float(anchor_xy[0]), float(anchor_xy[1]))
        candidates = [
            j
            for j in junctions
            if str(getattr(j, "kind", "") or "") in {"cross_junction", "t_junction"}
            and math.hypot(
                anchor[0] - float(tuple(getattr(j, "anchor_xy", (0.0, 0.0)) or (0.0, 0.0))[0]),
                anchor[1] - float(tuple(getattr(j, "anchor_xy", (0.0, 0.0)) or (0.0, 0.0))[1]),
            ) <= 0.75
        ]
        if candidates:
            junction_obj = candidates[0]

    if junction_obj is None:
        return None

    # Get road profiles
    road_profiles = _road_profile_widths_from_graph(road_segment_graph)

    # Build arms data similar to _build_explicit_graph_junction_geometries
    roads_by_id = {}
    for road in roads or getattr(road_segment_graph, "roads", ()) or ():
        road_id = int(getattr(road, "osm_id", 0) or 0)
        if road_id > 0:
            roads_by_id[road_id] = road

    anchor = (float(anchor_xy[0]), float(anchor_xy[1]))
    def point_distance(point: Tuple[float, float], other: Tuple[float, float]) -> float:
        return math.hypot(float(point[0]) - float(other[0]), float(point[1]) - float(other[1]))

    def angle_deg(point: Tuple[float, float], other: Tuple[float, float]) -> float:
        value = math.degrees(math.atan2(float(other[1]) - float(point[1]), float(other[0]) - float(point[0])))
        return value + 360.0 if value < 0.0 else value

    arms: List[Dict[str, Any]] = []
    seen_branch_ids: set[str] = set()

    centerline_ids_by_road_id: Dict[int, str] = {
        int(road_id): str(centerline_id)
        for road_id, centerline_id in zip(
            tuple(getattr(junction_obj, "connected_road_ids", ()) or ()),
            tuple(getattr(junction_obj, "connected_centerline_ids", ()) or ()),
        )
    }

    def append_arm(
        *,
        road_id: int,
        centerline_id: str,
        branch_id: str,
        neighbor: Tuple[float, float],
        split_distance_m: float = 0.0,
    ) -> None:
        if road_id <= 0 or branch_id in seen_branch_ids:
            return
        road = roads_by_id.get(road_id)
        if road is None:
            return
        points = list(getattr(road, "coords", ()) or ())
        if len(points) < 2:
            return
        length_m = math.hypot(float(neighbor[0]) - anchor[0], float(neighbor[1]) - anchor[1])
        if length_m <= 1e-6:
            return
        tangent = (
            (float(neighbor[0]) - anchor[0]) / length_m,
            (float(neighbor[1]) - anchor[1]) / length_m,
        )
        profile = road_profiles.get(road_id, {})
        available_length_m = sum(
            math.hypot(
                float(end[0]) - float(start[0]),
                float(end[1]) - float(start[1]),
            )
            for start, end in zip(points[:-1], points[1:])
        )
        arms.append({
            "road_id": road_id,
            "centerline_id": branch_id or centerline_id,
            "angle_deg": angle_deg(anchor, neighbor),
            "tangent": tangent,
            "normal": (float(tangent[1]), float(-tangent[0])),
            "carriageway_width_m": max(float(profile.get("carriageway_width_m", 8.0) or 8.0), 1.0),
            "nearroad_buffer_width_m": float(profile.get("nearroad_buffer_width_m", 0.0) or 0.0),
            "nearroad_furnishing_width_m": float(profile.get("nearroad_furnishing_width_m", 0.0) or 0.0),
            "clear_sidewalk_width_m": float(profile.get("clear_sidewalk_width_m", 0.0) or 0.0),
            "farfromroad_buffer_width_m": float(profile.get("farfromroad_buffer_width_m", 0.0) or 0.0),
            "frontage_reserve_width_m": float(profile.get("frontage_reserve_width_m", 0.0) or 0.0),
            "side_strip_layouts": dict(profile.get("side_strip_layouts", {}) or {}),
            "center_strip_layouts": list(profile.get("center_strip_layouts", []) or ()),
            "available_length_m": max(float(available_length_m), float(length_m)),
            "split_distance_m": max(float(split_distance_m), 0.0),
        })
        seen_branch_ids.add(branch_id)

    # The placement pass already solved the actual approach cut lines.  Use
    # one arm per boundary rather than one arm per OSM way: a through road has
    # two opposing approaches with the same road_id, and collapsing those into
    # one record was why T junctions silently fell back to rectangular patches.
    for boundary_index, boundary in enumerate(approach_boundaries):
        road_id = int(boundary.get("road_id", 0) or 0)
        center = tuple(boundary.get("center_xy", ()) or ())
        if road_id <= 0 or len(center) < 2:
            continue
        centerline_id = centerline_ids_by_road_id.get(road_id, f"road_{road_id}")
        boundary_id = str(boundary.get("boundary_id", "") or f"approach_{boundary_index:02d}")
        append_arm(
            road_id=road_id,
            centerline_id=centerline_id,
            branch_id=f"{centerline_id}:{boundary_id}",
            neighbor=(float(center[0]), float(center[1])),
            split_distance_m=float(boundary.get("exit_distance_m", 0.0) or 0.0),
        )
    if len(arms) >= 3:
        return arms

    connected_road_ids = tuple(getattr(junction_obj, "connected_road_ids", ()) or ())
    connected_centerline_ids = tuple(getattr(junction_obj, "connected_centerline_ids", ()) or ())

    for road_id, centerline_id in zip(connected_road_ids, connected_centerline_ids):
        road_id = int(road_id)
        if road_id <= 0:
            continue
        road = roads_by_id.get(road_id)
        if road is None:
            continue
        points = list(getattr(road, "coords", ()) or ())
        if len(points) < 2:
            continue

        # Find the neighbor point closest to anchor
        if point_distance(anchor, points[0]) <= point_distance(anchor, points[-1]):
            neighbor = points[1] if len(points) > 1 else points[0]
        else:
            neighbor = points[-2] if len(points) > 1 else points[0]

        append_arm(
            road_id=road_id,
            centerline_id=str(centerline_id),
            branch_id=str(centerline_id),
            neighbor=(float(neighbor[0]), float(neighbor[1])),
        )

    return arms if len(arms) >= 3 else None


def _apply_manual_junction_compositions(
    annotation: ReferenceAnnotation,
    junction_geometries: List[Dict[str, Any]],
    *,
    road_segment_graph: Any | None = None,
    roads: Sequence[OsmRoad] = (),
    compose_config: StreetComposeConfig | None = None,
) -> List[Dict[str, Any]]:
    manual_by_junction_id = {comp.junction_id: comp for comp in annotation.junction_compositions} if annotation.junction_compositions else {}

    # Pre-compute arms data for cross junctions without manual compositions
    arms_by_junction_id: Dict[str, List[Dict[str, Any]]] = {}
    if road_segment_graph is not None and not manual_by_junction_id:
        # Only pre-compute if there are manual compositions to apply,
        # because we only need fusion for cross_junctions without manual compositions
        pass  # Lazy computation below

    result: List[Dict[str, Any]] = []
    seen_junction_ids: set[str] = set()
    for geom in junction_geometries:
        junction_id = str(geom.get("junction_id", "") or "")
        kind = str(geom.get("kind", "") or "")

        if junction_id in manual_by_junction_id:
            manual_geom = _build_manual_junction_geometry(manual_by_junction_id[junction_id])
            # Preserve fields that manual geom doesn't provide (core rect, crosswalks, etc.)
            merged = {**geom, **manual_geom}
            result.append(merged)
            seen_junction_ids.add(junction_id)
        elif kind in {"cross_junction", "t_junction"} and junction_id not in manual_by_junction_id:
            # Generate both three- and four-arm junctions from the same
            # continuous corner-ribbon algorithm.
            fusion_geom = _try_build_cross_fusion_for_junction(
                annotation, geom, road_segment_graph, roads, compose_config
            )
            if fusion_geom is not None:
                merged = {**geom, **fusion_geom}
                # Keep the placement solver's full transition envelope as an
                # independent render-level target.  The fusion partition uses
                # its own exact semantic union internally, but auditing that
                # union against itself cannot reveal a missing connector at a
                # road-arm seam (the historical 2.73/4.17 m2 red patches).
                merged["expected_transition_envelope"] = geom.get(
                    "sidewalk_trim_zone"
                )
                result.append(merged)
            else:
                result.append(geom)
        else:
            result.append(geom)

    for junction_id, comp in manual_by_junction_id.items():
        if junction_id in seen_junction_ids:
            continue
        result.append(_build_manual_junction_geometry(comp))
    return result


__all__ = [
    "ANNOTATION_SCENE_BBOX_PADDING_M",
    "ReferenceAnnotationSceneBridgeResult",
    "build_reference_annotation_scene_bridge",
]
