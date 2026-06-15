"""StreetProgram generation for the neuralsymbolic street pipeline."""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Sequence, Tuple

from .poi_taxonomy import (
    CANONICAL_FIRE_POI,
    asset_backed_category_counts,
    extract_poi_points_by_type,
    normalize_poi_counts,
)
from .street_band_semantics import (
    DETAILED_SIDE_STRIP_KINDS,
    detailed_strip_allowed_categories,
    detailed_strip_band_kind,
    detailed_strip_band_name,
    has_detailed_strip_profiles,
    iter_detailed_strip_profiles,
)
from .street_priors import (
    DEFAULT_CATEGORIES,
    DEFAULT_SPACING_M,
    FURNITURE_RHYTHM_CATEGORIES,
    FURNITURE_RHYTHM_INTERVAL_M,
    FURNITURE_SCENE_MAX_COUNTS,
)
from .types import StreetBand, StreetComposeConfig, StreetProgram

_PROFILE_DEFAULTS: Dict[str, Dict[str, object]] = {
    "balanced_complete_street_v1": {
        "cross_section_type": "balanced_complete_street",
        "min_clear_path_width_m": 2.4,
        "furnishing_width_m": 1.0,
        "band_kinds": ("furnishing", "clear_path", "carriageway", "clear_path", "furnishing"),
        "design_goals": ("safety", "walkability", "amenity", "clarity"),
        "density_scales": {
            "bench": 0.9,
            "lamp": 1.0,
            "trash": 0.9,
            "tree": 1.0,
            "bus_stop": 0.5,
            "mailbox": 0.4,
            "hydrant": 0.4,
            "bollard": 1.0,
        },
        "required_categories": ("lamp", "tree", "bollard"),
    },
    "pedestrian_priority_v1": {
        "cross_section_type": "pedestrian_priority",
        "min_clear_path_width_m": 3.2,
        "furnishing_width_m": 1.4,
        "band_kinds": ("furnishing", "clear_path", "carriageway", "clear_path", "furnishing"),
        "design_goals": ("walkability", "comfort", "greening", "safety"),
        "density_scales": {
            "bench": 1.35,
            "lamp": 1.1,
            "trash": 1.1,
            "tree": 1.45,
            "bus_stop": 0.4,
            "mailbox": 0.3,
            "hydrant": 0.3,
            "bollard": 1.3,
        },
        "required_categories": ("bench", "tree", "lamp", "bollard"),
    },
    "transit_priority_v1": {
        "cross_section_type": "transit_priority",
        "min_clear_path_width_m": 2.6,
        "furnishing_width_m": 1.0,
        "right_edge_width_m": 1.8,
        "band_kinds": ("furnishing", "clear_path", "carriageway", "clear_path", "transit_edge"),
        "design_goals": ("transit_access", "legibility", "safety", "throughput"),
        "density_scales": {
            "bench": 0.55,
            "lamp": 1.2,
            "trash": 0.85,
            "tree": 0.75,
            "bus_stop": 1.4,
            "mailbox": 0.4,
            "hydrant": 0.35,
            "bollard": 1.15,
        },
        "required_categories": ("bus_stop", "lamp", "bollard"),
    },
}

_ROAD_TYPE_KEYWORDS: Tuple[Tuple[str, str], ...] = (
    ("industrial", "industrial"),
    ("residential", "residential"),
    ("neighborhood", "residential"),
    ("downtown", "urban_core"),
    ("commercial", "mixed_use"),
    ("mixed-use", "mixed_use"),
    ("transit", "transit_corridor"),
    ("bus", "transit_corridor"),
    ("boulevard", "boulevard"),
)

_QUERY_CATEGORY_BOOSTS: Tuple[Tuple[Tuple[str, ...], Dict[str, float]], ...] = (
    (("tree", "green", "park"), {"tree": 1.35, "bench": 1.1}),
    (("pedestrian", "walkable", "walkability"), {"bench": 1.25, "bollard": 1.15, "trash": 1.1}),
    (("bus", "transit"), {"bus_stop": 1.8, "lamp": 1.1, "bollard": 1.1}),
    (("industrial",), {"bench": 0.6, "tree": 0.6, "bollard": 1.05}),
    (("clean", "orderly", "minimal"), {"trash": 1.15, "bollard": 1.1}),
)

