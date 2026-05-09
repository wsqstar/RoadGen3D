"""Compile simple semantic scenario edits into graph-template patches.

This module intentionally keeps the first version deterministic.  LLMs may
produce the small semantic edit schema later, but the final template_patch is
always produced here and validated through apply_template_patch.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Sequence

from .graph_templates import load_graph_template_annotation_payload
from .json_safe import make_json_safe
from .template_patch import TEMPLATE_PATCH_SCHEMA_VERSION, apply_template_patch

SEMANTIC_SCENARIO_EDIT_SCHEMA_VERSION = "roadgen3d_semantic_scenario_edit_v1"

_FEATURE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "bus_stop": {
        "surface_kind": "transit_pad",
        "surface_role": "transit_pad",
        "length_min_m": 18.0,
        "length_max_m": 30.0,
        "length_fraction": 0.12,
        "width_m": 3.2,
        "lateral_anchor": "right_curbside",
        "material_preset": "transit_pad",
        "green_material_preset": "bus_lane_green",
    },
    "transit_pad": {
        "surface_kind": "transit_pad",
        "surface_role": "transit_pad",
        "length_min_m": 16.0,
        "length_max_m": 30.0,
        "length_fraction": 0.12,
        "width_m": 3.0,
        "lateral_anchor": "right_curbside",
        "material_preset": "transit_pad",
        "green_material_preset": "bus_lane_green",
    },
    "colored_pavement": {
        "surface_kind": "colored_pavement",
        "surface_role": "colored_pavement",
        "length_min_m": 12.0,
        "length_max_m": 40.0,
        "length_fraction": 0.18,
        "width_m": 2.4,
        "lateral_anchor": "right_sidewalk",
        "material_preset": "colored_pavement",
        "green_material_preset": "colored_pavement_green",
    },
    "bike_lane": {
        "surface_kind": "colored_pavement",
        "surface_role": "bike_lane",
        "length_min_m": 18.0,
        "length_max_m": 60.0,
        "length_fraction": 0.35,
        "width_m": 1.8,
        "lateral_anchor": "right_curbside",
        "material_preset": "bike_lane",
        "green_material_preset": "bike_lane",
    },
    "bus_lane": {
        "surface_kind": "bus_lane_widening",
        "surface_role": "bus_lane",
        "length_min_m": 18.0,
        "length_max_m": 60.0,
        "length_fraction": 0.25,
        "width_m": 3.2,
        "lateral_anchor": "right_curbside",
        "material_preset": "bus_lane_green",
        "green_material_preset": "bus_lane_green",
    },
    "safety_island": {
        "surface_kind": "safety_island",
        "surface_role": "safety_island",
        "length_min_m": 8.0,
        "length_max_m": 24.0,
        "length_fraction": 0.10,
        "width_m": 2.0,
        "lateral_anchor": "median",
        "material_preset": "safety_island_concrete",
    },
    "median_green": {
        "surface_kind": "safety_island",
        "surface_role": "median_green",
        "length_min_m": 16.0,
        "length_max_m": 80.0,
        "length_fraction": 0.35,
        "width_m": 2.4,
        "lateral_anchor": "median",
        "material_preset": "median_green",
        "green_material_preset": "median_green",
    },
}


@dataclass(frozen=True)
class _ResolvedRoad:
    centerline_id: str
    length_m: float
    center_width_m: float
    left_width_m: float
    right_width_m: float


def draft_semantic_scenario_variant(
    *,
    prompt: str,
    graph_template_id: str = "hkust_gz_gate",
    semantic_payload: Mapping[str, Any] | None = None,
    citations: Sequence[Mapping[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Create a temporary scenario design variant from a prompt or semantic edit payload."""

    prompt_text = str(prompt or "").strip()
    if not prompt_text and not semantic_payload:
        raise ValueError("prompt is required when semantic_payload is not provided.")
    annotation = load_graph_template_annotation_payload(graph_template_id)
    payload = _normalize_semantic_payload(semantic_payload) if semantic_payload else _parse_prompt(prompt_text)
    road = _resolve_road(annotation, payload)
    operations: list[Dict[str, Any]] = []
    resolved_defaults: list[Dict[str, Any]] = []
    warnings: list[str] = []
    semantic_edits = payload["edits"]
    for index, edit in enumerate(semantic_edits, start=1):
        operation, defaults, edit_warnings = _compile_edit(edit, road=road, edit_index=index)
        operations.append(operation)
        resolved_defaults.append(defaults)
        warnings.extend(edit_warnings)
    scenario_id = f"draft_semantic_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    template_patch = {
        "schema_version": TEMPLATE_PATCH_SCHEMA_VERSION,
        "variant_id": scenario_id,
        "description": prompt_text or "Semantic scenario variant",
        "operations": operations,
    }
    application = apply_template_patch(annotation, template_patch)
    response = {
        "scenario_id": scenario_id,
        "title_zh": _compact_title(prompt_text),
        "scenario_type": "semantic_prompt_variant",
        "graph_template_id": graph_template_id,
        "prompt": prompt_text,
        "semantic_edits": semantic_edits,
        "resolved_defaults": resolved_defaults,
        "template_patch": template_patch,
        "annotation": application.annotation,
        "annotation_summary": application.summary,
        "citations": [_citation_summary(item) for item in (citations or ())],
        "warnings": warnings,
    }
    return make_json_safe(response)


