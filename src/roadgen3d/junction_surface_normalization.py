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
MAX_PLANAR_OVERLAP_AREA_M2 = 1e-4
MIN_SURFACE_COMPONENT_AREA_M2 = 1e-2
DEFAULT_GAP_CLOSE_TOLERANCE_M = 0.02


def normalize_junction_surface_geometries(junction_geometries: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Return junction geometry records with canonical normalized surface patches.

    The raw patch buckets remain present for diagnostics/provenance, but consumers
    should render ``normalized_surface_patches`` when it is available.
    """

    normalized = [normalize_junction_surface_geometry(junction) for junction in junction_geometries]
    for junction in normalized:
        generation_mode = str(junction.get("generation_mode", "") or junction.get("debug_info", {}).get("generation_mode", ""))
        quality = junction.get("geometry_qa", {})
        if "continuous_junction_fusion" in generation_mode and not bool(quality.get("ok", False)):
            raise ValueError(
                f"Junction surface QA failed for {junction.get('junction_id', 'junction')}: "
                f"overlap={quality.get('coplanar_overlap_area_m2', 0.0)}m2, "
                f"uncovered={quality.get('junction_uncovered_area_m2', 0.0)}m2, "
                f"unassigned={quality.get('unassigned_transition_area_m2', 0.0)}m2, "
                f"transition_uncovered={quality.get('junction_transition_uncovered_area_m2', 0.0)}m2, "
                f"transition_fills={quality.get('junction_transition_fill_count', 0)}, "
                f"seam_width={quality.get('max_semantic_seam_width_error_m', 0.0)}m, "
                f"seam_tangent={quality.get('max_semantic_seam_tangent_error_deg', 0.0)}deg, "
                f"invalid={quality.get('invalid_polygon_count', 0)}, "
                f"slivers={quality.get('sliver_component_count', 0)}"
            )
    return normalized


def normalize_junction_surface_geometry(junction: Mapping[str, Any]) -> Dict[str, Any]:
    from shapely.geometry import GeometryCollection
    from shapely.ops import unary_union

    normalized_junction: Dict[str, Any] = dict(junction)
    junction_id = str(junction.get("junction_id", "") or "junction")
    precision_grid_m = max(
        float(junction.get("debug_info", {}).get("precision_grid_m", 0.001) or 0.001),
        0.0001,
    )
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
        if cleaned is not None and not getattr(cleaned, "is_empty", True):
            try:
                from shapely import set_precision

                cleaned = _clean_polygonal_geometry(set_precision(cleaned, grid_size=precision_grid_m))
            except Exception:
                pass
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
    junction_transition_fill_area_m2 = 0.0
    junction_transition_fill_count = 0
    unassigned_transition_area_m2 = 0.0
    unassigned_transition_count = 0
    if canonical_surface_patches:
        # ``sidewalk_trim_zone`` is the independent junction envelope removed
        # from the straight road-arm surfaces before the curved corner ribbons
        # are installed.  Every part of that envelope must therefore be owned
        # by a canonical junction surface.  The continuous turn connector can
        # otherwise leave a long, open wedge between the road-arm cut line and
        # its curved curb boundary.  Because that wedge is absent from the
        # canonical patch union, auditing the union against itself cannot see
        # the missing pavement and the context-ground slab becomes visible.
        trim_zone = _clean_polygonal_geometry(junction.get("sidewalk_trim_zone"))
        canonical_geometries = [
            patch.get("geometry")
            for patch in canonical_surface_patches
            if patch.get("geometry") is not None
            and not getattr(patch.get("geometry"), "is_empty", True)
        ]
        if trim_zone is not None and not getattr(trim_zone, "is_empty", True) and canonical_geometries:
            canonical_union = _clean_polygonal_geometry(unary_union(canonical_geometries))
            try:
                from shapely import difference, set_precision

                trim_zone = _clean_polygonal_geometry(
                    set_precision(trim_zone, grid_size=precision_grid_m)
                )
                canonical_union = _clean_polygonal_geometry(
                    set_precision(canonical_union, grid_size=precision_grid_m)
                )
                transition_gap = _clean_polygonal_geometry(
                    difference(
                        trim_zone,
                        canonical_union,
                        grid_size=precision_grid_m,
                    )
                )
            except Exception:
                transition_gap = (
                    _clean_polygonal_geometry(trim_zone.difference(canonical_union))
                    if canonical_union is not None
                    else None
                )
            if canonical_union is not None and not getattr(canonical_union, "is_empty", True):
                transition_components = [
                    component
                    for component in _polygon_components(transition_gap)
                    if float(component.area) > max(precision_grid_m * precision_grid_m, 1e-8)
                ]
                unassigned_transition_area_m2 = float(
                    sum(component.area for component in transition_components)
                )
                unassigned_transition_count = len(transition_components)
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
        try:
            from shapely import set_precision

            role_union = _clean_polygonal_geometry(
                set_precision(role_union, grid_size=precision_grid_m)
            )
        except Exception:
            pass
        same_role_overlap_removed += max(0.0, role_source_area - float(role_union.area))
        visible_geometry = role_union
        if not getattr(occupied_geometry, "is_empty", True):
            try:
                from shapely import difference, set_precision

                visible_geometry = _clean_polygonal_geometry(
                    difference(
                        role_union,
                        set_precision(occupied_geometry, grid_size=precision_grid_m),
                        grid_size=precision_grid_m,
                    )
                )
            except Exception:
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
            try:
                from shapely import set_precision

                occupied_geometry = _clean_polygonal_geometry(
                    set_precision(occupied_geometry, grid_size=precision_grid_m)
                ) or occupied_geometry
            except Exception:
                pass

    # Boolean partitioning can leave millimetre-scale residuals where several
    # priority boundaries meet. Assign those remnants to the lowest planar
    # layer before triangulation so the exported surface remains a partition.
    normalization_residual_area_m2 = 0.0
    if planar_union is not None and not getattr(planar_union, "is_empty", True):
        residual = _clean_polygonal_geometry(planar_union.difference(occupied_geometry))
        if residual is not None and not getattr(residual, "is_empty", True):
            residual_components = _polygon_components(residual)
            normalization_residual_area_m2 = float(sum(component.area for component in residual_components))
            context_records = [
                patch for patch in normalized_patches if patch.get("surface_role") == "context_ground"
            ]
            normalized_patches = [
                patch for patch in normalized_patches if patch.get("surface_role") != "context_ground"
            ]
            context_union = _clean_polygonal_geometry(
                unary_union([
                    *(patch["geometry"] for patch in context_records),
                    *residual_components,
                ])
            )
            for component_index, component in enumerate(_polygon_components(context_union)):
                normalized_patches.append(
                    {
                        "surface_id": f"{junction_id}_normalized_context_ground_{component_index:02d}",
                        "surface_kind": "normalized",
                        "surface_role": "context_ground",
                        "geometry": component,
                        "component_index": int(component_index),
                        "source_ids": sorted({
                            "normalization_residual",
                            *(source_id for patch in context_records for source_id in patch.get("source_ids", []) or ()),
                        }),
                        "source_kinds": sorted({
                            "normalization_residual",
                            *(source_kind for patch in context_records for source_kind in patch.get("source_kinds", []) or ()),
                        }),
                        "is_overlay": False,
                        "area_m2": float(component.area),
                    }
                )
            occupied_geometry = _clean_polygonal_geometry(unary_union([occupied_geometry, residual])) or occupied_geometry

    sliver_reassigned_count = 0
    sliver_records = [
        patch
        for patch in normalized_patches
        if not patch.get("is_overlay", False)
        and float(getattr(patch.get("geometry"), "area", 0.0) or 0.0) < MIN_SURFACE_COMPONENT_AREA_M2
    ]
    for sliver in sliver_records:
        geometry = sliver.get("geometry")
        candidates = [
            patch
            for patch in normalized_patches
            if patch is not sliver
            and not patch.get("is_overlay", False)
            and patch.get("geometry") is not None
            and not getattr(patch.get("geometry"), "is_empty", True)
            and float(patch["geometry"].distance(geometry)) <= precision_grid_m * 2.0
        ]
        if not candidates:
            continue
        candidates.sort(
            key=lambda patch: (
                patch.get("surface_role") == sliver.get("surface_role"),
                float(patch["geometry"].boundary.intersection(geometry.boundary).length),
                float(patch["geometry"].area),
            ),
            reverse=True,
        )
        target = candidates[0]
        merged_geometry = _clean_polygonal_geometry(unary_union([target["geometry"], geometry]))
        if merged_geometry is None or getattr(merged_geometry, "is_empty", True):
            continue
        target["geometry"] = merged_geometry
        target["area_m2"] = float(merged_geometry.area)
        target["source_ids"] = sorted({
            *(target.get("source_ids", []) or ()),
            *(sliver.get("source_ids", []) or ()),
        })
        target["source_kinds"] = sorted({
            *(target.get("source_kinds", []) or ()),
            *(sliver.get("source_kinds", []) or ()),
            "sliver_reassignment",
        })
        normalized_patches.remove(sliver)
        sliver_reassigned_count += 1

    # Sliver reassignment and GEOS validity repair can re-introduce a tiny
    # overlap along shared boundaries. Repartition once more on the configured
    # metric grid so the records consumed by GLB export are strictly disjoint.
    repartitioned_planar_patches: List[Dict[str, Any]] = []
    repartition_occupied = GeometryCollection()
    for role in PLANAR_SURFACE_ROLE_PRIORITY:
        for patch in [item for item in normalized_patches if item.get("surface_role") == role]:
            geometry = patch.get("geometry")
            if geometry is None or getattr(geometry, "is_empty", True):
                continue
            try:
                from shapely import difference, set_precision

                geometry = set_precision(geometry, grid_size=precision_grid_m)
                if not getattr(repartition_occupied, "is_empty", True):
                    geometry = difference(
                        geometry,
                        set_precision(repartition_occupied, grid_size=precision_grid_m),
                        grid_size=precision_grid_m,
                    )
            except Exception:
                if not getattr(repartition_occupied, "is_empty", True):
                    geometry = geometry.difference(repartition_occupied)
            visible_components = _polygon_components(_clean_polygonal_geometry(geometry))
            for component_index, component in enumerate(visible_components):
                record = dict(patch)
                record["geometry"] = component
                record["area_m2"] = float(component.area)
                record["component_index"] = int(component_index)
                record["surface_id"] = f"{junction_id}_normalized_{role}_{len(repartitioned_planar_patches):02d}"
                repartitioned_planar_patches.append(record)
            if visible_components:
                repartition_occupied = _clean_polygonal_geometry(
                    unary_union([repartition_occupied, *visible_components])
                ) or repartition_occupied
    # Audit a final grid residual, but never assign an unknown region a semantic
    # role. A real residual must fail QA instead of silently becoming road.
    sidewalk_trim_zone = _clean_polygonal_geometry(junction.get("sidewalk_trim_zone"))
    final_envelope_residual_area_m2 = 0.0
    if sidewalk_trim_zone is not None and not getattr(sidewalk_trim_zone, "is_empty", True):
        try:
            from shapely import difference, set_precision

            final_residual = _clean_polygonal_geometry(
                difference(
                    set_precision(sidewalk_trim_zone, grid_size=precision_grid_m),
                    set_precision(repartition_occupied, grid_size=precision_grid_m),
                    grid_size=precision_grid_m,
                )
            )
        except Exception:
            final_residual = _clean_polygonal_geometry(
                sidewalk_trim_zone.difference(repartition_occupied)
            )
        residual_components = [
            component
            for component in _polygon_components(final_residual)
            if float(component.area) >= MIN_SURFACE_COMPONENT_AREA_M2
        ]
        final_envelope_residual_area_m2 = float(
            sum(component.area for component in residual_components)
        )
    normalized_patches = repartitioned_planar_patches

    # Repartitioning itself can create a sub-centimetre role fragment at a
    # three-way grid vertex. Merge only such already-classified numerical
    # fragments into a touching surface; never create a new semantic patch.
    final_slivers = [
        patch
        for patch in list(normalized_patches)
        if float(getattr(patch.get("geometry"), "area", 0.0) or 0.0)
        < MIN_SURFACE_COMPONENT_AREA_M2
    ]
    for sliver in final_slivers:
        geometry = sliver.get("geometry")
        candidates = []
        for candidate in normalized_patches:
            if candidate is sliver:
                continue
            candidate_geometry = candidate.get("geometry")
            if candidate_geometry is None or getattr(candidate_geometry, "is_empty", True):
                continue
            shared_length = float(
                candidate_geometry.boundary.intersection(geometry.boundary).length
            )
            distance = float(candidate_geometry.distance(geometry))
            if shared_length <= 1e-9 and distance > precision_grid_m:
                continue
            candidates.append(
                (
                    candidate.get("surface_role") == sliver.get("surface_role"),
                    shared_length,
                    -distance,
                    float(candidate_geometry.area),
                    candidate,
                )
            )
        if not candidates:
            continue
        candidates.sort(key=lambda item: item[:4], reverse=True)
        target = candidates[0][4]
        merged = _clean_polygonal_geometry(unary_union([target["geometry"], geometry]))
        if merged is None or getattr(merged, "is_empty", True):
            continue
        target["geometry"] = merged
        target["area_m2"] = float(merged.area)
        target["source_ids"] = sorted(
            {*(target.get("source_ids", []) or ()), *(sliver.get("source_ids", []) or ())}
        )
        target["source_kinds"] = sorted(
            {
                *(target.get("source_kinds", []) or ()),
                *(sliver.get("source_kinds", []) or ()),
                "precision_grid_sliver_merge",
            }
        )
        normalized_patches.remove(sliver)
        sliver_reassigned_count += 1

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
    normalization_debug = {
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
        "normalization_residual_area_m2": round(normalization_residual_area_m2, 6),
        "sliver_reassigned_count": int(sliver_reassigned_count),
        "junction_transition_fill_count": int(junction_transition_fill_count),
        "junction_transition_fill_area_m2": round(junction_transition_fill_area_m2, 6),
        "unassigned_transition_count": int(unassigned_transition_count),
        "unassigned_transition_area_m2": round(unassigned_transition_area_m2, 6),
        "final_envelope_residual_area_m2": round(final_envelope_residual_area_m2, 6),
    }
    normalized_junction["surface_normalization_debug"] = normalization_debug
    geometry_qa = audit_junction_surface_geometry(
        normalized_junction,
        gap_close_tolerance_m=float(
            junction.get("debug_info", {}).get("precision_grid_m", 0.001)
            or 0.001
        ),
    )
    normalized_junction["geometry_qa"] = geometry_qa
    normalization_debug["geometry_qa"] = geometry_qa
    return normalized_junction


def audit_junction_surface_geometry(
    junction: Mapping[str, Any],
    *,
    gap_close_tolerance_m: float = DEFAULT_GAP_CLOSE_TOLERANCE_M,
) -> Dict[str, Any]:
    """Measure final planar overlap, tiny gaps, invalid polygons and slivers."""
    from shapely.geometry import GeometryCollection
    from shapely.ops import unary_union

    planar_records = [
        patch
        for patch in junction.get("normalized_surface_patches", []) or ()
        if isinstance(patch, Mapping) and not bool(patch.get("is_overlay", False))
    ]
    geometries = [
        patch.get("geometry")
        for patch in planar_records
        if patch.get("geometry") is not None and not getattr(patch.get("geometry"), "is_empty", True)
    ]
    invalid_polygon_count = sum(1 for geometry in geometries if not bool(getattr(geometry, "is_valid", False)))
    components = [component for geometry in geometries for component in _polygon_components(geometry)]
    sliver_component_count = sum(
        1 for component in components if float(getattr(component, "area", 0.0) or 0.0) < MIN_SURFACE_COMPONENT_AREA_M2
    )
    normalization_debug = junction.get("surface_normalization_debug", {}) or {}
    generation_debug = junction.get("debug_info", {}) or {}
    unassigned_transition_area_m2 = float(
        normalization_debug.get("unassigned_transition_area_m2", 0.0) or 0.0
    )
    final_envelope_residual_area_m2 = float(
        normalization_debug.get("final_envelope_residual_area_m2", 0.0) or 0.0
    )
    junction_transition_fill_count = int(
        generation_debug.get("junction_transition_fill_count", 0) or 0
    )
    max_semantic_seam_width_error_m = float(
        generation_debug.get("max_semantic_seam_width_error_m", 0.0) or 0.0
    )
    max_semantic_seam_tangent_error_deg = float(
        generation_debug.get("max_semantic_seam_tangent_error_deg", 0.0) or 0.0
    )

    coplanar_overlap_area_m2 = 0.0
    occupied = GeometryCollection()
    for geometry in geometries:
        if not getattr(occupied, "is_empty", True):
            coplanar_overlap_area_m2 += float(geometry.intersection(occupied).area)
        occupied = unary_union([occupied, geometry])

    planar_union = unary_union(geometries) if geometries else GeometryCollection()
    tolerance = max(float(gap_close_tolerance_m), 0.0)
    canonical_geometries = [
        patch.get("geometry")
        for patch in junction.get("canonical_surface_patches", []) or ()
        if isinstance(patch, Mapping)
        and patch.get("geometry") is not None
        and not getattr(patch.get("geometry"), "is_empty", True)
    ]
    try:
        from shapely import set_precision

        planar_union = set_precision(planar_union, grid_size=max(tolerance, 0.0001))
        canonical_geometries = [
            set_precision(geometry, grid_size=max(tolerance, 0.0001))
            for geometry in canonical_geometries
        ]
    except Exception:
        pass
    envelope_geometries = list(canonical_geometries)
    sidewalk_trim_zone = _clean_polygonal_geometry(junction.get("sidewalk_trim_zone"))
    if sidewalk_trim_zone is not None and not getattr(sidewalk_trim_zone, "is_empty", True):
        envelope_geometries.append(sidewalk_trim_zone)
    canonical_envelope = unary_union(envelope_geometries) if envelope_geometries else planar_union
    try:
        from shapely import set_precision

        # Snap once more after union.  Snapping each source independently can
        # leave sub-cell seams in GEOS even when the final metric-grid union is
        # complete.
        canonical_envelope = set_precision(
            canonical_envelope,
            grid_size=max(tolerance, 0.0001),
        )
    except Exception:
        pass
    uncovered_area_m2 = float(canonical_envelope.difference(planar_union).area)
    envelope_area_m2 = float(getattr(canonical_envelope, "area", 0.0) or 0.0)
    # The full canonical envelope can retain a sub-millimetre GEOS seam along
    # an outer connector edge.  Keep the legacy numerical tolerance there,
    # while the independently meaningful road-arm transition envelope below is
    # enforced at the stricter visible-surface threshold.
    uncovered_limit_m2 = max(1e-3, envelope_area_m2 * 1e-6)
    junction_transition_uncovered_area_m2 = 0.0
    if sidewalk_trim_zone is not None and not getattr(sidewalk_trim_zone, "is_empty", True):
        try:
            from shapely import set_precision

            transition_envelope = set_precision(
                sidewalk_trim_zone,
                grid_size=max(tolerance, 0.0001),
            )
        except Exception:
            transition_envelope = sidewalk_trim_zone
        # GEOS may retain sub-grid seams where several curved role boundaries
        # meet.  Audit coverage with one precision-cell tolerance so those
        # numerical seams are not mistaken for a visible road-arm wedge.
        transition_cover = planar_union.buffer(max(tolerance, 0.001))
        junction_transition_uncovered_area_m2 = float(
            transition_envelope.difference(transition_cover).area
        )
    ok = bool(
        invalid_polygon_count == 0
        and sliver_component_count == 0
        and coplanar_overlap_area_m2 <= MAX_PLANAR_OVERLAP_AREA_M2
        and uncovered_area_m2 <= uncovered_limit_m2
        and junction_transition_uncovered_area_m2 <= 1e-4
        and unassigned_transition_area_m2 <= 1e-4
        and final_envelope_residual_area_m2 <= 1e-4
        and junction_transition_fill_count == 0
        and max_semantic_seam_width_error_m <= 0.02
        and max_semantic_seam_tangent_error_deg <= 2.0
    )
    return {
        "ok": ok,
        "coplanar_overlap_area_m2": round(coplanar_overlap_area_m2, 6),
        "junction_uncovered_area_m2": round(uncovered_area_m2, 6),
        "junction_uncovered_limit_m2": round(uncovered_limit_m2, 6),
        "junction_transition_uncovered_area_m2": round(
            junction_transition_uncovered_area_m2,
            6,
        ),
        "unassigned_transition_area_m2": round(unassigned_transition_area_m2, 6),
        "final_envelope_residual_area_m2": round(final_envelope_residual_area_m2, 6),
        "junction_transition_fill_count": int(junction_transition_fill_count),
        "max_semantic_seam_width_error_m": round(max_semantic_seam_width_error_m, 6),
        "max_semantic_seam_tangent_error_deg": round(
            max_semantic_seam_tangent_error_deg,
            6,
        ),
        "invalid_polygon_count": int(invalid_polygon_count),
        "sliver_component_count": int(sliver_component_count),
        "gap_close_tolerance_m": round(tolerance, 6),
        "planar_envelope_area_m2": round(envelope_area_m2, 6),
    }


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