_DEMAND_FACTORS: Dict[str, float] = {
    "low": 0.85,
    "medium": 1.0,
    "high": 1.2,
}


def _coerce_category_tuple(value: object) -> Tuple[str, ...]:
    if isinstance(value, str):
        raw_items = value.replace(";", ",").split(",")
    elif isinstance(value, Sequence):
        raw_items = list(value)
    else:
        raw_items = []
    return tuple(dict.fromkeys(str(item).strip().lower() for item in raw_items if str(item).strip()))


def _profile_defaults(profile_name: str) -> Dict[str, object]:
    return dict(_PROFILE_DEFAULTS.get(profile_name, _PROFILE_DEFAULTS["balanced_complete_street_v1"]))


def profile_defaults(profile_name: str) -> Dict[str, object]:
    """Public access to cross-section defaults used by StreetProgram generation."""

    return _profile_defaults(profile_name)


def _infer_road_type(query: str, fallback: str) -> str:
    query_lc = query.strip().lower()
    for needle, road_type in _ROAD_TYPE_KEYWORDS:
        if needle in query_lc:
            return road_type
    return fallback.strip().lower() or "mixed_use"


def _merge_goals(query: str, base_goals: Sequence[str]) -> Tuple[str, ...]:
    goals: List[str] = list(base_goals)
    query_lc = query.strip().lower()
    if "tree" in query_lc or "green" in query_lc:
        goals.append("greening")
    if "pedestrian" in query_lc or "walk" in query_lc:
        goals.append("walkability")
    if "transit" in query_lc or "bus" in query_lc:
        goals.append("transit_access")
    if "clean" in query_lc or "orderly" in query_lc:
        goals.append("clarity")
    seen = set()
    ordered: List[str] = []
    for goal in goals:
        if goal not in seen:
            ordered.append(goal)
            seen.add(goal)
    return tuple(ordered)


def _goal_weights(goals: Sequence[str]) -> Dict[str, float]:
    if not goals:
        return {}
    total = float(len(goals))
    return {str(goal): float(1.0 / total) for goal in goals}


def _demand_factor(level: str) -> float:
    return float(_DEMAND_FACTORS.get(str(level).strip().lower(), 1.0))


def _street_furniture_disabled(config: StreetComposeConfig) -> bool:
    return str(getattr(config, "street_furniture_profile", "") or "").strip().lower() in {
        "none",
        "no_furniture",
        "furniture_free",
        "structure_only",
    }


def _observed_poi_counts(poi_context: object | None) -> Dict[str, int]:
    if poi_context is None:
        return {}
    counts = normalize_poi_counts({
        poi_type: len(points)
        for poi_type, points in extract_poi_points_by_type(poi_context, suffix="xz").items()
    })
    return {
        poi_type: int(count)
        for poi_type, count in counts.items()
        if int(count) > 0
    }


def _apply_observed_poi_bindings(
    requirements: Dict[str, int],
    observed_poi_counts: Dict[str, int],
    poi_context: object | None,
    control_points: List[str],
    merged_goals: Tuple[str, ...],
    bands: Sequence[StreetBand],
    profile_name: str,
) -> Tuple[Dict[str, str], Tuple[str, ...]]:
    asset_counts = asset_backed_category_counts(
        extract_poi_points_by_type(poi_context, suffix="xz") if poi_context is not None else {}
    )
    for category, count in asset_counts.items():
        requirements[category] = max(int(requirements.get(category, 0)), int(count))

    if int(observed_poi_counts.get("bus_stop", 0)) > 0 or int(observed_poi_counts.get("subway_entrance", 0)) > 0:
        if "transit_stop" not in control_points:
            control_points.append("transit_stop")
    if int(observed_poi_counts.get("crossing", 0)) > 0 and "crossing" not in control_points:
        control_points.append("crossing")
    if int(observed_poi_counts.get("parking_entrance", 0)) > 0 and "access" not in control_points:
        control_points.append("access")

    goal_list = list(merged_goals)
    if (
        int(observed_poi_counts.get("bus_stop", 0)) > 0
        or int(observed_poi_counts.get("subway_entrance", 0)) > 0
    ) and "transit_access" not in goal_list:
        goal_list.append("transit_access")
    merged_goals = tuple(goal_list)

    reserved_band_categories: Dict[str, str] = {}
    if int(observed_poi_counts.get("bus_stop", 0)) > 0:
        right_bus_band = next(
            (
                band.name
                for band in bands
                if band.side == "right"
                and band.kind in {"furnishing", "transit_edge"}
                and "bus_stop" in band.allowed_categories
            ),
            "",
        )
        if right_bus_band:
            reserved_band_categories[right_bus_band] = "bus_stop"
    elif profile_name == "transit_priority_v1":
        reserved_band_categories["right_transit_edge"] = "bus_stop"
    return reserved_band_categories, merged_goals