def _normalize_semantic_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    schema_version = str(payload.get("schema_version") or SEMANTIC_SCENARIO_EDIT_SCHEMA_VERSION).strip()
    if schema_version != SEMANTIC_SCENARIO_EDIT_SCHEMA_VERSION:
        raise ValueError(f"Unsupported semantic edit schema_version: {schema_version}")
    edits = payload.get("edits")
    if not isinstance(edits, Sequence) or isinstance(edits, (str, bytes)) or not edits:
        raise ValueError("semantic edit payload must contain a non-empty edits array.")
    normalized = []
    for item in edits:
        if not isinstance(item, Mapping):
            raise ValueError("semantic edit items must be objects.")
        normalized.append(dict(item))
    return {
        "schema_version": SEMANTIC_SCENARIO_EDIT_SCHEMA_VERSION,
        "edits": normalized,
    }


def _parse_prompt(prompt: str) -> Dict[str, Any]:
    text = prompt.lower()
    feature = _infer_feature(text)
    anchor, center_fraction = _infer_longitudinal(text)
    lateral_anchor = _infer_lateral(text, feature)
    style: Dict[str, Any] = {}
    if any(token in text for token in ("绿", "绿色", "green")):
        style["pavement_color"] = "green"
    length_m = _extract_length_m(text)
    edit: Dict[str, Any] = {
        "action": "add",
        "feature": feature,
        "road_selector": {"kind": "primary"},
        "longitudinal": {
            "anchor": anchor,
            "center_fraction": center_fraction,
        },
        "lateral": {"anchor": lateral_anchor},
        "style": style,
    }
    if length_m is not None:
        edit["longitudinal"]["length_m"] = length_m
    return {
        "schema_version": SEMANTIC_SCENARIO_EDIT_SCHEMA_VERSION,
        "edits": [edit],
    }


def _infer_feature(text: str) -> str:
    if any(token in text for token in ("公交车道", "bus lane")):
        return "bus_lane"
    if any(token in text for token in ("公交", "bus stop", "bus_stop", "transit stop")):
        return "bus_stop"
    if any(token in text for token in ("自行车", "bike", "cycle")):
        return "bike_lane"
    if any(token in text for token in ("安全岛", "safety island", "refuge")):
        return "safety_island"
    if any(token in text for token in ("绿化隔离", "绿化带", "median green", "green median")):
        return "median_green"
    if any(token in text for token in ("彩色", "颜色", "colored", "colour", "铺装", "pavement")):
        return "colored_pavement"
    return "colored_pavement"


def _infer_longitudinal(text: str) -> tuple[str, float]:
    fraction_match = re.search(r"(?<!\d)(0(?:\.\d+)?|1(?:\.0+)?)(?!\d)", text)
    percent_match = re.search(r"(\d{1,3})\s*%", text)
    if fraction_match:
        value = _clamp(float(fraction_match.group(1)), 0.0, 1.0)
        return "fraction", value
    if percent_match:
        value = _clamp(float(percent_match.group(1)) / 100.0, 0.0, 1.0)
        return "fraction", value
    if any(token in text for token in ("起点", "开头", "前段", "begin", "start")):
        return "start", 0.15
    if any(token in text for token in ("终点", "末端", "后段", "end")):
        return "end", 0.85
    return "middle", 0.5


def _infer_lateral(text: str, feature: str) -> str:
    if any(token in text for token in ("右", "right")):
        return "right_curbside"
    if any(token in text for token in ("左", "left")):
        return "left_curbside"
    if any(token in text for token in ("中央", "隔离带", "median", "中间")) and feature in {"safety_island", "median_green"}:
        return "median"
    return str(_FEATURE_DEFAULTS.get(feature, {}).get("lateral_anchor") or "right_curbside")


def _extract_length_m(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:m|米|meter|meters)", text)
    if not match:
        return None
    value = float(match.group(1))
    return value if value > 0.0 else None


