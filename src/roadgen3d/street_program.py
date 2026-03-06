"""StreetProgram generation for the neuralsymbolic street pipeline."""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Sequence, Tuple

from .street_priors import DEFAULT_CATEGORIES, DEFAULT_SPACING_M
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


def _profile_defaults(profile_name: str) -> Dict[str, object]:
    return dict(_PROFILE_DEFAULTS.get(profile_name, _PROFILE_DEFAULTS["balanced_complete_street_v1"]))


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


def _build_cross_section_bands(
    *,
    road_width_m: float,
    sidewalk_width_m: float,
    furnishing_width_m: float,
    right_edge_width_m: float,
    profile_name: str,
) -> Tuple[StreetBand, ...]:
    left_edge = float(furnishing_width_m)
    right_edge = float(right_edge_width_m)
    clear_width = float(sidewalk_width_m)
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
            width_m=clear_width,
            z_center_m=road_half + left_edge + clear_width / 2.0,
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
            width_m=clear_width,
            z_center_m=-(road_half + right_edge + clear_width / 2.0),
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


def infer_street_program(
    config: StreetComposeConfig,
    available_categories: Iterable[str],
) -> StreetProgram:
    """Infer a structured StreetProgram from text and composition context."""

    profile_name = str(config.design_rule_profile).strip().lower() or "balanced_complete_street_v1"
    defaults = _profile_defaults(profile_name)
    road_type = _infer_road_type(config.query, config.target_street_type)
    clear_width = max(float(config.sidewalk_width_m), float(defaults["min_clear_path_width_m"]))
    furnishing_width = float(defaults["furnishing_width_m"])
    right_edge_width = float(defaults.get("right_edge_width_m", furnishing_width))

    bands = _build_cross_section_bands(
        road_width_m=float(config.road_width_m),
        sidewalk_width_m=clear_width,
        furnishing_width_m=furnishing_width,
        right_edge_width_m=right_edge_width,
        profile_name=profile_name,
    )
    requirements = _estimate_furniture_requirements(
        query=config.query,
        length_m=float(config.length_m),
        density=float(config.density),
        available_categories=available_categories,
        profile_scales=dict(defaults["density_scales"]),
        required_categories=tuple(defaults["required_categories"]),
    )
    control_points: List[str] = ["entry", "midblock", "exit"]
    if requirements.get("bus_stop", 0) > 0:
        control_points.append("transit_stop")
    merged_goals = _merge_goals(str(config.query), tuple(defaults["design_goals"]))
    reserved_band_categories: Dict[str, str] = {}
    if profile_name == "transit_priority_v1":
        reserved_band_categories["right_transit_edge"] = "bus_stop"

    notes = (
        "heuristic_program_generator_v1",
        f"profile={profile_name}",
    )
    return StreetProgram(
        query=str(config.query),
        road_type=road_type,
        city_context=str(config.city_context),
        target_standard=profile_name,
        lane_count=max(1, int(config.lane_count)),
        cross_section_type=str(defaults["cross_section_type"]),
        road_width_m=float(config.road_width_m),
        sidewalk_width_m=float(clear_width),
        furnishing_width_m=float(max(furnishing_width, right_edge_width)),
        bands=bands,
        furniture_requirements=requirements,
        control_points=tuple(control_points),
        design_goals=merged_goals,
        context_conditions={
            "layout_mode": str(config.layout_mode),
            "city_context": str(config.city_context),
            "target_street_type": str(config.target_street_type),
            "program_generator": str(config.program_generator),
        },
        reserved_band_categories=reserved_band_categories,
        design_goal_weights=_goal_weights(merged_goals),
        notes=notes,
    )
