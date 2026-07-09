#!/usr/bin/env python3
"""Bridge between RoadPen scenes and RoadGen3D reference annotations.

Commands:
  python tools/bridge/format_bridge.py roadpen-to-roadgen3d <input.json> -o <output.json> [--mode strict|preview|repair]
  python tools/bridge/format_bridge.py roadgen3d-to-roadpen <input.json> -o <output.json> [--mode strict|preview|repair]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from roadgen3d.reference_annotation import (  # type: ignore
    ANNOTATION_SCHEMA_VERSION,
    DEFAULT_DRIVE_LANE_WIDTH_M,
    ReferenceAnnotation,
    parse_reference_annotation,
)

ROADPEN_VERSION = "1.0.0"
ROADPEN_DEFAULT_SCALE_PX_PER_M = 20.0
ROADPEN_DEFAULT_PROFILE = {
    "id": "default",
    "name": "bridge_default",
    "carriagewayWidth": 24.0,
    "facilityWidth": 4.0,
    "sidewalkWidth": 8.0,
    "clearanceWidth": 4.0,
}

SUPPORTED_MODES = ("strict", "preview", "repair")
MAX_TOLERANCE_M = 1e-6
POINT_DEDUPE_GRID = 1000.0


@dataclass(frozen=True)
class RoadPenNode:
    id: str
    x: float
    y: float


@dataclass(frozen=True)
class RoadPenProfile:
    id: str
    name: str
    carriagewayWidth: float
    facilityWidth: float
    sidewalkWidth: float
    clearanceWidth: float


@dataclass(frozen=True)
class RoadPenEdge:
    id: str
    from_id: str
    to_id: str
    control_points: Sequence[Tuple[float, float]]
    geomType: str = "spline"
    endMode: str = "free"
    layer: int = 0
    profileId: str = ROADPEN_DEFAULT_PROFILE["id"]


@dataclass(frozen=True)
class RoadPenScene:
    version: str
    units: str
    scalePxPerM: float
    nodes: Sequence[RoadPenNode]
    edges: Sequence[RoadPenEdge]
    profiles: Sequence[RoadPenProfile]
    source_meta: Mapping[str, Any]


@dataclass
class BridgeSummary:
    mode: str
    warnings: List[str]
    losses: List[str]
    repaired: List[str]
    converted_count: Dict[str, int]


# =========================
# Generic helpers
# =========================

def _read_json(path: Path) -> Any:
    raw = path.read_text(encoding="utf-8")
    return json.loads(raw)


def _as_str(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        text = value.strip()
        return text or default
    return default


def _as_float(value: Any, *, label: str = "value", default: float | None = None) -> float:
    if value is None:
        if default is None:
            raise ValueError(f"{label} must be a finite number")
        return float(default)
    parsed = float(value)
    if not math.isfinite(parsed):
        if default is None:
            raise ValueError(f"{label} must be a finite number")
        return float(default)
    return parsed


def _as_int(value: Any, *, label: str = "value", default: int | None = None) -> int:
    if value is None:
        if default is None:
            raise ValueError(f"{label} must be a finite integer")
        return int(default)
    return int(value)


def _as_point(value: Any) -> Tuple[float, float] | None:
    if not isinstance(value, Mapping):
        return None
    if "x" not in value or "y" not in value:
        return None
    try:
        return (_as_float(value.get("x"), label="x"), _as_float(value.get("y"), label="y"))
    except Exception:
        return None


def _as_point_list(value: Any) -> List[Tuple[float, float]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    points: List[Tuple[float, float]] = []
    for item in value:
        point = _as_point(item)
        if point is not None:
            points.append(point)
    return points


def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _dedupe_adjacent(points: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    if not points:
        return []
    out: List[Tuple[float, float]] = []
    for point in points:
        candidate = (float(point[0]), float(point[1]))
        if not out or _distance(out[-1], candidate) > MAX_TOLERANCE_M:
            out.append(candidate)
    return out


def _node_key(point: Tuple[float, float]) -> Tuple[int, int]:
    return (int(round(float(point[0]) * POINT_DEDUPE_GRID)), int(round(float(point[1]) * POINT_DEDUPE_GRID)))


def _infer_junction_kind(degree: int) -> str:
    if degree >= 4:
        return "cross_junction"
    if degree == 3:
        return "t_junction"
    return "intersection"


def _coerce_conversion_mode(value: str) -> str:
    if value not in SUPPORTED_MODES:
        raise argparse.ArgumentTypeError(f"mode must be one of {SUPPORTED_MODES}")
    return value


# =========================
# RoadPen normalization
# =========================

def _normalize_roadpen_scene(payload: Mapping[str, Any], mode: str) -> tuple[RoadPenScene, List[str]]:
    strict = mode == "strict"
    warnings: List[str] = []

    scene_raw = payload.get("scene") if isinstance(payload.get("scene"), Mapping) else payload
    if not isinstance(scene_raw, Mapping):
        raise ValueError("RoadPen input must be an object")

    version = _as_str(scene_raw.get("version"), ROADPEN_VERSION)
    if version != ROADPEN_VERSION:
        warnings.append(f"RoadPen version '{version}' normalized to '{ROADPEN_VERSION}'")

    raw_scale = scene_raw.get("scalePxPerM")
    if raw_scale is None:
        if strict:
            raise ValueError("scalePxPerM is required in strict mode")
        scale_px_per_m = ROADPEN_DEFAULT_SCALE_PX_PER_M
    else:
        try:
            scale_px_per_m = float(raw_scale)
        except Exception:
            if strict:
                raise ValueError("scalePxPerM must be numeric in strict mode")
            warnings.append("scalePxPerM invalid; fallback to 20.0")
            scale_px_per_m = ROADPEN_DEFAULT_SCALE_PX_PER_M
    if scale_px_per_m <= 0:
        if strict:
            raise ValueError("scalePxPerM must be > 0")
        warnings.append("scalePxPerM invalid; fallback to 20.0")
        scale_px_per_m = ROADPEN_DEFAULT_SCALE_PX_PER_M

    profiles: List[RoadPenProfile] = []
    raw_profiles = scene_raw.get("profiles")
    if isinstance(raw_profiles, Sequence) and not isinstance(raw_profiles, (str, bytes)):
        for index, item in enumerate(raw_profiles):
            if not isinstance(item, Mapping):
                continue
            profile_id = _as_str(item.get("id"), f"profile_{index + 1:03d}")
            profile_name = _as_str(item.get("name"), profile_id)
            profiles.append(
                RoadPenProfile(
                    id=profile_id,
                    name=profile_name,
                    carriagewayWidth=_as_float(item.get("carriagewayWidth"), default=ROADPEN_DEFAULT_PROFILE["carriagewayWidth"]),
                    facilityWidth=_as_float(item.get("facilityWidth"), default=ROADPEN_DEFAULT_PROFILE["facilityWidth"]),
                    sidewalkWidth=_as_float(item.get("sidewalkWidth"), default=ROADPEN_DEFAULT_PROFILE["sidewalkWidth"]),
                    clearanceWidth=_as_float(item.get("clearanceWidth"), default=ROADPEN_DEFAULT_PROFILE["clearanceWidth"]),
                )
            )
    else:
        warnings.append("RoadPen profiles missing; inserted default profile")

    if not profiles:
        profiles = [
            RoadPenProfile(
                id=ROADPEN_DEFAULT_PROFILE["id"],
                name=ROADPEN_DEFAULT_PROFILE["name"],
                carriagewayWidth=ROADPEN_DEFAULT_PROFILE["carriagewayWidth"],
                facilityWidth=ROADPEN_DEFAULT_PROFILE["facilityWidth"],
                sidewalkWidth=ROADPEN_DEFAULT_PROFILE["sidewalkWidth"],
                clearanceWidth=ROADPEN_DEFAULT_PROFILE["clearanceWidth"],
            )
        ]
    elif ROADPEN_DEFAULT_PROFILE["id"] not in {p.id for p in profiles}:
        profiles.insert(
            0,
            RoadPenProfile(
                id=ROADPEN_DEFAULT_PROFILE["id"],
                name=ROADPEN_DEFAULT_PROFILE["name"],
                carriagewayWidth=ROADPEN_DEFAULT_PROFILE["carriagewayWidth"],
                facilityWidth=ROADPEN_DEFAULT_PROFILE["facilityWidth"],
                sidewalkWidth=ROADPEN_DEFAULT_PROFILE["sidewalkWidth"],
                clearanceWidth=ROADPEN_DEFAULT_PROFILE["clearanceWidth"],
            ),
        )
    profile_map: Dict[str, RoadPenProfile] = {item.id: item for item in profiles}

    raw_nodes = scene_raw.get("nodes")
    if not isinstance(raw_nodes, Sequence) or isinstance(raw_nodes, (str, bytes)):
        raise ValueError("roadpen.nodes must be an array")

    nodes: List[RoadPenNode] = []
    for index, raw_node in enumerate(raw_nodes):
        if not isinstance(raw_node, Mapping):
            continue
        node_id = _as_str(raw_node.get("id"), f"node_{index + 1:04d}")
        point = _as_point(raw_node)
        if point is None:
            warnings.append(f"skip invalid node {node_id}")
            continue
        nodes.append(RoadPenNode(id=node_id, x=float(point[0]), y=float(point[1])))

    if strict and not nodes:
        raise ValueError("No valid roadpen nodes")

    raw_edges = scene_raw.get("edges")
    if not isinstance(raw_edges, Sequence) or isinstance(raw_edges, (str, bytes)):
        raise ValueError("roadpen.edges must be an array")

    used_ids = set[str]()
    edges: List[RoadPenEdge] = []
    for index, raw_edge in enumerate(raw_edges):
        if not isinstance(raw_edge, Mapping):
            continue
        edge_id = _as_str(raw_edge.get("id"), f"edge_{index + 1:04d}")
        from_id = _as_str(raw_edge.get("from"), "")
        to_id = _as_str(raw_edge.get("to"), "")
        if not from_id or not to_id:
            if strict:
                raise ValueError(f"edge {edge_id} missing from/to")
            warnings.append(f"skip edge {edge_id}: missing from/to")
            continue

        control_points = _as_point_list(raw_edge.get("controlPoints"))
        if len(control_points) < 2:
            if strict:
                raise ValueError(f"edge {edge_id} controlPoints < 2")
            warnings.append(f"edge {edge_id} controlPoints < 2, repaired with duplicated endpoints")
            # keep empty list; fixed later after endpoint check

        profile_id = _as_str(raw_edge.get("profileId"), ROADPEN_DEFAULT_PROFILE["id"])
        if profile_id not in profile_map:
            warnings.append(f"edge {edge_id} profile '{profile_id}' missing; fallback to default")
            profile_id = ROADPEN_DEFAULT_PROFILE["id"]

        geom_type = _as_str(raw_edge.get("geomType"), "spline")
        if geom_type not in {"polyline", "spline"}:
            geom_type = "spline"
        end_mode = _as_str(raw_edge.get("endMode"), "free")
        if end_mode not in {"free", "closed"}:
            end_mode = "free"

        if edge_id in used_ids:
            if strict:
                raise ValueError(f"duplicate edge id '{edge_id}'")
            suffix = 1
            new_id = edge_id
            while new_id in used_ids:
                new_id = f"{edge_id}_{suffix}"
                suffix += 1
            edge_id = new_id
            warnings.append(f"duplicate edge id normalized to '{edge_id}'")
        used_ids.add(edge_id)

        edges.append(
            RoadPenEdge(
                id=edge_id,
                from_id=from_id,
                to_id=to_id,
                control_points=_dedupe_adjacent(control_points),
                geomType=geom_type,
                endMode=end_mode,
                layer=max(0, _as_int(raw_edge.get("layer"), default=0)),
                profileId=profile_id,
            )
        )

    if strict and not edges:
        raise ValueError("No valid roadpen edges")

    source_meta = {
        "plan_id": _as_str(payload.get("plan_id"), ""),
        "image_path": _as_str(payload.get("image_path"), ""),
        "image_width_px": payload.get("image_width_px") if isinstance(payload.get("image_width_px"), int) else None,
        "image_height_px": payload.get("image_height_px") if isinstance(payload.get("image_height_px"), int) else None,
    }

    if strict:
        if source_meta["image_width_px"] is None or source_meta["image_width_px"] <= 0:
            raise ValueError("image_width_px is required in strict mode")
        if source_meta["image_height_px"] is None or source_meta["image_height_px"] <= 0:
            raise ValueError("image_height_px is required in strict mode")
        if not source_meta["image_path"]:
            warnings.append("strict mode: missing image_path (recoverable by viewer metadata fallback)")

    return (
        RoadPenScene(
            version=ROADPEN_VERSION,
            units="px",
            scalePxPerM=float(scale_px_per_m),
            nodes=tuple(nodes),
            edges=tuple(edges),
            profiles=tuple(profiles),
            source_meta=source_meta,
        ),
        warnings,
    )


def _roadpen_profile_to_lanes(profile: RoadPenProfile, scale_px_per_m: float) -> Tuple[int, int, int, int, int]:
    carriageway_m = max(0.01, float(profile.carriagewayWidth) / max(float(scale_px_per_m), 1e-9))
    total = max(1, int(round(carriageway_m / max(float(DEFAULT_DRIVE_LANE_WIDTH_M), 0.1))))
    forward = max(1, int(math.ceil(total / 2.0)))
    reverse = max(0, total - forward)
    return forward, reverse, 0, 0, 0


def _roadpen_to_roadgen_annotation(scene: RoadPenScene, mode: str) -> tuple[Dict[str, Any], BridgeSummary]:
    strict = mode == "strict"
    warnings: List[str] = []
    losses: List[str] = [
        "RoadGen3D region/building_region/functional_zone/surface_annotation/roundabout/junction_composition/advanced_semantics",
        "RoadGen3D furniture details (stored only in profile_ext if needed)",
    ]
    repaired: List[str] = []

    node_map: Dict[str, Tuple[float, float]] = {n.id: (float(n.x), float(n.y)) for n in scene.nodes}
    profile_map: Dict[str, RoadPenProfile] = {p.id: p for p in scene.profiles}

    degree: Dict[str, int] = {}
    for edge in scene.edges:
        degree[edge.from_id] = degree.get(edge.from_id, 0) + 1
        degree[edge.to_id] = degree.get(edge.to_id, 0) + 1

    junction_by_node: Dict[str, str] = {}
    for node_id, deg in degree.items():
        if deg >= 3:
            junction_by_node[node_id] = f"junction_{node_id}"

    centerlines: List[Dict[str, Any]] = []
    for edge in scene.edges:
        start = node_map.get(edge.from_id)
        end = node_map.get(edge.to_id)
        if start is None or end is None:
            if strict:
                raise ValueError(f"edge {edge.id} missing endpoint node")
            warnings.append(f"edge {edge.id} missing endpoint node, skipped")
            continue

        path = _dedupe_adjacent((start, *edge.control_points, end))
        if len(path) < 2:
            if strict:
                raise ValueError(f"edge {edge.id} has invalid geometry")
            warnings.append(f"edge {edge.id} geometry invalid, skipped")
            continue

        # repair short control-point list for non-strict modes
        cp_count_before = len(path)
        if cp_count_before == 2:
            path = [path[0], path[1]]
        if cp_count_before != len(path) and mode in {"preview", "repair"}:
            repaired.append(f"edge {edge.id}: normalized control points")

        profile = profile_map.get(edge.profileId, scene.profiles[0])
        forward, reverse, bike, bus, parking = _roadpen_profile_to_lanes(profile, scene.scalePxPerM)
        carriageway_m = max(0.2, float(profile.carriagewayWidth) / max(scene.scalePxPerM, 1e-9))
        facility_m = max(0.0, float(profile.facilityWidth) / max(scene.scalePxPerM, 1e-9))
        sidewalk_m = max(0.0, float(profile.sidewalkWidth) / max(scene.scalePxPerM, 1e-9))
        clearance_m = max(0.0, float(profile.clearanceWidth) / max(scene.scalePxPerM, 1e-9))

        centerlines.append(
            {
                "id": edge.id,
                "label": edge.id,
                "road_width_m": max(1.0, carriageway_m + 0.6 * facility_m + 0.2 * sidewalk_m + clearance_m),
                "reference_width_px": float(profile.carriagewayWidth + profile.facilityWidth + profile.sidewalkWidth + profile.clearanceWidth),
                "forward_drive_lane_count": int(forward),
                "reverse_drive_lane_count": int(reverse),
                "bike_lane_count": int(bike),
                "bus_lane_count": int(bus),
                "parking_lane_count": int(parking),
                "highway_type": "roadpen_bridge",
                "cross_section_mode": "coarse",
                "cross_section_strips": [],
                "street_furniture_instances": [],
                "start_junction_id": _as_str(junction_by_node.get(edge.from_id), ""),
                "end_junction_id": _as_str(junction_by_node.get(edge.to_id), ""),
                "points": [{"x": float(x), "y": float(y)} for x, y in path],
            }
        )

    if not centerlines:
        if strict:
            raise ValueError("No valid centerlines for RoadGen3D output")
        warnings.append("No valid centerlines converted")

    junction_payload: List[Dict[str, Any]] = []
    for node_id, junction_id in junction_by_node.items():
        connected = [
            edge.id
            for edge in scene.edges
            if edge.from_id == node_id or edge.to_id == node_id
        ]
        if not connected:
            continue
        point = node_map.get(node_id)
        if point is None:
            continue
        junction_payload.append(
            {
                "id": junction_id,
                "label": junction_id,
                "kind": _infer_junction_kind(degree.get(node_id, 0)),
                "x": float(point[0]),
                "y": float(point[1]),
                "connected_centerline_ids": connected,
                "source_mode": "legacy_marker" if strict else "explicit",
                "crosswalk_depth_m": 3.0,
            }
        )

    control_points: List[Dict[str, Any]] = []
    if mode != "strict":
        for node in scene.nodes:
            control_points.append(
                {
                    "id": f"rp_node_{node.id}",
                    "label": node.id,
                    "kind": "control_point",
                    "x": float(node.x),
                    "y": float(node.y),
                }
            )

    image_width = _as_int(scene.source_meta.get("image_width_px"), default=0)
    image_height = _as_int(scene.source_meta.get("image_height_px"), default=0)
    all_x = [node.x for node in scene.nodes]
    all_y = [node.y for node in scene.nodes]
    if image_width <= 0:
        image_width = int(math.ceil((max(all_x) - min(all_x)) + 256.0)) if all_x else 1024
    if image_height <= 0:
        image_height = int(math.ceil((max(all_y) - min(all_y)) + 256.0)) if all_y else 1024

    payload = {
        "version": ANNOTATION_SCHEMA_VERSION,
        "plan_id": _as_str(scene.source_meta.get("plan_id"), "roadpen_bridge"),
        "image_path": _as_str(scene.source_meta.get("image_path"), ""),
        "image_width_px": max(1, image_width),
        "image_height_px": max(1, image_height),
        "pixels_per_meter": float(scene.scalePxPerM),
        "centerlines": centerlines,
        "junctions": junction_payload,
        "roundabouts": [],
        "control_points": control_points,
        "regions": [],
        "building_regions": [],
        "functional_zones": [],
        "surface_annotations": [],
        "station_strip_patches": [],
        "junction_compositions": [],
        "__bridge_meta": {
            "source_format": "roadpen_scene",
            "source_version": scene.version,
            "junction_count": len(junction_payload),
            "loss_profile": "preview-lossy" if mode != "strict" else "lossless-like",
        },
    }

    annotation = parse_reference_annotation(payload)
    if strict and annotation.version != ANNOTATION_SCHEMA_VERSION:
        raise RuntimeError("RoadGen3D conversion failed in strict mode")

    if mode == "strict":
        losses = []

    return payload, BridgeSummary(
        mode=mode,
        warnings=warnings,
        losses=losses,
        repaired=repaired,
        converted_count={
            "nodes": len(scene.nodes),
            "edges": len(scene.edges),
            "centerlines": len(centerlines),
            "junctions": len(junction_payload),
        },
    )


# =========================
# RoadGen3D -> RoadPen
# =========================

def _extract_annotation_payload(raw: Mapping[str, Any]) -> Dict[str, Any]:
    if "annotation" in raw and isinstance(raw["annotation"], Mapping):
        nested = raw["annotation"]
        if isinstance(nested, Mapping) and ("centerlines" in nested or "version" in nested):
            return dict(nested)
    return dict(raw)


def _repair_annotation_payload(raw: Mapping[str, Any], warnings: List[str]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "version": _as_str(raw.get("version"), ANNOTATION_SCHEMA_VERSION),
        "plan_id": _as_str(raw.get("plan_id"), "repair_bridge"),
        "image_path": _as_str(raw.get("image_path"), ""),
        "image_width_px": _as_int(raw.get("image_width_px"), default=1024),
        "image_height_px": _as_int(raw.get("image_height_px"), default=1024),
        "pixels_per_meter": _as_float(raw.get("pixels_per_meter"), default=ROADPEN_DEFAULT_SCALE_PX_PER_M),
        "centerlines": [],
        "junctions": [],
        "roundabouts": [],
        "control_points": [],
        "regions": [],
        "building_regions": [],
        "functional_zones": [],
        "surface_annotations": [],
        "station_strip_patches": [],
        "junction_compositions": [],
        "__bridge_meta": {
            "repair": "applied",
        },
    }

    centerline_raw = raw.get("centerlines")
    if not isinstance(centerline_raw, Sequence) or isinstance(centerline_raw, (str, bytes)):
        warnings.append("repair: centerlines invalid or missing, forced to empty")
    else:
        for index, item in enumerate(centerline_raw):
            if not isinstance(item, Mapping):
                warnings.append(f"repair: centerline[{index}] invalid item")
                continue
            fid = _as_str(item.get("id"), f"centerline_{index + 1:03d}")
            points = _as_point_list(item.get("points"))
            if len(points) < 2:
                warnings.append(f"repair: centerline {fid} has <2 points")
                continue
            payload["centerlines"].append(
                {
                    "id": fid,
                    "label": _as_str(item.get("label"), fid),
                    "points": [{"x": float(x), "y": float(y)} for x, y in points],
                    "road_width_m": _as_float(item.get("road_width_m"), default=8.0),
                    "forward_drive_lane_count": _as_int(item.get("forward_drive_lane_count"), default=1),
                    "reverse_drive_lane_count": _as_int(item.get("reverse_drive_lane_count"), default=1),
                    "bike_lane_count": _as_int(item.get("bike_lane_count"), default=0),
                    "bus_lane_count": _as_int(item.get("bus_lane_count"), default=0),
                    "parking_lane_count": _as_int(item.get("parking_lane_count"), default=0),
                    "cross_section_mode": _as_str(item.get("cross_section_mode"), "coarse"),
                    "cross_section_strips": [],
                    "street_furniture_instances": [],
                }
            )

    junctions_raw = raw.get("junctions")
    if isinstance(junctions_raw, Sequence) and not isinstance(junctions_raw, (str, bytes)):
        for item in junctions_raw:
            if isinstance(item, Mapping):
                payload["junctions"].append(item)

    roundabouts_raw = raw.get("roundabouts")
    if isinstance(roundabouts_raw, Sequence) and not isinstance(roundabouts_raw, (str, bytes)):
        for item in roundabouts_raw:
            if isinstance(item, Mapping):
                payload["roundabouts"].append(item)

    return payload


def _unsupported_roadgen_fields(annotation: ReferenceAnnotation) -> List[str]:
    unsupported: List[str] = []
    if annotation.regions:
        unsupported.append("regions")
    if annotation.building_regions:
        unsupported.append("building_regions")
    if annotation.functional_zones:
        unsupported.append("functional_zones")
    if annotation.surface_annotations:
        unsupported.append("surface_annotations")
    if annotation.station_strip_patches:
        unsupported.append("station_strip_patches")
    if annotation.junction_compositions:
        unsupported.append("junction_compositions")
    if annotation.roundabouts:
        unsupported.append("roundabouts")
    if any(cl.street_furniture_instances for cl in annotation.centerlines):
        unsupported.append("street_furniture_instances")
    return unsupported


def _node_profile_from_centerline(
    centerline: Any,
    pixels_per_meter: float,
    profile_defs: List[tuple[RoadPenProfile, Tuple[float, float, float, float]]],
) -> RoadPenProfile:
    lane_profile = centerline.lane_profile()
    forward = int(lane_profile.get("forward_drive_lane_count", 1))
    reverse = int(lane_profile.get("reverse_drive_lane_count", 0))
    if forward <= 0 and reverse <= 0:
        forward = 1

    cross_section_m = max(1.0, float(centerline.cross_section_width_m()))
    carriageway_m = max(1.0, float(centerline.carriageway_width_m()))
    side_band_m = max(0.0, cross_section_m - carriageway_m)

    carriageway_px = max(2.0, round(carriageway_m * pixels_per_meter, 3))
    facility_px = max(1.0, round(side_band_m * 0.45 * pixels_per_meter, 3))
    sidewalk_px = max(1.0, round(side_band_m * 0.35 * pixels_per_meter, 3))
    clearance_px = max(1.0, round(side_band_m * 0.2 * pixels_per_meter, 3))

    key = (carriageway_px, facility_px, sidewalk_px, clearance_px)
    for profile, saved_key in profile_defs:
        if key == saved_key:
            return profile

    # allocate lane-aware extra width only if semantically needed
    base_name = _as_str(centerline.label, centerline.feature_id)
    profile = RoadPenProfile(
        id=f"auto_profile_{len(profile_defs) + 1:03d}",
        name=base_name,
        carriagewayWidth=float(carriageway_px),
        facilityWidth=float(facility_px),
        sidewalkWidth=float(sidewalk_px),
        clearanceWidth=float(clearance_px),
    )

    # touch forward/reverse for future diagnostics
    if forward + reverse >= 4:
        profile = RoadPenProfile(
            id=profile.id,
            name=profile.name,
            carriagewayWidth=float(profile.carriagewayWidth + 1.0),
            facilityWidth=float(profile.facilityWidth),
            sidewalkWidth=float(profile.sidewalkWidth),
            clearanceWidth=float(profile.clearanceWidth),
        )

    profile_defs.append((profile, key))
    return profile


def _roadgen_to_roadpen_scene(annotation: ReferenceAnnotation, mode: str) -> tuple[Dict[str, Any], BridgeSummary]:
    strict = mode == "strict"
    warnings: List[str] = []
    repaired: List[str] = []

    unsupported = _unsupported_roadgen_fields(annotation)
    if strict and unsupported:
        raise ValueError("strict mode blocked unsupported fields: " + ", ".join(unsupported))

    point_to_node: Dict[Tuple[int, int], str] = {}
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    profile_defs: List[tuple[RoadPenProfile, Tuple[float, float, float, float]]] = []

    def _get_node(point: Tuple[float, float]) -> str:
        key = _node_key(point)
        node_id = point_to_node.get(key)
        if node_id is not None:
            return node_id
        node_id = f"n{len(nodes):04d}"
        point_to_node[key] = node_id
        nodes.append({"id": node_id, "x": float(point[0]), "y": float(point[1])})
        return node_id

    for index, centerline in enumerate(annotation.centerlines):
        raw_points = [(float(point.x), float(point.y)) for point in centerline.points]
        points = _dedupe_adjacent(raw_points)
        if len(points) < 2:
            if strict:
                raise ValueError(f"centerline {centerline.feature_id} has <2 points")
            warnings.append(f"drop centerline {centerline.feature_id}: <2 points")
            continue

        profile = _node_profile_from_centerline(centerline, annotation.pixels_per_meter, profile_defs)
        control_points = points if len(points) > 2 else [points[0], points[-1]]
        start_node_id = _get_node(points[0])
        end_node_id = _get_node(points[-1])
        if start_node_id == end_node_id:
            if strict:
                raise ValueError(f"centerline {centerline.feature_id} collapsed to same point")
            warnings.append(f"drop centerline {centerline.feature_id}: collapsed")
            continue

        edges.append(
            {
                "id": _as_str(centerline.feature_id, f"centerline_{index + 1:03d}"),
                "from": start_node_id,
                "to": end_node_id,
                "geomType": "spline" if len(points) > 2 else "polyline",
                "controlPoints": [{"x": float(x), "y": float(y)} for x, y in control_points],
                "profileId": profile.id,
                "endMode": "free",
                "layer": 0,
            }
        )

    if annotation.roundabouts:
        msg = f"dropped {len(annotation.roundabouts)} roundabouts"
        if strict:
            warnings.append(msg)
            raise ValueError("strict mode blocked roundabouts")
        warnings.append(msg)
        if mode == "repair":
            repaired.append("roundabouts converted to approximated loops")

    if mode == "repair" and annotation.roundabouts:
        for roundabout in annotation.roundabouts:
            cx = float(roundabout.x)
            cy = float(roundabout.y)
            radius = max(6.0, float(roundabout.radius_px))
            segments = max(10, int(math.ceil(2.0 * math.pi * radius / 40.0)))
            profile = profile_defs[0][0] if profile_defs else RoadPenProfile(
                id=ROADPEN_DEFAULT_PROFILE["id"],
                name=ROADPEN_DEFAULT_PROFILE["name"],
                carriagewayWidth=ROADPEN_DEFAULT_PROFILE["carriagewayWidth"],
                facilityWidth=ROADPEN_DEFAULT_PROFILE["facilityWidth"],
                sidewalkWidth=ROADPEN_DEFAULT_PROFILE["sidewalkWidth"],
                clearanceWidth=ROADPEN_DEFAULT_PROFILE["clearanceWidth"],
            )
            loop = [
                (
                    cx + math.cos((2.0 * math.pi * i) / segments) * radius,
                    cy + math.sin((2.0 * math.pi * i) / segments) * radius,
                )
                for i in range(segments + 1)
            ]
            for i in range(len(loop) - 1):
                start_xy = loop[i]
                end_xy = loop[i + 1]
                start_id = _get_node(start_xy)
                end_id = _get_node(end_xy)
                edges.append(
                    {
                        "id": f"roundabout_{roundabout.feature_id}_{i:03d}",
                        "from": start_id,
                        "to": end_id,
                        "geomType": "polyline",
                        "controlPoints": [
                            {"x": float(start_xy[0]), "y": float(start_xy[1])},
                            {"x": float(end_xy[0]), "y": float(end_xy[1])},
                        ],
                        "profileId": profile.id,
                        "endMode": "free",
                        "layer": 0,
                    }
                )
            warnings.append(f"repair converted roundabout {roundabout.feature_id} to loop edges")

    if not edges:
        if strict:
            raise ValueError("No usable centerline edges")
        warnings.append("No usable centerlines")

    if not profile_defs:
        profile_defs.append(
            (
                RoadPenProfile(
                    id=ROADPEN_DEFAULT_PROFILE["id"],
                    name=ROADPEN_DEFAULT_PROFILE["name"],
                    carriagewayWidth=ROADPEN_DEFAULT_PROFILE["carriagewayWidth"],
                    facilityWidth=ROADPEN_DEFAULT_PROFILE["facilityWidth"],
                    sidewalkWidth=ROADPEN_DEFAULT_PROFILE["sidewalkWidth"],
                    clearanceWidth=ROADPEN_DEFAULT_PROFILE["clearanceWidth"],
                ),
                (
                    float(ROADPEN_DEFAULT_PROFILE["carriagewayWidth"]),
                    float(ROADPEN_DEFAULT_PROFILE["facilityWidth"]),
                    float(ROADPEN_DEFAULT_PROFILE["sidewalkWidth"]),
                    float(ROADPEN_DEFAULT_PROFILE["clearanceWidth"]),
                ),
            )
        )

    profiles = [item[0] for item in profile_defs]
    profile_ids = {p.id for p in profiles}
    for edge in edges:
        if edge["profileId"] not in profile_ids:
            edge["profileId"] = profiles[0].id

    all_x = [node["x"] for node in nodes]
    all_y = [node["y"] for node in nodes]
    if all_x and all_y:
        image_width = int(math.ceil((max(all_x) - min(all_x)) + 256.0))
        image_height = int(math.ceil((max(all_y) - min(all_y)) + 256.0))
    else:
        image_width = 1024
        image_height = 1024

    image_width = annotation.image_width_px if annotation.image_width_px > 0 else max(64, image_width)
    image_height = annotation.image_height_px if annotation.image_height_px > 0 else max(64, image_height)

    if strict:
        losses = []
    else:
        losses = unsupported

    payload: Dict[str, Any] = {
        "version": ROADPEN_VERSION,
        "units": "px",
        "scalePxPerM": float(annotation.pixels_per_meter),
        "nodes": nodes,
        "edges": edges,
        "profiles": [
            {
                "id": item.id,
                "name": item.name,
                "carriagewayWidth": float(item.carriagewayWidth),
                "facilityWidth": float(item.facilityWidth),
                "sidewalkWidth": float(item.sidewalkWidth),
                "clearanceWidth": float(item.clearanceWidth),
            }
            for item in profiles
        ],
        "__bridge_meta": {
            "source_format": ANNOTATION_SCHEMA_VERSION,
            "target_format": ROADPEN_VERSION,
            "mode": mode,
            "source_centerlines": len(annotation.centerlines),
            "converted_edges": len(edges),
            "source_roundabouts": len(annotation.roundabouts),
            "lane_profile": [
                {
                    "id": item.id,
                    "carriagewayWidth": item.carriagewayWidth,
                    "facilityWidth": item.facilityWidth,
                    "sidewalkWidth": item.sidewalkWidth,
                    "clearanceWidth": item.clearanceWidth,
                }
                for item in profiles
            ],
        },
    }

    if strict:
        image_width = max(64, image_width)
        image_height = max(64, image_height)

    return payload, BridgeSummary(
        mode=mode,
        warnings=warnings,
        losses=losses,
        repaired=repaired,
        converted_count={
            "nodes": len(nodes),
            "edges": len(edges),
            "profiles": len(profiles),
        },
    )


# =========================
# CLI glue
# =========================

def _build_bridge_result(
    *,
    command: str,
    mode: str,
    source_path: Path,
    payload: Dict[str, Any],
    summary: BridgeSummary,
) -> Dict[str, Any]:
    return {
        "schema": "roadpen_roadgen3d_bridge_v1",
        "converted_at": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "mode": mode,
        "source_path": str(source_path),
        "payload": payload,
        "bridge_summary": {
            "source_format": command.split("-")[0],
            "target_format": command.split("-")[-1],
            "mode": mode,
            "converted_count": summary.converted_count,
            "losses": summary.losses,
            "repaired": summary.repaired,
            "warning_count": len(summary.warnings),
        },
        "warnings": summary.warnings,
        "stats": summary.converted_count,
    }


def _write_result(payload: Dict[str, Any], output_path: Path | None, *, compact: bool = False) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=None if compact else 2)
    if output_path is None:
        print(text)
        return
    output_path.write_text(text + "\n", encoding="utf-8")
    print(f"wrote: {output_path}")


def _command_roadpen_to_roadgen(args: argparse.Namespace) -> int:
    mode = args.mode
    raw = _read_json(args.input_path)
    if not isinstance(raw, Mapping):
        raise ValueError("RoadPen input must be an object")

    scene, parse_warnings = _normalize_roadpen_scene(raw, mode)
    payload, summary = _roadpen_to_roadgen_annotation(scene, mode)
    summary.warnings.extend(parse_warnings)

    result = _build_bridge_result(
        command="roadpen-to-roadgen3d",
        mode=mode,
        source_path=args.input_path,
        payload=payload,
        summary=summary,
    )
    _write_result(result, args.output_path, compact=args.compact)
    return 0


def _command_roadgen_to_roadpen(args: argparse.Namespace) -> int:
    mode = args.mode
    raw = _read_json(args.input_path)
    if not isinstance(raw, Mapping):
        raise ValueError("RoadGen3D annotation input must be an object")

    payload = _extract_annotation_payload(raw)
    warnings: List[str] = []

    try:
        annotation = parse_reference_annotation(payload)
    except Exception as exc:
        if mode == "strict":
            raise
        warnings.append(f"initial parse failed: {exc}")
        payload = _repair_annotation_payload(payload, warnings)
        annotation = parse_reference_annotation(payload)

    scene_payload, summary = _roadgen_to_roadpen_scene(annotation, mode)
    summary.warnings = warnings + summary.warnings

    result = _build_bridge_result(
        command="roadgen3d-to-roadpen",
        mode=mode,
        source_path=args.input_path,
        payload=scene_payload,
        summary=summary,
    )
    _write_result(result, args.output_path, compact=args.compact)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert between RoadPen scene and RoadGen3D annotation")
    parser.add_argument("--compact", action="store_true", help="Print one-line JSON")

    subparsers = parser.add_subparsers(dest="command", required=True)

    p1 = subparsers.add_parser("roadpen-to-roadgen3d", help="Convert RoadPen scene -> RoadGen3D annotation")
    p1.add_argument("input_path", type=Path)
    p1.add_argument("-o", "--output", dest="output_path", type=Path, default=None)
    p1.add_argument(
        "--mode",
        type=_coerce_conversion_mode,
        choices=SUPPORTED_MODES,
        default="preview",
        help="strict|preview|repair",
    )
    p1.set_defaults(handler=_command_roadpen_to_roadgen)

    p2 = subparsers.add_parser("roadgen3d-to-roadpen", help="Convert RoadGen3D annotation -> RoadPen scene")
    p2.add_argument("input_path", type=Path)
    p2.add_argument("-o", "--output", dest="output_path", type=Path, default=None)
    p2.add_argument(
        "--mode",
        type=_coerce_conversion_mode,
        choices=SUPPORTED_MODES,
        default="preview",
        help="strict|preview|repair",
    )
    p2.set_defaults(handler=_command_roadgen_to_roadpen)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        return int(args.handler(args))
    except Exception as exc:
        print(f"[format_bridge] error: {exc}", file=sys.stderr)
        if getattr(args, "mode", "preview") == "strict":
            return 2
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
