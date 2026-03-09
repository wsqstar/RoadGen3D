"""POI constraint rules engine for M5 soft-constraint placement scoring."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .poi_taxonomy import (
    CANONICAL_FIRE_POI,
    extract_poi_points_by_type,
    normalize_poi_points_by_type,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PoiRule:
    """One POI constraint rule."""

    name: str  # e.g. "entrance_clearance"
    poi_type: str  # "entrance" | "fire" | "bus_stop"
    clearance_sigma_m: float  # sigma for exponential decay
    affected_categories: Dict[str, float]  # category -> w_cat penalty weight


@dataclass(frozen=True)
class PoiRuleSet:
    """A named set of POI rules."""

    rules: Tuple[PoiRule, ...]


@dataclass(frozen=True)
class PoiContext:
    """Lightweight collection of POI positions for scoring."""

    entrance_points_xz: Tuple[Tuple[float, float], ...]
    bus_stop_points_xz: Tuple[Tuple[float, float], ...]
    fire_points_xz: Tuple[Tuple[float, float], ...]
    poi_points_by_type_xz: Dict[str, Tuple[Tuple[float, float], ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        mapping = normalize_poi_points_by_type(self.poi_points_by_type_xz or {})
        if not mapping.get("entrance"):
            mapping["entrance"] = list(self.entrance_points_xz)
        if not mapping.get("bus_stop"):
            mapping["bus_stop"] = list(self.bus_stop_points_xz)
        if not mapping.get(CANONICAL_FIRE_POI):
            mapping[CANONICAL_FIRE_POI] = list(self.fire_points_xz)
        object.__setattr__(
            self,
            "poi_points_by_type_xz",
            {
                poi_type: tuple(points)
                for poi_type, points in mapping.items()
            },
        )


@dataclass(frozen=True)
class ConstraintResult:
    """Result of evaluating all rules for one candidate placement."""

    penalty: float  # total accumulated penalty (>= 0)
    feasibility_score: float  # exp(-penalty) in [0, 1]
    violated_rules: Tuple[str, ...]  # names of rules whose penalty_r exceeded threshold


# ---------------------------------------------------------------------------
# Rule set definitions
# ---------------------------------------------------------------------------

_MULTITYPE_STREET_POI_V2 = PoiRuleSet(
    rules=(
        PoiRule(
            name="entrance_clearance",
            poi_type="entrance",
            clearance_sigma_m=2.5,
            affected_categories={
                "tree": 1.0,
                "lamp": 0.8,
                "bench": 0.7,
                "trash": 0.7,
                "bollard": 0.9,
                "mailbox": 0.5,
                "bus_stop": 1.0,
                "hydrant": 0.2,
            },
        ),
        PoiRule(
            name="fire_access",
            poi_type=CANONICAL_FIRE_POI,
            clearance_sigma_m=3.0,
            affected_categories={
                "tree": 1.0,
                "lamp": 1.0,
                "bench": 1.0,
                "trash": 1.0,
                "bollard": 1.0,
                "mailbox": 1.0,
                "bus_stop": 1.0,
                "hydrant": 0.0,  # hydrant near fire hydrant is fine
            },
        ),
        PoiRule(
            name="bus_stop_clearance",
            poi_type="bus_stop",
            clearance_sigma_m=4.0,
            affected_categories={
                "tree": 0.9,
                "bollard": 0.6,
                "trash": 0.4,
                "lamp": 0.3,
                "bench": 0.2,
                "mailbox": 0.4,
                "hydrant": 0.2,
                "bus_stop": 0.0,  # bus_stop near bus_stop POI is expected
            },
        ),
        PoiRule(
            name="crossing_keep_clear",
            poi_type="crossing",
            clearance_sigma_m=3.0,
            affected_categories={
                "tree": 1.0,
                "bench": 1.0,
                "trash": 1.0,
                "mailbox": 1.0,
                "bus_stop": 1.0,
                "hydrant": 1.0,
                "bollard": 1.0,
            },
        ),
        PoiRule(
            name="traffic_signal_visibility",
            poi_type="traffic_signals",
            clearance_sigma_m=3.0,
            affected_categories={
                "tree": 1.0,
                "bus_stop": 0.9,
                "mailbox": 0.8,
                "lamp": 0.2,
            },
        ),
        PoiRule(
            name="parking_entrance_clearance",
            poi_type="parking_entrance",
            clearance_sigma_m=3.5,
            affected_categories={
                "tree": 1.0,
                "lamp": 0.8,
                "bench": 0.7,
                "trash": 0.7,
                "bollard": 0.9,
                "mailbox": 0.6,
                "bus_stop": 1.0,
                "hydrant": 0.2,
            },
        ),
        PoiRule(
            name="subway_entrance_clearance",
            poi_type="subway_entrance",
            clearance_sigma_m=3.0,
            affected_categories={
                "tree": 1.0,
                "lamp": 0.8,
                "bench": 0.8,
                "trash": 0.7,
                "bollard": 0.9,
                "mailbox": 0.6,
                "bus_stop": 1.0,
                "hydrant": 0.2,
            },
        ),
        PoiRule(
            name="post_box_access",
            poi_type="post_box",
            clearance_sigma_m=2.0,
            affected_categories={
                "tree": 0.8,
                "bus_stop": 1.0,
            },
        ),
        PoiRule(
            name="waste_basket_clearance",
            poi_type="waste_basket",
            clearance_sigma_m=2.0,
            affected_categories={
                "bus_stop": 0.8,
                "tree": 0.5,
            },
        ),
    )
)

_RULE_SETS: Dict[str, PoiRuleSet] = {
    "entrance_fire_bus_stop_v1": _MULTITYPE_STREET_POI_V2,
    "multitype_street_poi_v2": _MULTITYPE_STREET_POI_V2,
}

# Threshold for considering a single rule "violated" (per-rule penalty_r).
_VIOLATION_THRESHOLD = 0.3


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_rule_set(name: str = "entrance_fire_bus_stop_v1") -> PoiRuleSet:
    """Load a named POI rule set."""
    rs = _RULE_SETS.get(name)
    if rs is None:
        raise ValueError(f"Unknown POI rule set: {name!r}. Available: {list(_RULE_SETS)}")
    return rs


def _min_distance(pos: Tuple[float, float], points: Tuple[Tuple[float, float], ...]) -> float:
    """Euclidean distance from *pos* to the closest point in *points*.

    Returns ``float('inf')`` when *points* is empty.
    """
    if not points:
        return float("inf")
    px, pz = pos
    return min(math.hypot(px - qx, pz - qz) for qx, qz in points)


def _get_poi_points(poi_type: str, ctx: PoiContext) -> Tuple[Tuple[float, float], ...]:
    mapping = normalize_poi_points_by_type(getattr(ctx, "poi_points_by_type_xz", {}) or {})
    return tuple(mapping.get(poi_type, ()))


def score_placement(
    position_xz: Tuple[float, float],
    category: str,
    rule_set: PoiRuleSet,
    poi_context: PoiContext,
) -> ConstraintResult:
    """Evaluate all rules and return the aggregated constraint result.

    Parameters
    ----------
    position_xz : (x, z) in local metres.
    category : asset category string (e.g. "bench").
    rule_set : the set of rules to apply.
    poi_context : POI point coordinates.

    Returns
    -------
    ConstraintResult with total penalty, feasibility score, and list of
    violated rule names.
    """
    total_penalty = 0.0
    violated: list[str] = []

    for rule in rule_set.rules:
        w_cat = rule.affected_categories.get(category)
        if w_cat is None or w_cat <= 0.0:
            continue  # category not affected by this rule

        poi_pts = _get_poi_points(rule.poi_type, poi_context)
        if not poi_pts:
            continue  # no POI of this type → no penalty

        d = _min_distance(position_xz, poi_pts)
        sigma = max(rule.clearance_sigma_m / 2.0, 1e-6)  # sigma_r = radius / 2
        penalty_r = w_cat * math.exp(-d / sigma)
        total_penalty += penalty_r

        if penalty_r > _VIOLATION_THRESHOLD:
            violated.append(rule.name)

    feasibility = math.exp(-total_penalty)
    return ConstraintResult(
        penalty=total_penalty,
        feasibility_score=feasibility,
        violated_rules=tuple(violated),
    )


# ---------------------------------------------------------------------------
# Exclusion zone computation (for visualization)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PoiExclusionInfo:
    """One POI point with its computed exclusion-zone radius."""

    poi_type: str  # "entrance" | "fire" | "bus_stop"
    position_xz: Tuple[float, float]
    radius_m: float  # red-line boundary radius
    rule_name: str


def compute_exclusion_radii(
    rule_set: PoiRuleSet,
    threshold: float = _VIOLATION_THRESHOLD,
) -> Dict[str, float]:
    """Compute the red-line exclusion radius per POI type.

    The radius is the distance *d* at which the worst-case single-rule
    penalty equals *threshold*::

        w_max * exp(-d / sigma) = threshold
        d = sigma * ln(w_max / threshold)

    where ``sigma = clearance_sigma_m / 2``.
    """
    radii: Dict[str, float] = {}
    for rule in rule_set.rules:
        if not rule.affected_categories:
            continue
        w_max = max(rule.affected_categories.values())
        if w_max <= threshold or w_max <= 0.0:
            radii[rule.poi_type] = 0.0
            if rule.poi_type == CANONICAL_FIRE_POI:
                radii["fire"] = 0.0
            continue
        sigma = max(rule.clearance_sigma_m / 2.0, 1e-6)
        radii[rule.poi_type] = sigma * math.log(w_max / threshold)
        if rule.poi_type == CANONICAL_FIRE_POI:
            radii["fire"] = radii[rule.poi_type]
    return radii


def build_exclusion_zones(
    poi_context: PoiContext,
    rule_set: PoiRuleSet,
) -> Tuple[PoiExclusionInfo, ...]:
    """Build a flat list of exclusion zones for every POI point.

    Each POI point gets one ``PoiExclusionInfo`` per applicable rule.
    """
    radii = compute_exclusion_radii(rule_set)
    zones: List[PoiExclusionInfo] = []
    for rule in rule_set.rules:
        r = radii.get(rule.poi_type, 0.0)
        if r <= 0.0:
            continue
        for pt in _get_poi_points(rule.poi_type, poi_context):
            zones.append(PoiExclusionInfo(
                poi_type=rule.poi_type,
                position_xz=pt,
                radius_m=r,
                rule_name=rule.name,
            ))
    return tuple(zones)


def build_poi_context(placement_context: object) -> PoiContext:
    """Build a lightweight PoiContext from a PlacementContext.

    Extracts (x, z) tuples from the shapely-based PlacementContext into
    plain tuples for fast scoring.
    """
    ctx = placement_context  # type: ignore[assignment]
    poi_points = {
        poi_type: tuple(points)
        for poi_type, points in extract_poi_points_by_type(ctx).items()
    }
    return PoiContext(
        entrance_points_xz=tuple(ctx.entrance_points),
        bus_stop_points_xz=tuple(ctx.bus_stop_points),
        fire_points_xz=tuple(ctx.fire_points),
        poi_points_by_type_xz=poi_points,
    )
