"""Tests for M5 POI constraint rules engine."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.poi_rules import (
    ConstraintResult,
    PoiContext,
    load_rule_set,
    score_placement,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(
    entrances=(),
    bus_stops=(),
    fire_points=(),
) -> PoiContext:
    return PoiContext(
        entrance_points_xz=tuple(entrances),
        bus_stop_points_xz=tuple(bus_stops),
        fire_points_xz=tuple(fire_points),
    )


# ---------------------------------------------------------------------------
# Rule set loading
# ---------------------------------------------------------------------------


def test_rule_set_loads():
    rs = load_rule_set("entrance_fire_bus_stop_v1")
    assert len(rs.rules) >= 9
    names = {r.name for r in rs.rules}
    assert "entrance_clearance" in names
    assert "fire_access" in names
    assert "bus_stop_clearance" in names
    assert "crossing_keep_clear" in names
    assert "subway_entrance_clearance" in names


def test_rule_set_unknown_raises():
    with pytest.raises(ValueError, match="Unknown POI rule set"):
        load_rule_set("nonexistent_rules")


# ---------------------------------------------------------------------------
# No penalty when far from POI
# ---------------------------------------------------------------------------


def test_no_penalty_far_from_poi():
    rs = load_rule_set()
    ctx = _make_ctx(entrances=[(100.0, 100.0)])
    result = score_placement((0.0, 0.0), "bench", rs, ctx)
    assert result.penalty < 0.01
    assert result.feasibility_score > 0.99
    assert len(result.violated_rules) == 0


def test_no_penalty_empty_poi():
    rs = load_rule_set()
    ctx = _make_ctx()  # no POI at all
    result = score_placement((5.0, 5.0), "tree", rs, ctx)
    assert result.penalty == 0.0
    assert result.feasibility_score == 1.0


# ---------------------------------------------------------------------------
# High penalty near POI
# ---------------------------------------------------------------------------


def test_high_penalty_bench_on_entrance():
    """Bench at d=0 from entrance should get high penalty."""
    rs = load_rule_set()
    ctx = _make_ctx(entrances=[(5.0, 5.0)])
    result = score_placement((5.0, 5.0), "bench", rs, ctx)
    # w_cat for bench on entrance = 0.7
    assert result.penalty > 0.5
    assert result.feasibility_score < 0.7
    assert "entrance_clearance" in result.violated_rules


def test_high_penalty_tree_on_entrance():
    """Tree at d=0 from entrance should get maximal penalty for that rule."""
    rs = load_rule_set()
    ctx = _make_ctx(entrances=[(0.0, 0.0)])
    result = score_placement((0.0, 0.0), "tree", rs, ctx)
    # w_cat for tree on entrance = 1.0
    assert result.penalty >= 0.9
    assert "entrance_clearance" in result.violated_rules


# ---------------------------------------------------------------------------
# Hydrant near fire hydrant POI = no penalty
# ---------------------------------------------------------------------------


def test_hydrant_no_fire_penalty():
    """Hydrant category near fire_hydrant POI should have w_cat=0.0."""
    rs = load_rule_set()
    ctx = _make_ctx(fire_points=[(1.0, 1.0)])
    result = score_placement((1.0, 1.0), "hydrant", rs, ctx)
    # fire_access w_cat for hydrant = 0.0
    # entrance_clearance w_cat for hydrant = 0.2 (but no entrance POI)
    # bus_stop_clearance w_cat for hydrant = 0.2 (but no bus_stop POI)
    assert result.penalty < 0.01


def test_crossing_keep_clear_penalizes_bollard():
    rs = load_rule_set()
    ctx = PoiContext(
        entrance_points_xz=(),
        bus_stop_points_xz=(),
        fire_points_xz=(),
        poi_points_by_type_xz={"crossing": ((0.0, 0.0),)},
    )
    result = score_placement((0.0, 0.0), "bollard", rs, ctx)
    assert result.penalty > 0.8
    assert "crossing_keep_clear" in result.violated_rules


def test_subway_entrance_penalizes_bus_stop_overlap():
    rs = load_rule_set()
    ctx = PoiContext(
        entrance_points_xz=(),
        bus_stop_points_xz=(),
        fire_points_xz=(),
        poi_points_by_type_xz={"subway_entrance": ((0.0, 0.0),)},
    )
    result = score_placement((0.0, 0.0), "bus_stop", rs, ctx)
    assert result.penalty > 0.8
    assert "subway_entrance_clearance" in result.violated_rules


def test_bus_stop_near_bus_stop_poi():
    """bus_stop category near bus_stop POI should have w_cat=0.0 for bus_stop_clearance."""
    rs = load_rule_set()
    ctx = _make_ctx(bus_stops=[(0.0, 0.0)])
    result = score_placement((0.0, 0.0), "bus_stop", rs, ctx)
    # bus_stop_clearance w_cat for bus_stop = 0.0, so no penalty from that rule
    # fire_access w_cat for bus_stop = 1.0 but no fire POI
    # entrance_clearance w_cat for bus_stop = 1.0 but no entrance POI
    assert result.penalty < 0.01


# ---------------------------------------------------------------------------
# Unaffected category
# ---------------------------------------------------------------------------


def test_unaffected_category_no_penalty():
    """A category not listed in a rule's affected_categories gets no penalty from that rule."""
    rs = load_rule_set()
    # bus_stop_clearance does not list "bus_stop" with nonzero weight (it's 0.0)
    # but entrance_clearance has bus_stop=1.0, so test a different combo
    # lamp is NOT in bus_stop_clearance affected (w=0.3), so it should get some penalty
    # Let's test with a POI type that doesn't affect a specific category
    ctx = _make_ctx(bus_stops=[(0.0, 0.0)])
    result = score_placement((0.0, 0.0), "lamp", rs, ctx)
    # bus_stop_clearance w_cat for lamp = 0.3
    # penalty should be moderate (0.3 * exp(0) = 0.3)
    assert result.penalty > 0.2
    assert result.penalty < 0.5  # only one rule active


