"""Apply optimizer/LLM mutations to reference-plan annotations.

Template patches sit between a fixed base annotation and scene generation:
they may reshape cross-section strips and add/remove functional zones, but
they intentionally do not move centerlines, junctions, or building regions.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Sequence, Tuple

from .reference_annotation import (
    CENTER_STRIP_KINDS,
    FURNITURE_COMPATIBLE_STRIP_KINDS,
    LANE_STRIP_KINDS,
    NOMINAL_STRIP_WIDTHS,
    SIDE_STRIP_KINDS,
    VALID_CROSS_SECTION_ZONES,
    VALID_FUNCTIONAL_ZONE_KINDS,
    VALID_FURNITURE_KINDS,
    VALID_SURFACE_ANNOTATION_KINDS,
    VALID_SURFACE_ROLES,
    VALID_STRIP_DIRECTIONS,
    VALID_STRIP_KINDS,
    parse_reference_annotation,
)

TEMPLATE_PATCH_SCHEMA_VERSION = "roadgen3d_template_patch_v1"

DEFAULT_TEMPLATE_PATCH_CONSTRAINTS: Dict[str, Any] = {
    "min_strip_width_m": 0.1,
    "min_drive_lane_width_m": 2.8,
    "min_bus_lane_width_m": 3.0,
    "min_bike_lane_width_m": 1.2,
    "min_clear_sidewalk_width_m": 1.5,
    "min_total_drive_lanes": 2,
    "require_bidirectional_drive_lanes": True,
}

_ZONE_ORDER = {"left": 0, "center": 1, "right": 2}
_NON_DIRECTIONAL_STRIP_KINDS = (VALID_STRIP_KINDS - LANE_STRIP_KINDS) | {"parking_lane"}


class TemplatePatchError(ValueError):
    """Raised when a template patch is invalid or violates design constraints."""


@dataclass(frozen=True)
class TemplatePatchApplication:
    """Result of applying a template patch to an annotation payload."""

    annotation: Dict[str, Any]
    applied_operations: Tuple[Dict[str, Any], ...]
    summary: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "annotation": copy.deepcopy(self.annotation),
            "applied_operations": [dict(item) for item in self.applied_operations],
            "summary": dict(self.summary),
        }


def apply_template_patch(
    annotation_payload: Mapping[str, Any],
    patch: Mapping[str, Any] | None,
) -> TemplatePatchApplication:
    """Return a mutated annotation payload plus an application summary.

    The returned payload is a deep copy. The base annotation is never mutated.
    """

    if not isinstance(annotation_payload, Mapping):
        raise TemplatePatchError("annotation_payload must be a JSON object.")
    result: Dict[str, Any] = copy.deepcopy(dict(annotation_payload))
    raw_patch: Dict[str, Any] = copy.deepcopy(dict(patch or {}))
    if not raw_patch:
        summary = _build_patch_summary(result, raw_patch, ())
        return TemplatePatchApplication(annotation=result, applied_operations=(), summary=summary)

    schema_version = str(raw_patch.get("schema_version") or TEMPLATE_PATCH_SCHEMA_VERSION).strip()
    if schema_version != TEMPLATE_PATCH_SCHEMA_VERSION:
        raise TemplatePatchError(
            f"Unsupported template_patch schema_version: {schema_version}. "
            f"Expected {TEMPLATE_PATCH_SCHEMA_VERSION}."
        )
    operations = raw_patch.get("operations") or []
    if not isinstance(operations, Sequence) or isinstance(operations, (str, bytes)):
        raise TemplatePatchError("template_patch.operations must be an array.")

    constraints = _merge_constraints(raw_patch.get("constraints"))
    applied: list[Dict[str, Any]] = []
    for index, raw_operation in enumerate(operations):
        if not isinstance(raw_operation, Mapping):
            raise TemplatePatchError(f"template_patch.operations[{index}] must be an object.")
        operation = dict(raw_operation)
        op_type = str(operation.get("op") or operation.get("type") or "").strip().lower()
        if not op_type:
            raise TemplatePatchError(f"template_patch.operations[{index}].op is required.")
        applied.extend(_apply_operation(result, operation, op_type=op_type, index=index, constraints=constraints))

    _validate_annotation(result)
    summary = _build_patch_summary(result, raw_patch, tuple(applied))
    _attach_patch_metadata(result, summary)
    return TemplatePatchApplication(
        annotation=result,
        applied_operations=tuple(applied),
        summary=summary,
    )


def _apply_operation(
    annotation: Dict[str, Any],
    operation: Mapping[str, Any],
    *,
    op_type: str,
    index: int,
    constraints: Mapping[str, Any],
) -> Tuple[Dict[str, Any], ...]:
    if op_type in {"resize_strip", "update_strip", "remove_strip", "add_strip", "replace_strips"}:
        centerline_ids = _resolve_centerline_ids(annotation, operation, index=index)
        applied: list[Dict[str, Any]] = []
        for centerline_id in centerline_ids:
            centerline = _get_centerline(annotation, centerline_id, index=index)
            if op_type == "resize_strip":
                _resize_strip(centerline, operation, index=index)
            elif op_type == "update_strip":
                _update_strip(centerline, operation, index=index)
            elif op_type == "remove_strip":
                _remove_strip(centerline, operation, index=index)
            elif op_type == "add_strip":
                _add_strip(centerline, operation, index=index)
            elif op_type == "replace_strips":
                _replace_strips(centerline, operation, index=index)
            _normalize_centerline(centerline, constraints=constraints, label=f"operations[{index}]/{centerline_id}")
            applied.append({"op": op_type, "centerline_id": centerline_id})
        return tuple(applied)
    if op_type in {"add_functional_zone", "upsert_functional_zone"}:
        zone_id = _upsert_functional_zone(annotation, operation, index=index, replace_existing=op_type == "upsert_functional_zone")
        return ({"op": op_type, "zone_id": zone_id},)
    if op_type == "remove_functional_zone":
        zone_id = _remove_functional_zone(annotation, operation, index=index)
        return ({"op": op_type, "zone_id": zone_id},)
    if op_type in {"add_surface_annotation", "upsert_surface_annotation"}:
        surface_id = _upsert_surface_annotation(annotation, operation, index=index, replace_existing=op_type == "upsert_surface_annotation")
        return ({"op": op_type, "surface_id": surface_id},)
    if op_type == "remove_surface_annotation":
        surface_id = _remove_surface_annotation(annotation, operation, index=index)
        return ({"op": op_type, "surface_id": surface_id},)
    raise TemplatePatchError(f"Unsupported template_patch operation: {op_type}.")


def _merge_constraints(raw_constraints: Any) -> Dict[str, Any]:
    constraints = dict(DEFAULT_TEMPLATE_PATCH_CONSTRAINTS)
    if raw_constraints is None:
        return constraints
    if not isinstance(raw_constraints, Mapping):
        raise TemplatePatchError("template_patch.constraints must be an object when provided.")
    for key, value in raw_constraints.items():
        if key in {"require_bidirectional_drive_lanes"}:
            constraints[str(key)] = bool(value)
            continue
        if key in constraints:
            constraints[str(key)] = _finite_float(value, f"constraints.{key}")
    constraints["min_total_drive_lanes"] = int(max(0, round(float(constraints["min_total_drive_lanes"]))))
    return constraints


def _resolve_centerline_ids(
    annotation: Mapping[str, Any],
    operation: Mapping[str, Any],
    *,
    index: int,
) -> Tuple[str, ...]:
    target = operation.get("target")
    target_record = target if isinstance(target, Mapping) else {}
    all_centerlines = (
        bool(operation.get("all_centerlines"))
        or bool(target_record.get("all_centerlines"))
        or str(operation.get("selector") or target_record.get("selector") or "").strip().lower() in {"*", "all", "all_centerlines"}
    )
    if all_centerlines:
        return tuple(_centerline_id(item) for item in _centerlines(annotation))

    raw_ids = operation.get("centerline_ids", target_record.get("centerline_ids"))
    if raw_ids is not None:
        if not isinstance(raw_ids, Sequence) or isinstance(raw_ids, (str, bytes)):
            raise TemplatePatchError(f"template_patch.operations[{index}].centerline_ids must be an array.")
        ids = tuple(str(item).strip() for item in raw_ids if str(item).strip())
        if not ids:
            raise TemplatePatchError(f"template_patch.operations[{index}].centerline_ids cannot be empty.")
        return ids

    raw_id = operation.get("centerline_id", target_record.get("centerline_id"))
    centerline_id = str(raw_id or "").strip()
    if centerline_id:
        return (centerline_id,)
    raise TemplatePatchError(
        f"template_patch.operations[{index}] must target centerline_id, centerline_ids, or all_centerlines."
    )


def _centerlines(annotation: Mapping[str, Any]) -> Tuple[Mapping[str, Any], ...]:
    raw_centerlines = annotation.get("centerlines") or []
    if not isinstance(raw_centerlines, Sequence) or isinstance(raw_centerlines, (str, bytes)):
        raise TemplatePatchError("annotation.centerlines must be an array.")
    return tuple(item for item in raw_centerlines if isinstance(item, Mapping))


def _get_centerline(annotation: Mapping[str, Any], centerline_id: str, *, index: int) -> Dict[str, Any]:
    for centerline in _centerlines(annotation):
        if _centerline_id(centerline) == centerline_id:
            return centerline  # type: ignore[return-value]
    raise TemplatePatchError(f"template_patch.operations[{index}] references unknown centerline_id: {centerline_id}.")


def _centerline_id(centerline: Mapping[str, Any]) -> str:
    return str(centerline.get("id") or centerline.get("feature_id") or "").strip()


def _resize_strip(centerline: Dict[str, Any], operation: Mapping[str, Any], *, index: int) -> None:
    strip_id = _required_text(operation, "strip_id", f"template_patch.operations[{index}]")
    width_m = _finite_float(operation.get("width_m"), f"template_patch.operations[{index}].width_m")
    strip = _get_strip(centerline, strip_id, index=index)
    strip["width_m"] = width_m


def _update_strip(centerline: Dict[str, Any], operation: Mapping[str, Any], *, index: int) -> None:
    strip_id = _required_text(operation, "strip_id", f"template_patch.operations[{index}]")
    strip = _get_strip(centerline, strip_id, index=index)
    updates = operation.get("updates")
    update_record = dict(updates) if isinstance(updates, Mapping) else {
        key: value
        for key, value in operation.items()
        if key in {"zone", "kind", "width_m", "direction"}
    }
    if not update_record:
        raise TemplatePatchError(f"template_patch.operations[{index}].updates cannot be empty.")
    for key in ("zone", "kind", "direction"):
        if key in update_record:
            strip[key] = str(update_record[key]).strip().lower()
    if "width_m" in update_record:
        strip["width_m"] = _finite_float(update_record["width_m"], f"template_patch.operations[{index}].updates.width_m")


def _remove_strip(centerline: Dict[str, Any], operation: Mapping[str, Any], *, index: int) -> None:
    strip_id = _required_text(operation, "strip_id", f"template_patch.operations[{index}]")
    strips = _strip_records(centerline)
    next_strips = [strip for strip in strips if str(strip.get("strip_id") or "").strip() != strip_id]
    if len(next_strips) == len(strips):
        raise TemplatePatchError(f"template_patch.operations[{index}] references unknown strip_id: {strip_id}.")
    centerline["cross_section_strips"] = next_strips
    centerline["street_furniture_instances"] = [
        item
        for item in _street_furniture_records(centerline)
        if str(item.get("strip_id") or "").strip() != strip_id
    ]


def _add_strip(centerline: Dict[str, Any], operation: Mapping[str, Any], *, index: int) -> None:
    raw_strip = operation.get("strip")
    if not isinstance(raw_strip, Mapping):
        raise TemplatePatchError(f"template_patch.operations[{index}].strip must be an object.")
    existing_ids = {str(strip.get("strip_id") or "").strip() for strip in _strip_records(centerline)}
    strip = _normalize_strip_record(
        dict(raw_strip),
        label=f"template_patch.operations[{index}].strip",
        existing_ids=existing_ids,
    )
    _insert_strip(centerline, strip, operation=operation, index=index)


def _replace_strips(centerline: Dict[str, Any], operation: Mapping[str, Any], *, index: int) -> None:
    raw_strips = operation.get("strips")
    if not isinstance(raw_strips, Sequence) or isinstance(raw_strips, (str, bytes)):
        raise TemplatePatchError(f"template_patch.operations[{index}].strips must be an array.")
    existing_ids: set[str] = set()
    strips: list[Dict[str, Any]] = []
    for strip_index, raw_strip in enumerate(raw_strips):
        if not isinstance(raw_strip, Mapping):
            raise TemplatePatchError(f"template_patch.operations[{index}].strips[{strip_index}] must be an object.")
        strip = _normalize_strip_record(
            dict(raw_strip),
            label=f"template_patch.operations[{index}].strips[{strip_index}]",
            existing_ids=existing_ids,
        )
        existing_ids.add(str(strip["strip_id"]))
        strips.append(strip)
    centerline["cross_section_strips"] = strips
    valid_strip_ids = {str(strip["strip_id"]) for strip in strips}
    centerline["street_furniture_instances"] = [
        item
        for item in _street_furniture_records(centerline)
        if str(item.get("strip_id") or "").strip() in valid_strip_ids
    ]


def _insert_strip(
    centerline: Dict[str, Any],
    strip: Dict[str, Any],
    *,
    operation: Mapping[str, Any],
    index: int,
) -> None:
    strips = _strip_records(centerline)
    zone = str(strip["zone"])
    anchor_id = str(operation.get("before_strip_id") or operation.get("after_strip_id") or "").strip()
    insert_index = operation.get("order_index", strip.get("order_index"))
    if anchor_id:
        anchor = _get_strip(centerline, anchor_id, index=index)
        if str(anchor.get("zone") or "").strip().lower() != zone:
            raise TemplatePatchError(
                f"template_patch.operations[{index}] anchor strip {anchor_id} is not in the new strip zone {zone}."
            )
        anchor_order = int(anchor.get("order_index") or 0)
        insert_index = anchor_order if operation.get("before_strip_id") else anchor_order + 1
    if insert_index is None:
        same_zone_orders = [
            int(existing.get("order_index") or 0)
            for existing in strips
            if str(existing.get("zone") or "").strip().lower() == zone
        ]
        insert_index = (max(same_zone_orders) + 1) if same_zone_orders else 0
    insert_index = max(0, int(insert_index))
    for existing in strips:
        if str(existing.get("zone") or "").strip().lower() == zone and int(existing.get("order_index") or 0) >= insert_index:
            existing["order_index"] = int(existing.get("order_index") or 0) + 1
    strip["order_index"] = insert_index
    centerline["cross_section_strips"] = [*strips, strip]


def _get_strip(centerline: Mapping[str, Any], strip_id: str, *, index: int) -> Dict[str, Any]:
    for strip in _strip_records(centerline):
        if str(strip.get("strip_id") or "").strip() == strip_id:
            return strip
    raise TemplatePatchError(f"template_patch.operations[{index}] references unknown strip_id: {strip_id}.")


def _strip_records(centerline: Mapping[str, Any]) -> list[Dict[str, Any]]:
    raw_strips = centerline.get("cross_section_strips") or []
    if not isinstance(raw_strips, Sequence) or isinstance(raw_strips, (str, bytes)):
        raise TemplatePatchError(f"centerline {_centerline_id(centerline)} cross_section_strips must be an array.")
    strips: list[Dict[str, Any]] = []
    for strip in raw_strips:
        if not isinstance(strip, Mapping):
            raise TemplatePatchError(f"centerline {_centerline_id(centerline)} contains a non-object strip.")
        strips.append(strip)  # type: ignore[arg-type]
    return strips


def _street_furniture_records(centerline: Mapping[str, Any]) -> list[Dict[str, Any]]:
    raw_items = centerline.get("street_furniture_instances") or []
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
        raise TemplatePatchError(f"centerline {_centerline_id(centerline)} street_furniture_instances must be an array.")
    return [item for item in raw_items if isinstance(item, dict)]


def _filter_compatible_street_furniture(centerline: Mapping[str, Any]) -> list[Dict[str, Any]]:
    compatible_strip_ids = {
        str(strip.get("strip_id") or "").strip()
        for strip in _strip_records(centerline)
        if str(strip.get("kind") or "").strip().lower() in FURNITURE_COMPATIBLE_STRIP_KINDS
    }
    return [
        item
        for item in _street_furniture_records(centerline)
        if str(item.get("strip_id") or "").strip() in compatible_strip_ids
    ]


def _normalize_centerline(
    centerline: Dict[str, Any],
    *,
    constraints: Mapping[str, Any],
    label: str,
) -> None:
    raw_strips = _strip_records(centerline)
    existing_ids: set[str] = set()
    normalized: list[Dict[str, Any]] = []
    for strip_index, raw_strip in enumerate(raw_strips):
        strip = _normalize_strip_record(
            raw_strip,
            label=f"{label}.cross_section_strips[{strip_index}]",
            existing_ids=existing_ids,
        )
        existing_ids.add(str(strip["strip_id"]))
        normalized.append(strip)
    normalized = _sort_and_reindex_strips(normalized)
    centerline["cross_section_mode"] = "detailed" if normalized else "coarse"
    centerline["cross_section_strips"] = normalized
    centerline["street_furniture_instances"] = _filter_compatible_street_furniture(centerline)
    _update_centerline_lane_fields(centerline)
    _validate_centerline_constraints(centerline, constraints=constraints, label=label)


def _normalize_strip_record(
    raw_strip: Dict[str, Any],
    *,
    label: str,
    existing_ids: set[str],
) -> Dict[str, Any]:
    zone = str(raw_strip.get("zone") or "center").strip().lower()
    if zone not in VALID_CROSS_SECTION_ZONES:
        raise TemplatePatchError(f"{label}.zone must be one of {sorted(VALID_CROSS_SECTION_ZONES)}.")
    kind = str(raw_strip.get("kind") or "drive_lane").strip().lower()
    if kind not in VALID_STRIP_KINDS:
        raise TemplatePatchError(f"{label}.kind must be one of {sorted(VALID_STRIP_KINDS)}.")
    if zone == "center" and kind not in CENTER_STRIP_KINDS:
        raise TemplatePatchError(f"{label} uses center zone but kind {kind!r} is not a center strip.")
    if zone in {"left", "right"} and kind not in SIDE_STRIP_KINDS:
        raise TemplatePatchError(f"{label} uses side zone {zone!r} but kind {kind!r} is not a side strip.")

    strip_id = str(raw_strip.get("strip_id") or "").strip()
    if not strip_id:
        strip_id = _next_strip_id(zone, kind, existing_ids)
    if strip_id in existing_ids:
        raise TemplatePatchError(f"{label}.strip_id duplicates an existing strip: {strip_id}.")

    width_m = _finite_float(
        raw_strip.get("width_m", NOMINAL_STRIP_WIDTHS.get(kind, 1.0)),
        f"{label}.width_m",
    )
    direction = str(raw_strip.get("direction") or "none").strip().lower()
    if direction not in VALID_STRIP_DIRECTIONS:
        raise TemplatePatchError(f"{label}.direction must be one of {sorted(VALID_STRIP_DIRECTIONS)}.")
    if kind in _NON_DIRECTIONAL_STRIP_KINDS:
        direction = "none"
    elif direction == "none":
        raise TemplatePatchError(f"{label}.direction is required for lane strip kind {kind!r}.")

    return {
        "strip_id": strip_id,
        "zone": zone,
        "kind": kind,
        "width_m": width_m,
        "direction": direction,
        "order_index": int(max(0, int(raw_strip.get("order_index", 0) or 0))),
    }


def _sort_and_reindex_strips(strips: Sequence[Mapping[str, Any]]) -> list[Dict[str, Any]]:
    sorted_strips = sorted(
        (dict(strip) for strip in strips),
        key=lambda item: (_ZONE_ORDER.get(str(item.get("zone")), 99), int(item.get("order_index") or 0), str(item.get("strip_id") or "")),
    )
    zone_counts = {"left": 0, "center": 0, "right": 0}
    for strip in sorted_strips:
        zone = str(strip["zone"])
        strip["order_index"] = zone_counts[zone]
        zone_counts[zone] += 1
    return sorted_strips


def _update_centerline_lane_fields(centerline: Dict[str, Any]) -> None:
    profile = _lane_profile(_strip_records(centerline))
    center_width = sum(
        max(float(strip.get("width_m") or 0.0), 0.0)
        for strip in _strip_records(centerline)
        if str(strip.get("zone") or "").strip().lower() == "center"
    )
    total_width = sum(max(float(strip.get("width_m") or 0.0), 0.0) for strip in _strip_records(centerline))
    centerline["forward_drive_lane_count"] = profile["forward_drive_lane_count"]
    centerline["reverse_drive_lane_count"] = profile["reverse_drive_lane_count"]
    centerline["bike_lane_count"] = profile["bike_lane_count"]
    centerline["bus_lane_count"] = profile["bus_lane_count"]
    centerline["parking_lane_count"] = profile["parking_lane_count"]
    centerline["lane_count"] = profile["total_drive_lane_count"]
    centerline["lane_profile"] = profile
    centerline["road_width_m"] = round(total_width, 3)
    centerline["carriageway_width_m"] = round(center_width, 3)


def _lane_profile(strips: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    forward_drive = 0
    reverse_drive = 0
    bidirectional_drive = 0
    bidirectional_lanes = 0
    bike = 0
    bus = 0
    parking = 0
    for strip in strips:
        if str(strip.get("zone") or "").strip().lower() != "center":
            continue
        kind = str(strip.get("kind") or "").strip().lower()
        direction = str(strip.get("direction") or "none").strip().lower()
        if kind == "drive_lane":
            if direction == "forward":
                forward_drive += 1
            elif direction == "reverse":
                reverse_drive += 1
            elif direction == "bidirectional":
                bidirectional_drive += 1
                bidirectional_lanes += 1
        elif kind == "bike_lane":
            bike += 1
            if direction == "bidirectional":
                bidirectional_lanes += 1
        elif kind == "bus_lane":
            bus += 1
            if direction == "bidirectional":
                bidirectional_lanes += 1
        elif kind == "parking_lane":
            parking += 1
    total_drive = forward_drive + reverse_drive + bidirectional_drive
    return {
        "forward_drive_lane_count": forward_drive,
        "reverse_drive_lane_count": reverse_drive,
        "bike_lane_count": bike,
        "bus_lane_count": bus,
        "parking_lane_count": parking,
        "bidirectional_drive_lane_count": bidirectional_drive,
        "bidirectional_lane_count": bidirectional_lanes,
        "total_drive_lane_count": total_drive,
        "total_lane_count": total_drive + bike + bus + parking,
    }


def _validate_centerline_constraints(
    centerline: Mapping[str, Any],
    *,
    constraints: Mapping[str, Any],
    label: str,
) -> None:
    strips = _strip_records(centerline)
    min_strip_width = float(constraints["min_strip_width_m"])
    for strip in strips:
        kind = str(strip.get("kind") or "")
        width_m = float(strip.get("width_m") or 0.0)
        if width_m < min_strip_width:
            raise TemplatePatchError(f"{label}.{strip.get('strip_id')}.width_m must be at least {min_strip_width:g} m.")
        if kind == "drive_lane" and width_m < float(constraints["min_drive_lane_width_m"]):
            raise TemplatePatchError(
                f"{label}.{strip.get('strip_id')} drive lane width must be at least {constraints['min_drive_lane_width_m']:g} m."
            )
        if kind == "bus_lane" and width_m < float(constraints["min_bus_lane_width_m"]):
            raise TemplatePatchError(
                f"{label}.{strip.get('strip_id')} bus lane width must be at least {constraints['min_bus_lane_width_m']:g} m."
            )
        if kind == "bike_lane" and width_m < float(constraints["min_bike_lane_width_m"]):
            raise TemplatePatchError(
                f"{label}.{strip.get('strip_id')} bike lane width must be at least {constraints['min_bike_lane_width_m']:g} m."
            )
        if kind == "clear_sidewalk" and width_m < float(constraints["min_clear_sidewalk_width_m"]):
            raise TemplatePatchError(
                f"{label}.{strip.get('strip_id')} clear sidewalk width must be at least {constraints['min_clear_sidewalk_width_m']:g} m."
            )
    profile = _lane_profile(strips)
    if profile["total_drive_lane_count"] < int(constraints["min_total_drive_lanes"]):
        raise TemplatePatchError(
            f"{label} must keep at least {constraints['min_total_drive_lanes']} drive lanes."
        )
    if bool(constraints["require_bidirectional_drive_lanes"]):
        has_forward = profile["forward_drive_lane_count"] > 0 or profile["bidirectional_drive_lane_count"] > 0
        has_reverse = profile["reverse_drive_lane_count"] > 0 or profile["bidirectional_drive_lane_count"] > 0
        if not (has_forward and has_reverse):
            raise TemplatePatchError(f"{label} must keep at least one drive lane in each direction.")


def _upsert_functional_zone(
    annotation: Dict[str, Any],
    operation: Mapping[str, Any],
    *,
    index: int,
    replace_existing: bool,
) -> str:
    raw_zone = operation.get("zone")
    if not isinstance(raw_zone, Mapping):
        raise TemplatePatchError(f"template_patch.operations[{index}].zone must be an object.")
    zone = _normalize_functional_zone(dict(raw_zone), index=index)
    zones = _functional_zone_records(annotation)
    existing_index = next(
        (zone_index for zone_index, item in enumerate(zones) if str(item.get("id") or item.get("feature_id") or "").strip() == zone["id"]),
        None,
    )
    if existing_index is not None:
        if not replace_existing:
            raise TemplatePatchError(f"template_patch.operations[{index}] functional zone already exists: {zone['id']}.")
        zones[existing_index] = zone
    else:
        zones.append(zone)
    annotation["functional_zones"] = zones
    return str(zone["id"])


def _remove_functional_zone(annotation: Dict[str, Any], operation: Mapping[str, Any], *, index: int) -> str:
    zone_id = _required_text(operation, "zone_id", f"template_patch.operations[{index}]")
    zones = _functional_zone_records(annotation)
    next_zones = [
        item
        for item in zones
        if str(item.get("id") or item.get("feature_id") or "").strip() != zone_id
    ]
    if len(next_zones) == len(zones):
        raise TemplatePatchError(f"template_patch.operations[{index}] references unknown zone_id: {zone_id}.")
    annotation["functional_zones"] = next_zones
    return zone_id


def _upsert_surface_annotation(
    annotation: Dict[str, Any],
    operation: Mapping[str, Any],
    *,
    index: int,
    replace_existing: bool,
) -> str:
    raw_surface = operation.get("surface") or operation.get("surface_annotation")
    if not isinstance(raw_surface, Mapping):
        raise TemplatePatchError(f"template_patch.operations[{index}].surface must be an object.")
    surface = _normalize_surface_annotation(dict(raw_surface), index=index)
    surfaces = _surface_annotation_records(annotation)
    existing_index = next(
        (
            surface_index
            for surface_index, item in enumerate(surfaces)
            if str(item.get("id") or item.get("feature_id") or "").strip() == surface["id"]
        ),
        None,
    )
    if existing_index is not None:
        if not replace_existing:
            raise TemplatePatchError(f"template_patch.operations[{index}] surface annotation already exists: {surface['id']}.")
        surfaces[existing_index] = surface
    else:
        surfaces.append(surface)
    annotation["surface_annotations"] = surfaces
    return str(surface["id"])


def _remove_surface_annotation(annotation: Dict[str, Any], operation: Mapping[str, Any], *, index: int) -> str:
    surface_id = _required_text(operation, "surface_id", f"template_patch.operations[{index}]")
    surfaces = _surface_annotation_records(annotation)
    next_surfaces = [
        item
        for item in surfaces
        if str(item.get("id") or item.get("feature_id") or "").strip() != surface_id
    ]
    if len(next_surfaces) == len(surfaces):
        raise TemplatePatchError(f"template_patch.operations[{index}] references unknown surface_id: {surface_id}.")
    annotation["surface_annotations"] = next_surfaces
    return surface_id


def _functional_zone_records(annotation: Mapping[str, Any]) -> list[Dict[str, Any]]:
    raw_zones = annotation.get("functional_zones") or []
    if not isinstance(raw_zones, Sequence) or isinstance(raw_zones, (str, bytes)):
        raise TemplatePatchError("annotation.functional_zones must be an array.")
    return [item for item in raw_zones if isinstance(item, dict)]


def _surface_annotation_records(annotation: Mapping[str, Any]) -> list[Dict[str, Any]]:
    raw_surfaces = annotation.get("surface_annotations") or []
    if not isinstance(raw_surfaces, Sequence) or isinstance(raw_surfaces, (str, bytes)):
        raise TemplatePatchError("annotation.surface_annotations must be an array.")
    return [item for item in raw_surfaces if isinstance(item, dict)]


def _normalize_functional_zone(raw_zone: Dict[str, Any], *, index: int) -> Dict[str, Any]:
    zone_id = str(raw_zone.get("id") or raw_zone.get("feature_id") or f"functional_zone_{index + 1:02d}").strip()
    if not zone_id:
        raise TemplatePatchError(f"template_patch.operations[{index}].zone.id is required.")
    kind = str(raw_zone.get("kind") or "plaza").strip().lower()
    if kind not in VALID_FUNCTIONAL_ZONE_KINDS:
        raise TemplatePatchError(f"template_patch.operations[{index}].zone.kind must be one of {sorted(VALID_FUNCTIONAL_ZONE_KINDS)}.")
    points = raw_zone.get("points") or []
    if not isinstance(points, Sequence) or isinstance(points, (str, bytes)) or len(points) < 3:
        raise TemplatePatchError(f"template_patch.operations[{index}].zone.points must contain at least three points.")
    normalized_points = [_normalize_point(point, f"template_patch.operations[{index}].zone.points[{point_index}]") for point_index, point in enumerate(points)]
    furniture_instances = raw_zone.get("furniture_instances") or []
    if not isinstance(furniture_instances, Sequence) or isinstance(furniture_instances, (str, bytes)):
        raise TemplatePatchError(f"template_patch.operations[{index}].zone.furniture_instances must be an array.")
    return {
        "id": zone_id,
        "label": str(raw_zone.get("label") or zone_id).strip(),
        "kind": kind,
        "points": normalized_points,
        "furniture_instances": [
            _normalize_zone_furniture(item, f"template_patch.operations[{index}].zone.furniture_instances[{furniture_index}]")
            for furniture_index, item in enumerate(furniture_instances)
        ],
    }


def _normalize_surface_annotation(raw_surface: Dict[str, Any], *, index: int) -> Dict[str, Any]:
    surface_id = str(raw_surface.get("id") or raw_surface.get("feature_id") or f"surface_annotation_{index + 1:02d}").strip()
    if not surface_id:
        raise TemplatePatchError(f"template_patch.operations[{index}].surface.id is required.")
    kind = str(raw_surface.get("kind") or "paving_zone").strip().lower()
    if kind not in VALID_SURFACE_ANNOTATION_KINDS:
        raise TemplatePatchError(f"template_patch.operations[{index}].surface.kind must be one of {sorted(VALID_SURFACE_ANNOTATION_KINDS)}.")
    surface_role = str(raw_surface.get("surface_role") or "colored_pavement").strip().lower()
    if surface_role not in VALID_SURFACE_ROLES:
        raise TemplatePatchError(f"template_patch.operations[{index}].surface.surface_role must be one of {sorted(VALID_SURFACE_ROLES)}.")
    centerline_id = str(raw_surface.get("centerline_id") or "").strip()
    if not centerline_id:
        raise TemplatePatchError(f"template_patch.operations[{index}].surface.centerline_id is required.")
    material = raw_surface.get("material") or {}
    if not isinstance(material, Mapping):
        raise TemplatePatchError(f"template_patch.operations[{index}].surface.material must be an object when provided.")
    return {
        "id": surface_id,
        "label": str(raw_surface.get("label") or surface_id).strip(),
        "kind": kind,
        "surface_role": surface_role,
        "centerline_id": centerline_id,
        "station_start_m": _finite_float(raw_surface.get("station_start_m"), f"template_patch.operations[{index}].surface.station_start_m"),
        "station_end_m": _finite_float(raw_surface.get("station_end_m"), f"template_patch.operations[{index}].surface.station_end_m"),
        "lateral_start_m": _finite_float(raw_surface.get("lateral_start_m"), f"template_patch.operations[{index}].surface.lateral_start_m"),
        "lateral_end_m": _finite_float(raw_surface.get("lateral_end_m"), f"template_patch.operations[{index}].surface.lateral_end_m"),
        "material": dict(material),
    }


def _normalize_point(value: Any, label: str) -> Dict[str, float]:
    if not isinstance(value, Mapping):
        raise TemplatePatchError(f"{label} must be an object with x/y.")
    return {
        "x": _finite_float(value.get("x"), f"{label}.x"),
        "y": _finite_float(value.get("y"), f"{label}.y"),
    }


def _normalize_zone_furniture(value: Any, label: str) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TemplatePatchError(f"{label} must be an object.")
    kind = str(value.get("kind") or "").strip().lower()
    if kind not in VALID_FURNITURE_KINDS:
        raise TemplatePatchError(f"{label}.kind must be one of {sorted(VALID_FURNITURE_KINDS)}.")
    return {
        "instance_id": str(value.get("instance_id") or f"{kind}_01").strip(),
        "kind": kind,
        "x_px": _finite_float(value.get("x_px", value.get("x")), f"{label}.x_px"),
        "y_px": _finite_float(value.get("y_px", value.get("y")), f"{label}.y_px"),
        "yaw_deg": (
            _finite_float(value.get("yaw_deg"), f"{label}.yaw_deg")
            if value.get("yaw_deg") is not None
            else None
        ),
    }


def _validate_annotation(annotation: Mapping[str, Any]) -> None:
    try:
        parse_reference_annotation(annotation)
    except ValueError as exc:
        raise TemplatePatchError(str(exc)) from exc


def _build_patch_summary(
    annotation: Mapping[str, Any],
    patch: Mapping[str, Any],
    applied_operations: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    variant_id = str(patch.get("variant_id") or "").strip()
    summary = {
        "schema_version": TEMPLATE_PATCH_SCHEMA_VERSION,
        "base_plan_id": str(annotation.get("plan_id") or ""),
        "variant_id": variant_id or None,
        "description": str(patch.get("description") or "").strip() or None,
        "operation_count": len(tuple(patch.get("operations") or ())),
        "applied_operation_count": len(tuple(applied_operations)),
        "applied_operations": [dict(item) for item in applied_operations],
        "centerline_count": len(_centerlines(annotation)),
        "functional_zone_count": len(_functional_zone_records(annotation)),
        "surface_annotation_count": len(_surface_annotation_records(annotation)),
    }
    return summary


def _attach_patch_metadata(annotation: Dict[str, Any], summary: Mapping[str, Any]) -> None:
    metadata = annotation.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["template_patch"] = dict(summary)
    annotation["metadata"] = metadata


def _next_strip_id(zone: str, kind: str, existing_ids: set[str]) -> str:
    stem = f"{zone}_{kind}".replace("-", "_")
    index = 1
    while f"{stem}_{index:02d}" in existing_ids:
        index += 1
    return f"{stem}_{index:02d}"


def _required_text(operation: Mapping[str, Any], key: str, label: str) -> str:
    value = str(operation.get(key) or "").strip()
    if not value:
        raise TemplatePatchError(f"{label}.{key} is required.")
    return value


def _finite_float(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise TemplatePatchError(f"{label} must be a finite number.") from exc
    if not math.isfinite(parsed):
        raise TemplatePatchError(f"{label} must be a finite number.")
    return float(parsed)


__all__ = [
    "DEFAULT_TEMPLATE_PATCH_CONSTRAINTS",
    "TEMPLATE_PATCH_SCHEMA_VERSION",
    "TemplatePatchApplication",
    "TemplatePatchError",
    "apply_template_patch",
]