def _build_cross_section_bands(
    *,
    road_width_m: float,
    left_clear_path_width_m: float,
    right_clear_path_width_m: float,
    left_furnishing_width_m: float,
    right_edge_width_m: float,
    profile_name: str,
) -> Tuple[StreetBand, ...]:
    left_edge = float(left_furnishing_width_m)
    right_edge = float(right_edge_width_m)
    left_clear_width = float(left_clear_path_width_m)
    right_clear_width = float(right_clear_path_width_m)
    road_half = float(road_width_m) / 2.0

    left_edge_kind = "furnishing"
    right_edge_kind = "transit_edge" if profile_name == "transit_priority_v1" else "furnishing"
    furnishing_categories = ("bench", "lamp", "trash", "tree", "mailbox", "hydrant", "bollard", "bus_stop")
    transit_categories = ("bus_stop", "lamp", "bollard", "trash", "bench")

    return (
        StreetBand(
            name="left_furnishing",
            kind=left_edge_kind,
            side="left",
            width_m=left_edge,
            z_center_m=road_half + left_edge / 2.0,
            allowed_categories=furnishing_categories,
        ),
        StreetBand(
            name="left_clear_path",
            kind="clear_path",
            side="left",
            width_m=left_clear_width,
            z_center_m=road_half + left_edge + left_clear_width / 2.0,
            allowed_categories=(),
        ),
        StreetBand(
            name="carriageway",
            kind="carriageway",
            side="center",
            width_m=float(road_width_m),
            z_center_m=0.0,
            allowed_categories=(),
        ),
        StreetBand(
            name="right_clear_path",
            kind="clear_path",
            side="right",
            width_m=right_clear_width,
            z_center_m=-(road_half + right_edge + right_clear_width / 2.0),
            allowed_categories=(),
        ),
        StreetBand(
            name="right_furnishing" if right_edge_kind == "furnishing" else "right_transit_edge",
            kind=right_edge_kind,
            side="right",
            width_m=right_edge,
            z_center_m=-(road_half + right_edge / 2.0),
            allowed_categories=transit_categories if right_edge_kind == "transit_edge" else furnishing_categories,
        ),
    )


def _aggregate_detailed_strip_profiles(placement_context: object | None) -> Dict[Tuple[str, str], Dict[str, float]]:
    aggregated: Dict[Tuple[str, str], Dict[str, float]] = {}
    for profile in iter_detailed_strip_profiles(placement_context):
        side = str(profile.get("side", "") or "").strip().lower()
        strip_kind = str(profile.get("kind", "") or "").strip().lower()
        if side not in {"left", "right"} or strip_kind not in DETAILED_SIDE_STRIP_KINDS:
            continue
        key = (side, strip_kind)
        values = aggregated.setdefault(
            key,
            {
                "width_sum": 0.0,
                "center_sum": 0.0,
                "count": 0.0,
            },
        )
        values["width_sum"] += float(profile.get("width_m", 0.0) or 0.0)
        values["center_sum"] += float(profile.get("center_offset_m", 0.0) or 0.0)
        values["count"] += 1.0
    return aggregated