def _resolve_road(annotation: Mapping[str, Any], payload: Mapping[str, Any]) -> _ResolvedRoad:
    edits = payload.get("edits") or []
    wanted_id = ""
    if edits and isinstance(edits[0], Mapping):
        selector = edits[0].get("road_selector")
        if isinstance(selector, Mapping):
            wanted_id = str(selector.get("road_id") or selector.get("centerline_id") or "").strip()
    centerlines = [item for item in annotation.get("centerlines", []) or [] if isinstance(item, Mapping)]
    if not centerlines:
        raise ValueError("graph template contains no centerlines.")
    pixels_per_meter = _positive_float(annotation.get("pixels_per_meter"), 1.0)
    candidates = []
    for centerline in centerlines:
        centerline_id = str(centerline.get("id") or centerline.get("feature_id") or "").strip()
        length_m = _centerline_length_m(centerline, pixels_per_meter)
        if centerline_id == wanted_id:
            candidates = [(centerline, centerline_id, length_m)]
            break
        candidates.append((centerline, centerline_id, length_m))
    centerline, centerline_id, length_m = max(candidates, key=lambda item: item[2])
    widths = _cross_section_widths(centerline)
    return _ResolvedRoad(
        centerline_id=centerline_id,
        length_m=max(length_m, 1.0),
        center_width_m=widths["center"],
        left_width_m=widths["left"],
        right_width_m=widths["right"],
    )


def _compile_edit(edit: Mapping[str, Any], *, road: _ResolvedRoad, edit_index: int) -> tuple[Dict[str, Any], Dict[str, Any], list[str]]:
    action = str(edit.get("action") or "add").strip().lower()
    if action not in {"add", "modify", "update"}:
        raise ValueError(f"semantic_edits[{edit_index}].action must be add/modify/update.")
    feature = str(edit.get("feature") or "colored_pavement").strip().lower()
    if feature not in _FEATURE_DEFAULTS:
        raise ValueError(f"Unsupported semantic feature: {feature}")
    defaults = dict(_FEATURE_DEFAULTS[feature])
    longitudinal = edit.get("longitudinal") if isinstance(edit.get("longitudinal"), Mapping) else {}
    lateral = edit.get("lateral") if isinstance(edit.get("lateral"), Mapping) else {}
    style = edit.get("style") if isinstance(edit.get("style"), Mapping) else {}
    center_fraction = _resolve_center_fraction(longitudinal)
    span_fraction = _clamp(_finite_float(longitudinal.get("span_fraction"), 0.0), 0.0, 1.0)
    default_length_m = (
        road.length_m * span_fraction
        if span_fraction > 0.0
        else _clamp(
            road.length_m * float(defaults["length_fraction"]),
            float(defaults["length_min_m"]),
            float(defaults["length_max_m"]),
        )
    )
    length_m = _positive_float(
        longitudinal.get("length_m"),
        default_length_m,
    )
    length_m = min(length_m, max(road.length_m * 0.85, 1.0))
    station_start_m = _clamp(center_fraction * road.length_m - length_m * 0.5, 0.0, max(road.length_m - length_m, 0.0))
    station_end_m = min(station_start_m + length_m, road.length_m)
    width_m = _positive_float(lateral.get("width_m"), float(defaults["width_m"]))
    lateral_anchor = str(lateral.get("anchor") or defaults.get("lateral_anchor") or "right_curbside").strip().lower()
    lateral_start_m, lateral_end_m = _resolve_lateral_span(lateral_anchor, width_m, road)
    material_preset = str(defaults.get("material_preset") or defaults["surface_role"])
    if str(style.get("pavement_color") or "").strip().lower() in {"green", "绿", "绿色"}:
        material_preset = str(defaults.get("green_material_preset") or material_preset)
    near = str(longitudinal.get("near") or "").strip().lower()
    warnings: list[str] = []
    if near:
        warnings.append(f"near={near} used heuristic placement in semantic_scenario_edit_v1; explicit POI snapping is not enabled yet.")
    surface_id = f"semantic_{feature}_{edit_index:02d}"
    surface = {
        "id": surface_id,
        "label": str(edit.get("label") or surface_id),
        "kind": defaults["surface_kind"],
        "surface_role": defaults["surface_role"],
        "centerline_id": road.centerline_id,
        "station_start_m": round(float(station_start_m), 3),
        "station_end_m": round(float(station_end_m), 3),
        "lateral_start_m": round(float(lateral_start_m), 3),
        "lateral_end_m": round(float(lateral_end_m), 3),
        "material": {"preset": material_preset},
    }
    operation = {
        "op": "upsert_surface_annotation",
        "surface": surface,
    }
    resolved = {
        "edit_index": edit_index,
        "feature": feature,
        "road_id": road.centerline_id,
        "road_length_m": round(float(road.length_m), 3),
        "center_fraction": round(float(center_fraction), 4),
        "station_start_m": surface["station_start_m"],
        "station_end_m": surface["station_end_m"],
        "lateral_anchor": lateral_anchor,
        "lateral_start_m": surface["lateral_start_m"],
        "lateral_end_m": surface["lateral_end_m"],
        "length_m": round(float(station_end_m - station_start_m), 3),
        "width_m": round(float(lateral_end_m - lateral_start_m), 3),
        "material_preset": material_preset,
        "span_fraction": round(float(span_fraction), 4),
        "near": near,
        "default_source": "built_in_semantic_defaults_v1",
    }
    return operation, resolved, warnings


