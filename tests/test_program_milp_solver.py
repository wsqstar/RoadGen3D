from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.design_rules import load_constraint_set
from roadgen3d.layout_solver import LayoutSolverRuntime
from roadgen3d.street_program import infer_street_program
from roadgen3d.types import (
    InventorySummary,
    LayoutSolverInput,
    RoadSegmentBand,
    RoadSegmentEdge,
    RoadSegmentGraph,
    RoadSegmentNode,
    StreetComposeConfig,
)


def _inventory(*categories: str) -> InventorySummary:
    return InventorySummary(
        category_counts={category: 2 for category in categories},
        asset_ids_by_category={category: (f"{category}_01", f"{category}_02") for category in categories},
    )


def _config(
    *,
    profile: str = "balanced_complete_street_v1",
    layout_mode: str = "template",
    allow_fallback: bool = True,
    layout_solver: str = "milp_template_v1",
    objective_profile: str = "balanced",
) -> StreetComposeConfig:
    return StreetComposeConfig(
        query="pedestrian-friendly boulevard with transit access",
        length_m=60.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        lane_count=2,
        density=1.0,
        seed=42,
        topk_per_category=20,
        max_trials_per_slot=30,
        layout_mode=layout_mode,
        aoi_bbox=(0.0, 0.0, 0.01, 0.01) if layout_mode == "osm" else None,
        design_rule_profile=profile,
        layout_solver=layout_solver,
        objective_profile=objective_profile,
        allow_solver_fallback=allow_fallback,
    )


def _band_widths(result) -> dict[str, float]:
    return {
        band.band_name: float(band.width_m)
        for band in result.band_solutions
    }


def _graph(*, allow_bus_stop: bool) -> RoadSegmentGraph:
    allowed = ("bench", "lamp", "tree", "bus_stop") if allow_bus_stop else ("bench",)
    right_kind = "right_transit_edge"
    nodes = (
        RoadSegmentNode(
            segment_id="seg_0000",
            road_id=1,
            start_xy=(0.0, 0.0),
            end_xy=(12.0, 0.0),
            center_xy=(6.0, 0.0),
            length_m=12.0,
            is_junction=False,
            poi_types=("bus_stop",),
            bands=(
                RoadSegmentBand(
                    band_id="seg_0000_left",
                    segment_id="seg_0000",
                    side="left",
                    kind="left_furnishing",
                    width_m=2.5,
                    allowed_categories=("bench", "lamp", "tree"),
                    nearest_poi_types=("entrance",),
                ),
                RoadSegmentBand(
                    band_id="seg_0000_right",
                    segment_id="seg_0000",
                    side="right",
                    kind=right_kind,
                    width_m=2.5,
                    allowed_categories=allowed,
                    nearest_poi_types=("bus_stop",),
                ),
            ),
        ),
        RoadSegmentNode(
            segment_id="seg_0001",
            road_id=1,
            start_xy=(12.0, 0.0),
            end_xy=(24.0, 0.0),
            center_xy=(18.0, 0.0),
            length_m=12.0,
            is_junction=True,
            poi_types=(),
            bands=(
                RoadSegmentBand(
                    band_id="seg_0001_left",
                    segment_id="seg_0001",
                    side="left",
                    kind="left_furnishing",
                    width_m=2.5,
                    allowed_categories=("bench", "lamp", "tree"),
                ),
                RoadSegmentBand(
                    band_id="seg_0001_right",
                    segment_id="seg_0001",
                    side="right",
                    kind=right_kind,
                    width_m=2.5,
                    allowed_categories=allowed,
                ),
            ),
        ),
    )
    edges = (
        RoadSegmentEdge(edge_id="edge_0000", from_segment_id="seg_0000", to_segment_id="seg_0001", weight=1.0),
    )
    return RoadSegmentGraph(nodes=nodes, edges=edges, mode="osm")