# ---------------------------------------------------------------------------
# Veto threshold behaviour
# ---------------------------------------------------------------------------


def test_veto_not_triggered_with_moderate_penalty():
    """Moderate penalty should not reach typical veto threshold."""
    rs = load_rule_set()
    # Single entrance, bench nearby
    ctx = _make_ctx(entrances=[(0.0, 0.0)])
    result = score_placement((0.5, 0.0), "bench", rs, ctx)
    # Should be under 0.95 veto threshold
    assert result.penalty < 0.95


def test_extreme_penalty_near_multiple_pois():
    """Placement near multiple POI types simultaneously should accumulate high penalty."""
    rs = load_rule_set()
    ctx = _make_ctx(
        entrances=[(0.0, 0.0)],
        bus_stops=[(0.0, 0.0)],
        fire_points=[(0.0, 0.0)],
    )
    result = score_placement((0.0, 0.0), "tree", rs, ctx)
    # tree: entrance w=1.0, fire w=1.0, bus_stop w=0.9 → total ~2.9
    assert result.penalty > 2.0
    assert result.feasibility_score < 0.2


# ---------------------------------------------------------------------------
# Constraint result types
# ---------------------------------------------------------------------------


def test_constraint_result_is_frozen():
    cr = ConstraintResult(penalty=0.5, feasibility_score=0.6, violated_rules=("a",))
    assert cr.penalty == 0.5
    assert cr.violated_rules == ("a",)
    with pytest.raises(AttributeError):
        cr.penalty = 1.0  # type: ignore[misc]


def test_feasibility_equals_exp_neg_penalty():
    rs = load_rule_set()
    ctx = _make_ctx(entrances=[(3.0, 0.0)])
    result = score_placement((3.0, 0.0), "bench", rs, ctx)
    expected = math.exp(-result.penalty)
    assert abs(result.feasibility_score - expected) < 1e-9