def _build_detailed_cross_section_bands(
    *,
    road_width_m: float,
    placement_context: object | None,
    profile_name: str,
) -> Tuple[StreetBand, ...]:
    aggregated = _aggregate_detailed_strip_profiles(placement_context)
    if not aggregated:
        return ()

    bands: List[StreetBand] = []
    for side in ("left", "right"):
        for strip_kind in DETAILED_SIDE_STRIP_KINDS:
            values = aggregated.get((side, strip_kind))
            if not values or float(values.get("count", 0.0) or 0.0) <= 0.0:
                continue
            width_m = float(values["width_sum"]) / float(values["count"])
            if width_m <= 0.0:
                continue
            z_center_m = float(values["center_sum"]) / float(values["count"])
            bands.append(
                StreetBand(
                    name=detailed_strip_band_name(side, strip_kind),
                    kind=detailed_strip_band_kind(strip_kind, side=side, profile_name=profile_name),
                    side=side,
                    width_m=float(width_m),
                    z_center_m=float(z_center_m),
                    allowed_categories=detailed_strip_allowed_categories(strip_kind),
                )
            )

    def _center_strip_allowed_categories(strip_kind: str) -> Tuple[str, ...]:
        normalized_kind = str(strip_kind or "").strip().lower()
        if normalized_kind in {"grass_belt", "median_green"}:
            return ("tree",)
        return ()

    # Add center bands from detailed profiles so that median / bike lane / grass belt are visible
    for profile in iter_detailed_strip_profiles(placement_context):
        side = str(profile.get("side", "") or "").strip().lower()
        if side != "center":
            continue
        width_m = float(profile.get("width_m", 0.0) or 0.0)
        if width_m <= 0.0:
            continue
        z_center_m = float(profile.get("center_m", 0.0) or profile.get("center_offset_m", 0.0) or 0.0)
        strip_kind = str(profile.get("kind", "") or "").strip().lower()
        bands.append(
            StreetBand(
                name=f"center_{strip_kind}",
                kind=detailed_strip_band_kind(strip_kind, side="center", profile_name=profile_name),
                side="center",
                width_m=float(width_m),
                z_center_m=float(z_center_m),
                allowed_categories=_center_strip_allowed_categories(strip_kind),
            )
        )

    if not any(str(b.side) == "center" for b in bands):
        bands.append(
            StreetBand(
                name="carriageway",
                kind="carriageway",
                side="center",
                width_m=float(road_width_m),
                z_center_m=0.0,
                allowed_categories=(),
            )
        )
    ordered = sorted(
        bands,
        key=lambda band: (
            0 if band.side == "left" else 1 if band.side == "center" else 2,
            -float(band.z_center_m) if band.side == "left" else float(band.z_center_m) if band.side == "right" else 0.0,
            str(band.name),
        ),
    )
    return tuple(ordered)


def _estimate_furniture_requirements(
    *,
    query: str,
    length_m: float,
    density: float,
    available_categories: Iterable[str],
    profile_scales: Dict[str, float],
    required_categories: Sequence[str],
) -> Dict[str, int]:
    available = set(available_categories)
    density_value = max(float(density), 0.1)
    query_lc = query.strip().lower()
    query_scales: Dict[str, float] = {}
    for keywords, scale_map in _QUERY_CATEGORY_BOOSTS:
        if any(keyword in query_lc for keyword in keywords):
            for category, scale in scale_map.items():
                query_scales[category] = query_scales.get(category, 1.0) * float(scale)

    requirements: Dict[str, int] = {}
    for category in DEFAULT_CATEGORIES:
        if category not in available:
            continue
        base_spacing = float(DEFAULT_SPACING_M[category])
        profile_scale = float(profile_scales.get(category, 1.0))
        query_scale = float(query_scales.get(category, 1.0))
        effective_scale = max(0.0, density_value * profile_scale * query_scale)
        if effective_scale <= 0.0:
            requirements[category] = 0
            continue
        if category in {"bus_stop", "mailbox", "hydrant"} and effective_scale < 0.6:
            requirements[category] = 0
            continue
        spacing = max(base_spacing / effective_scale, 3.0)
        count = int(math.floor(float(length_m) / spacing))
        if category in required_categories:
            count = max(1, count)
        elif effective_scale >= 0.8:
            count = max(1, count)
        requirements[category] = max(0, count)
    return requirements