def _resolve_center_fraction(longitudinal: Mapping[str, Any]) -> float:
    if "center_fraction" in longitudinal:
        return _clamp(_finite_float(longitudinal.get("center_fraction"), 0.5), 0.0, 1.0)
    near = str(longitudinal.get("near") or "").strip().lower()
    if near in {"entrance", "gate", "entry"}:
        return 0.18
    if near in {"junction", "crossing", "intersection", "school"}:
        return 0.5
    anchor = str(longitudinal.get("anchor") or "").strip().lower()
    if anchor in {"start", "begin", "front", "前段", "起点"}:
        return 0.15
    if anchor in {"end", "back", "rear", "后段", "终点", "末端"}:
        return 0.85
    return 0.5


def _resolve_lateral_span(anchor: str, width_m: float, road: _ResolvedRoad) -> tuple[float, float]:
    center_half = max(road.center_width_m * 0.5, 0.5)
    width = max(float(width_m), 0.1)
    if anchor in {"left", "left_curbside", "left_sidewalk"}:
        return center_half, center_half + min(width, max(road.left_width_m, width))
    if anchor in {"right", "right_curbside", "right_sidewalk"}:
        return -(center_half + min(width, max(road.right_width_m, width))), -center_half
    if anchor in {"center", "median", "road_center"}:
        return -width * 0.5, width * 0.5
    lane_index_match = re.search(r"(-?\d+)", anchor)
    if lane_index_match:
        index = int(lane_index_match.group(1))
        center = index * width
        return center - width * 0.5, center + width * 0.5
    return -(center_half + width), -center_half


def _centerline_length_m(centerline: Mapping[str, Any], pixels_per_meter: float) -> float:
    points = centerline.get("points") or []
    coords = [_point_xy(point) for point in points if _point_xy(point) is not None]
    if len(coords) < 2:
        return 1.0
    length_px = 0.0
    for start, end in zip(coords, coords[1:]):
        length_px += math.hypot(float(end[0]) - float(start[0]), float(end[1]) - float(start[1]))
    return length_px / max(pixels_per_meter, 1e-6)


def _point_xy(point: Any) -> tuple[float, float] | None:
    if isinstance(point, Mapping):
        if "x" in point and "y" in point:
            return float(point["x"]), float(point["y"])
        if "x_px" in point and "y_px" in point:
            return float(point["x_px"]), float(point["y_px"])
    if isinstance(point, Sequence) and not isinstance(point, (str, bytes)) and len(point) >= 2:
        return float(point[0]), float(point[1])
    return None


def _cross_section_widths(centerline: Mapping[str, Any]) -> Dict[str, float]:
    widths = {"left": 0.0, "center": 0.0, "right": 0.0}
    for strip in centerline.get("cross_section_strips", []) or []:
        if not isinstance(strip, Mapping):
            continue
        zone = str(strip.get("zone") or "center").strip().lower()
        if zone not in widths:
            continue
        widths[zone] += max(_positive_float(strip.get("width_m"), 0.0), 0.0)
    if widths["center"] <= 0.0:
        widths["center"] = _positive_float(centerline.get("road_width_m"), 12.0)
    return widths


def _positive_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if not math.isfinite(parsed) or parsed <= 0.0:
        return float(fallback)
    return parsed


def _finite_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if not math.isfinite(parsed):
        return float(fallback)
    return parsed


def _clamp(value: float, low: float, high: float) -> float:
    return max(float(low), min(float(high), float(value)))


def _compact_title(prompt: str) -> str:
    text = str(prompt or "").strip()
    return text[:48] if text else "Draft semantic variant"


def _citation_summary(item: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "doc_id": str(item.get("doc_id") or ""),
        "chunk_id": str(item.get("chunk_id") or ""),
        "source": str(item.get("knowledge_source") or item.get("source") or ""),
        "title": str(item.get("title") or ""),
        "text": str(item.get("text") or "")[:500],
    }


__all__ = [
    "SEMANTIC_SCENARIO_EDIT_SCHEMA_VERSION",
    "draft_semantic_scenario_variant",
]