def test_milp_template_solver_returns_feasible_template_solution():
    config = _config(profile="balanced_complete_street_v1")
    available = ("bench", "lamp", "trash", "tree", "mailbox")
    program = infer_street_program(config, available)
    runtime = LayoutSolverRuntime(backend="milp_template_v1")

    result = runtime.solve(
        LayoutSolverInput(
            program=program,
            config=config,
            available_categories=available,
            constraint_set=load_constraint_set("balanced_complete_street_v1"),
            inventory_summary=_inventory(*available),
        )
    )

    assert result.backend_requested == "milp_template_v1"
    assert result.backend_used == "milp_template_v1"
    assert result.slot_plans
    assert all(slot.category in available for slot in result.slot_plans)


def test_milp_template_solver_reports_segment_graph_summary_for_osm():
    config = _config(profile="transit_priority_v1", layout_mode="osm")
    available = ("bench", "lamp", "tree", "bus_stop")
    program = infer_street_program(config, available)
    runtime = LayoutSolverRuntime(backend="milp_template_v1")

    result = runtime.solve(
        LayoutSolverInput(
            program=program,
            config=config,
            available_categories=available,
            constraint_set=load_constraint_set("transit_priority_v1"),
            inventory_summary=_inventory(*available),
            road_segment_graph=_graph(allow_bus_stop=True),
        )
    )

    assert result.backend_used == "milp_template_v1"
    assert result.road_segment_graph_summary is not None
    assert result.road_segment_graph_summary["segment_count"] == 2
    assert result.road_segment_graph_summary["edge_count"] == 1


def test_milp_template_solver_falls_back_to_banded_when_graph_assignment_is_infeasible():
    config = _config(profile="transit_priority_v1", layout_mode="osm", allow_fallback=True)
    available = ("bench", "bus_stop")
    base_program = infer_street_program(config, available)
    program = replace(base_program, furniture_requirements={"bus_stop": 1})
    runtime = LayoutSolverRuntime(backend="milp_template_v1")

    result = runtime.solve(
        LayoutSolverInput(
            program=program,
            config=config,
            available_categories=available,
            constraint_set=load_constraint_set("transit_priority_v1"),
            inventory_summary=_inventory(*available),
            road_segment_graph=_graph(allow_bus_stop=False),
        )
    )

    assert result.backend_requested == "milp_template_v1"
    assert result.backend_used == "banded"
    assert "fallback" in result.fallback_reason.lower()
    assert result.slot_plans


def test_milp_template_solver_returns_conflict_without_fallback_when_infeasible():
    config = _config(profile="transit_priority_v1", layout_mode="osm", allow_fallback=False)
    available = ("bench", "bus_stop")
    base_program = infer_street_program(config, available)
    program = replace(base_program, furniture_requirements={"bus_stop": 1})
    runtime = LayoutSolverRuntime(backend="milp_template_v1")

    result = runtime.solve(
        LayoutSolverInput(
            program=program,
            config=config,
            available_categories=available,
            constraint_set=load_constraint_set("transit_priority_v1"),
            inventory_summary=_inventory(*available),
            road_segment_graph=_graph(allow_bus_stop=False),
        )
    )

    assert result.backend_used == "milp_template_v1"
    assert not result.slot_plans
    assert result.conflicts


def test_milp_template_solver_falls_back_to_banded_for_poi_anchored_slots():
    config = _config(profile="transit_priority_v1", layout_mode="osm", allow_fallback=True)
    available = ("bench", "lamp", "tree", "bus_stop", "hydrant")
    poi_context = SimpleNamespace(
        entrance_points_xz=((0.0, -1.5),),
        bus_stop_points_xz=((10.0, -1.0),),
        fire_points_xz=(),
    )
    placement_context = SimpleNamespace(
        entrance_points=[(0.0, -1.5)],
        bus_stop_points=[(10.0, -1.0)],
        fire_points=[],
    )
    program = infer_street_program(config, available, poi_context=poi_context)
    runtime = LayoutSolverRuntime(backend="milp_template_v1")

    result = runtime.solve(
        LayoutSolverInput(
            program=program,
            config=config,
            available_categories=available,
            constraint_set=load_constraint_set("transit_priority_v1"),
            inventory_summary=_inventory(*available),
            placement_context=placement_context,
            road_segment_graph=_graph(allow_bus_stop=True),
        )
    )

    assert result.backend_requested == "milp_template_v1"
    assert result.backend_used == "banded"
    assert "poi-backed anchored slots" in result.fallback_reason.lower()
    assert any(slot.anchor_poi_type == "bus_stop" for slot in result.slot_plans)


