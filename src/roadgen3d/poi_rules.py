"""POI constraint rules engine for M5 soft-constraint placement scoring."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Tuple


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


@dataclass(frozen=True)
class ConstraintResult:
    """Result of evaluating all rules for one candidate placement."""

    penalty: float  # total accumulated penalty (>= 0)
    feasibility_score: float  # exp(-penalty) in [0, 1]
    violated_rules: Tuple[str, ...]  # names of rules whose penalty_r exceeded threshold


# ---------------------------------------------------------------------------
# Rule set definitions
# ---------------------------------------------------------------------------

_ENTRANCE_FIRE_BUS_STOP_V1 = PoiRuleSet(
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
            poi_type="fire",
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
    )
)

_RULE_SETS: Dict[str, PoiRuleSet] = {
    "entrance_fire_bus_stop_v1": _ENTRANCE_FIRE_BUS_STOP_V1,
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
    if poi_type == "entrance":
        return ctx.entrance_points_xz
    elif poi_type == "fire":
        return ctx.fire_points_xz
    elif poi_type == "bus_stop":
        return ctx.bus_stop_points_xz
    return ()


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


def build_poi_context(placement_context: object) -> PoiContext:
    """Build a lightweight PoiContext from a PlacementContext.

    Extracts (x, z) tuples from the shapely-based PlacementContext into
    plain tuples for fast scoring.
    """
    ctx = placement_context  # type: ignore[assignment]
    return PoiContext(
        entrance_points_xz=tuple(ctx.entrance_points),
        bus_stop_points_xz=tuple(ctx.bus_stop_points),
        fire_points_xz=tuple(ctx.fire_points),
    )