def _rhythm_min_count(category: str, length_m: float) -> int:
    if category not in FURNITURE_RHYTHM_CATEGORIES:
        return 0
    length = max(float(length_m), 0.0)
    if length < 30.0:
        return 1
    interval = max(float(FURNITURE_RHYTHM_INTERVAL_M.get(category, 70.0)), 1.0)
    return int(max(2, 2 * math.ceil(length / interval)))


def _apply_furniture_design_quantity_rules(
    requirements: Dict[str, int],
    *,
    config: StreetComposeConfig,
    available_categories: Iterable[str],
) -> Dict[str, int]:
    available = {str(category).strip().lower() for category in available_categories}
    normalized = {
        str(category): max(0, int(count))
        for category, count in dict(requirements).items()
        if str(category) in available
    }
    for category in FURNITURE_RHYTHM_CATEGORIES:
        if category not in available:
            continue
        minimum = _rhythm_min_count(category, float(config.length_m))
        if minimum > 0:
            normalized[category] = max(int(normalized.get(category, 0)), int(minimum))
        if normalized.get(category, 0) > 1 and normalized[category] % 2:
            normalized[category] += 1
    distribution_policy = str(
        getattr(config, "street_furniture_distribution_policy", "road_uniform_v1") or "road_uniform_v1"
    ).strip().lower()
    if distribution_policy == "road_uniform_v1":
        rhythm_counts = [
            int(normalized.get(category, 0))
            for category in FURNITURE_RHYTHM_CATEGORIES
            if category in available and int(normalized.get(category, 0)) > 0
        ]
        if len(rhythm_counts) >= 2:
            rhythm_target = max(rhythm_counts)
            for category in FURNITURE_RHYTHM_CATEGORIES:
                if category in available and int(normalized.get(category, 0)) > 0:
                    normalized[category] = rhythm_target
    for category, cap in FURNITURE_SCENE_MAX_COUNTS.items():
        if category not in normalized:
            continue
        normalized[category] = min(int(cap), int(normalized[category]))
    return normalized


def _throughput_requirements(config: StreetComposeConfig, profile_name: str, lane_count: int) -> Dict[str, float]:
    ped_factor = _demand_factor(str(getattr(config, "ped_demand_level", "medium")))
    transit_factor = _demand_factor(str(getattr(config, "transit_demand_level", "medium")))
    vehicle_factor = _demand_factor(str(getattr(config, "vehicle_demand_level", "medium")))
    lane_width_m = float(getattr(config, "base_lane_width_m", None) or 3.0)

    requirements = {
        "ped_clear_path": float(1.8 * ped_factor),
        "vehicle_carriageway": float(max(3.0, lane_width_m) * max(1, int(lane_count)) * vehicle_factor),
    }
    if (
        profile_name == "transit_priority_v1"
        or str(getattr(config, "objective_profile", "balanced")).strip().lower() == "transit"
        or float(transit_factor) > 1.05
    ):
        requirements["transit_edge"] = float(1.4 * transit_factor)
    return requirements


def _default_band_bounds(
    bands: Sequence[StreetBand],
    *,
    config: StreetComposeConfig,
    profile_name: str,
    throughput_requirements: Dict[str, float],
) -> Dict[str, Dict[str, float]]:
    bounds: Dict[str, Dict[str, float]] = {}
    for band in bands:
        min_width = 0.5
        max_width = max(float(band.width_m), 0.5)
        if band.kind == "carriageway":
            min_width = float(throughput_requirements.get("vehicle_carriageway", max(float(band.width_m), 3.0)))
            max_width = max(float(band.width_m), float(max(1, int(config.lane_count))) * 3.8)
        elif band.kind == "clear_path":
            min_width = float(max(float(band.width_m), throughput_requirements.get("ped_clear_path", 1.8)))
            max_width = max(float(band.width_m), 4.5)
        elif band.kind == "transit_edge":
            min_width = float(max(1.0, throughput_requirements.get("transit_edge", 1.2)))
            max_width = max(float(band.width_m), 3.2)
        elif band.kind == "furnishing":
            min_width = float(max(0.8, min(float(band.width_m), 1.2)))
            max_width = max(float(band.width_m), 3.4 if profile_name == "pedestrian_priority_v1" else 3.0)
        bounds[band.name] = {
            "min_width_m": float(min_width),
            "max_width_m": float(max(max_width, min_width)),
        }
    return bounds