def test_hybrid_solver_reports_band_solutions_and_throughput_feasibility():
    config = _config(
        profile="balanced_complete_street_v1",
        layout_solver="hybrid_milp_v1",
        objective_profile="balanced",
    )
    available = ("bench", "lamp", "trash", "tree", "bus_stop", "bollard")
    program = infer_street_program(config, available)
    runtime = LayoutSolverRuntime(backend="hybrid_milp_v1")

    result = runtime.solve(
        LayoutSolverInput(
            program=program,
            config=config,
            available_categories=available,
            constraint_set=load_constraint_set("balanced_complete_street_v1"),
            inventory_summary=_inventory(*available),
        )
    )

    assert result.backend_used == "hybrid_milp_v1"
    assert result.band_solutions
    assert result.throughput_feasibility["overall_satisfied"] is True
    assert result.active_constraints
    for band in result.band_solutions:
        assert float(band.min_width_m) <= float(band.width_m) <= float(band.max_width_m)


def test_hybrid_objective_profiles_change_band_widths_and_slot_mix():
    available = ("bench", "lamp", "trash", "tree", "bus_stop", "bollard")
    runtime = LayoutSolverRuntime(backend="hybrid_milp_v1")

    greening_cfg = _config(
        profile="balanced_complete_street_v1",
        layout_solver="hybrid_milp_v1",
        objective_profile="greening",
    )
    commerce_cfg = _config(
        profile="balanced_complete_street_v1",
        layout_solver="hybrid_milp_v1",
        objective_profile="commerce",
    )
    transit_cfg = _config(
        profile="transit_priority_v1",
        layout_solver="hybrid_milp_v1",
        objective_profile="transit",
    )

    greening = runtime.solve(
        LayoutSolverInput(
            program=infer_street_program(greening_cfg, available),
            config=greening_cfg,
            available_categories=available,
            constraint_set=load_constraint_set("balanced_complete_street_v1"),
            inventory_summary=_inventory(*available),
        )
    )
    commerce = runtime.solve(
        LayoutSolverInput(
            program=infer_street_program(commerce_cfg, available),
            config=commerce_cfg,
            available_categories=available,
            constraint_set=load_constraint_set("balanced_complete_street_v1"),
            inventory_summary=_inventory(*available),
        )
    )
    transit = runtime.solve(
        LayoutSolverInput(
            program=infer_street_program(transit_cfg, available),
            config=transit_cfg,
            available_categories=available,
            constraint_set=load_constraint_set("transit_priority_v1"),
            inventory_summary=_inventory(*available),
        )
    )

    greening_bench_slots = sum(1 for slot in greening.slot_plans if slot.category == "bench")
    commerce_bench_slots = sum(1 for slot in commerce.slot_plans if slot.category == "bench")
    transit_bus_slots = sum(1 for slot in transit.slot_plans if slot.category == "bus_stop")

    assert commerce_bench_slots > greening_bench_slots
    assert transit_bus_slots >= 1
    assert _band_widths(greening) != _band_widths(commerce)


def test_hybrid_solver_respects_keepout_rules_for_template_candidates():
    config = _config(
        profile="balanced_complete_street_v1",
        layout_mode="osm",
        layout_solver="hybrid_milp_v1",
        objective_profile="commerce",
    )
    available = ("bench", "lamp", "trash", "tree", "bus_stop", "bollard")
    placement_context = SimpleNamespace(
        entrance_points=[(0.0, 4.5)],
        bus_stop_points=[],
        fire_points=[],
    )
    program = infer_street_program(config, available)
    runtime = LayoutSolverRuntime(backend="hybrid_milp_v1")

    result = runtime.solve(
        LayoutSolverInput(
            program=program,
            config=config,
            available_categories=available,
            constraint_set=load_constraint_set("balanced_complete_street_v1"),
            inventory_summary=_inventory(*available),
            placement_context=placement_context,
        )
    )

    bench_slots = [slot for slot in result.slot_plans if slot.category == "bench"]
    assert bench_slots
    assert all(abs(float(slot.x_center_m)) >= 1.8 for slot in bench_slots)