def _topology_requirements(profile_name: str, bands: Sequence[StreetBand]) -> Dict[str, object]:
    band_names = {band.name for band in bands}
    adjacency: List[Dict[str, str]] = []
    separation: List[Dict[str, str]] = []
    left_furnishing_name = next(
        (
            band.name
            for band in bands
            if band.side == "left" and band.kind in {"furnishing", "transit_edge"}
        ),
        "",
    )
    left_clear_name = next(
        (
            band.name
            for band in bands
            if band.side == "left" and band.kind == "clear_path"
        ),
        "",
    )
    right_edge_name = next(
        (
            band.name
            for band in bands
            if band.side == "right" and band.kind in {"furnishing", "transit_edge"}
        ),
        "",
    )
    right_clear_name = next(
        (
            band.name
            for band in bands
            if band.side == "right" and band.kind == "clear_path"
        ),
        "",
    )
    if left_furnishing_name and left_clear_name:
        adjacency.append({"band_name": left_clear_name, "adjacent_to": left_furnishing_name})
    if right_edge_name and right_clear_name:
        adjacency.append({"band_name": right_clear_name, "adjacent_to": right_edge_name})
    if left_furnishing_name and left_clear_name and "carriageway" in band_names:
        separation.append({"left": left_furnishing_name, "right": "carriageway", "separator": left_clear_name})
    if "carriageway" in band_names and right_edge_name and right_clear_name:
        separation.append({"left": "carriageway", "right": right_edge_name, "separator": right_clear_name})
    return {
        "profile_name": str(profile_name),
        "adjacency_required": adjacency,
        "separation_required": separation,
    }


def infer_street_program(
    config: StreetComposeConfig,
    available_categories: Iterable[str],
    poi_context: object | None = None,
    placement_context: object | None = None,
) -> StreetProgram:
    """Infer a structured StreetProgram from text and composition context."""

    profile_name = str(config.design_rule_profile).strip().lower() or "balanced_complete_street_v1"
    defaults = _profile_defaults(profile_name)
    road_type = _infer_road_type(config.query, config.target_street_type)
    clear_width = max(float(config.sidewalk_width_m), float(defaults["min_clear_path_width_m"]))
    furnishing_width = float(defaults["furnishing_width_m"])
    right_edge_width = float(defaults.get("right_edge_width_m", furnishing_width))
    carriageway_width = float(getattr(placement_context, "carriageway_width_m", config.road_width_m))
    left_clear_width = float(getattr(placement_context, "left_clear_path_width_m", clear_width))
    right_clear_width = float(getattr(placement_context, "right_clear_path_width_m", clear_width))
    left_furnishing_width = float(getattr(placement_context, "left_furnishing_width_m", furnishing_width))
    right_furnishing_width = float(getattr(placement_context, "right_furnishing_width_m", right_edge_width))
    row_width = float(
        getattr(
            placement_context,
            "row_width_m",
            carriageway_width + left_clear_width + right_clear_width + left_furnishing_width + right_furnishing_width,
        )
    )
    width_expanded = bool(getattr(placement_context, "width_expanded", False))
    width_reallocation_reason = str(getattr(placement_context, "width_reallocation_reason", ""))
    poi_fit_feasible = bool(getattr(placement_context, "poi_fit_feasible", True))
    poi_fit_report = dict(getattr(placement_context, "poi_fit_report", {}) or {})

    if has_detailed_strip_profiles(placement_context):
        bands = _build_detailed_cross_section_bands(
            road_width_m=carriageway_width,
            placement_context=placement_context,
            profile_name=profile_name,
        )
    else:
        bands = _build_cross_section_bands(
            road_width_m=carriageway_width,
            left_clear_path_width_m=left_clear_width,
            right_clear_path_width_m=right_clear_width,
            left_furnishing_width_m=left_furnishing_width,
            right_edge_width_m=right_furnishing_width,
            profile_name=profile_name,
        )
    furniture_disabled = _street_furniture_disabled(config)
    if furniture_disabled:
        requirements = {
            category: 0
            for category in DEFAULT_CATEGORIES
            if category in {str(item).strip().lower() for item in available_categories}
        }
    else:
        requirements = _estimate_furniture_requirements(
            query=config.query,
            length_m=float(config.length_m),
            density=float(config.density),
            available_categories=available_categories,
            profile_scales=dict(defaults["density_scales"]),
            required_categories=tuple(defaults["required_categories"]),
        )
    if not furniture_disabled and str(getattr(config, "amenity_coverage_mode", "try") or "try").strip().lower() == "try":
        available_set = {str(category).strip().lower() for category in available_categories}
        for category in _coerce_category_tuple(getattr(config, "minimum_category_presence", ("trash", "bench", "lamp"))):
            if category in available_set:
                requirements[category] = max(1, int(requirements.get(category, 0) or 0))
    if not furniture_disabled:
        requirements = _apply_furniture_design_quantity_rules(
            requirements,
            config=config,
            available_categories=available_categories,
        )
    observed_poi_counts = _observed_poi_counts(poi_context)
    control_points: List[str] = ["entry", "midblock", "exit"]
    merged_goals = _merge_goals(str(config.query), tuple(defaults["design_goals"]))
    if furniture_disabled:
        reserved_band_categories = {}
    else:
        reserved_band_categories, merged_goals = _apply_observed_poi_bindings(
            requirements=requirements,
            observed_poi_counts=observed_poi_counts,
            poi_context=poi_context,
            control_points=control_points,
            merged_goals=merged_goals,
            bands=bands,
            profile_name=profile_name,
        )
    throughput_requirements = _throughput_requirements(config, profile_name, max(1, int(config.lane_count)))
    band_bounds = _default_band_bounds(
        bands,
        config=config,
        profile_name=profile_name,
        throughput_requirements=throughput_requirements,
    )
    topology_requirements = _topology_requirements(profile_name, bands)

    notes = tuple(
        item
        for item in (
            "heuristic_program_generator_v1",
            f"profile={profile_name}",
            "observed_poi_binding_v1",
            f"objective_profile={str(getattr(config, 'objective_profile', 'balanced')).strip().lower() or 'balanced'}",
            "street_furniture_disabled" if furniture_disabled else "",
        )
        if item
    )
    return StreetProgram(
        query=str(config.query),
        road_type=road_type,
        city_context=str(config.city_context),
        target_standard=profile_name,
        lane_count=max(1, int(config.lane_count)),
        cross_section_type=str(defaults["cross_section_type"]),
        road_width_m=float(carriageway_width),
        sidewalk_width_m=float(max(left_clear_width, right_clear_width)),
        furnishing_width_m=float(max(left_furnishing_width, right_furnishing_width)),
        bands=bands,
        furniture_requirements=requirements,
        control_points=tuple(control_points),
        design_goals=merged_goals,
        context_conditions={
            "layout_mode": str(config.layout_mode),
            "city_context": str(config.city_context),
            "target_street_type": str(config.target_street_type),
            "program_generator": str(config.program_generator),
            "objective_profile": str(getattr(config, "objective_profile", "balanced")),
            "ped_demand_level": str(getattr(config, "ped_demand_level", "medium")),
            "bike_demand_level": str(getattr(config, "bike_demand_level", "low")),
            "transit_demand_level": str(getattr(config, "transit_demand_level", "medium")),
            "vehicle_demand_level": str(getattr(config, "vehicle_demand_level", "medium")),
        },
        objective_profile=str(getattr(config, "objective_profile", "balanced")).strip().lower() or "balanced",
        throughput_requirements=throughput_requirements,
        band_bounds=band_bounds,
        topology_requirements=topology_requirements,
        observed_poi_counts=observed_poi_counts,
        reserved_band_categories=reserved_band_categories,
        design_goal_weights=_goal_weights(merged_goals),
        notes=notes,
        left_clear_path_width_m=float(left_clear_width),
        right_clear_path_width_m=float(right_clear_width),
        left_furnishing_width_m=float(left_furnishing_width),
        right_furnishing_width_m=float(right_furnishing_width),
        row_width_m=float(row_width),
        width_expanded=bool(width_expanded),
        width_reallocation_reason=width_reallocation_reason,
        poi_fit_feasible=bool(poi_fit_feasible),
        poi_fit_report=poi_fit_report,
    )
