"""Constraint-aware layout solver for the neuralsymbolic street pipeline."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set, Tuple

from .milp_solver import solve_candidate_assignment
from .poi_taxonomy import (
    asset_backed_poi_types_for_category,
    cluster_asset_backed_poi_points,
    extract_poi_points_by_type,
)
from .street_band_semantics import (
    band_name_matches,
    coerce_band_rule_kinds,
    detailed_strip_kind_from_band_name,
    resolve_band_by_alias,
)
from .street_priors import CATEGORY_SUBSTITUTIONS, SIDE_PREF
from .types import (
    BandSolution,
    ConstraintSet,
    DesignRuleSpec,
    LayoutConflict,
    LayoutEdit,
    LayoutSlotPlan,
    LayoutSolverInput,
    LayoutSolverResult,
    RuleEvaluation,
    StreetBand,
    StreetProgram,
)

try:
    import pulp
except ImportError:
    pulp = None


_BALANCED_FURNITURE_CATEGORIES = {
    category for category, side_pref in SIDE_PREF.items() if str(side_pref) == "both"
}


def _rule_parameter(rule: DesignRuleSpec, key: str, default: object = None) -> object:
    return rule.parameters.get(key, default)


def _apply_numeric_rule(actual: float, operator: str, target: float) -> Tuple[float, bool]:
    if operator == "<=" and actual > target:
        return float(target), True
    if operator == ">=" and actual < target:
        return float(target), True
    if operator == "=" and actual != target:
        return float(target), True
    return float(actual), False


def _recompute_bands(bands: Sequence[StreetBand]) -> Tuple[StreetBand, ...]:
    ordered = list(bands)
    road_band = next((band for band in ordered if band.kind == "carriageway"), None)
    road_half = float(road_band.width_m) / 2.0 if road_band is not None else 0.0

    left_bands = sorted(
        [band for band in ordered if band.side == "left"],
        key=lambda item: abs(float(item.z_center_m)),
    )
    right_bands = sorted(
        [band for band in ordered if band.side == "right"],
        key=lambda item: abs(float(item.z_center_m)),
    )

    left_centers: Dict[str, float] = {}
    right_centers: Dict[str, float] = {}

    left_offset = road_half
    for band in left_bands:
        left_centers[band.name] = left_offset + float(band.width_m) / 2.0
        left_offset += float(band.width_m)

    right_offset = road_half
    for band in right_bands:
        right_centers[band.name] = -(right_offset + float(band.width_m) / 2.0)
        right_offset += float(band.width_m)

    rebuilt: List[StreetBand] = []
    for band in ordered:
        if band.side == "left":
            rebuilt.append(replace(band, z_center_m=float(left_centers[band.name])))
        elif band.side == "right":
            rebuilt.append(replace(band, z_center_m=float(right_centers[band.name])))
        else:
            rebuilt.append(replace(band, z_center_m=0.0))
    return tuple(rebuilt)


def _band_width_for_alias(
    bands: Sequence[StreetBand],
    *,
    band_name: str,
    side: str = "",
    fallback: float = 0.0,
) -> float:
    band = resolve_band_by_alias(bands, band_name=band_name, side=side)
    if band is None:
        return float(fallback)
    return float(getattr(band, "width_m", fallback) or fallback)


def _rebuild_program(
    program: StreetProgram,
    *,
    lane_count: int,
    bands: Sequence[StreetBand],
    requirements: Dict[str, int],
) -> StreetProgram:
    rebuilt_bands = _recompute_bands(bands)
    clear_widths = [
        float(band.width_m)
        for band in rebuilt_bands
        if "clear_path" in coerce_band_rule_kinds(band.name, band.kind)
    ]
    furnishing_widths = [
        float(band.width_m)
        for band in rebuilt_bands
        if set(coerce_band_rule_kinds(band.name, band.kind)).intersection({"furnishing", "transit_edge"})
    ]
    left_clear_width = _band_width_for_alias(
        rebuilt_bands,
        band_name="clear_sidewalk",
        side="left",
        fallback=program.left_clear_path_width_m,
    )
    right_clear_width = _band_width_for_alias(
        rebuilt_bands,
        band_name="clear_sidewalk",
        side="right",
        fallback=program.right_clear_path_width_m,
    )
    left_furnishing_width = _band_width_for_alias(
        rebuilt_bands,
        band_name="nearroad_furnishing",
        side="left",
        fallback=program.left_furnishing_width_m,
    )
    right_furnishing_width = _band_width_for_alias(
        rebuilt_bands,
        band_name="nearroad_furnishing",
        side="right",
        fallback=program.right_furnishing_width_m,
    )
    carriageway_width = float(next((band.width_m for band in rebuilt_bands if band.kind == "carriageway"), program.road_width_m))
    return StreetProgram(
        query=program.query,
        road_type=program.road_type,
        city_context=program.city_context,
        target_standard=program.target_standard,
        lane_count=int(max(1, lane_count)),
        cross_section_type=program.cross_section_type,
        road_width_m=carriageway_width,
        sidewalk_width_m=float(max(clear_widths) if clear_widths else program.sidewalk_width_m),
        furnishing_width_m=float(max(furnishing_widths) if furnishing_widths else program.furnishing_width_m),
        bands=rebuilt_bands,
        furniture_requirements=dict(requirements),
        control_points=program.control_points,
        design_goals=program.design_goals,
        context_conditions=dict(program.context_conditions),
        objective_profile=str(program.objective_profile),
        throughput_requirements=dict(program.throughput_requirements),
        band_bounds={key: dict(value) for key, value in program.band_bounds.items()},
        topology_requirements=dict(program.topology_requirements),
        observed_poi_counts=dict(program.observed_poi_counts),
        reserved_band_categories=dict(program.reserved_band_categories),
        design_goal_weights=dict(program.design_goal_weights),
        notes=program.notes,
        left_clear_path_width_m=left_clear_width,
        right_clear_path_width_m=right_clear_width,
        left_furnishing_width_m=left_furnishing_width,
        right_furnishing_width_m=right_furnishing_width,
        row_width_m=float(carriageway_width + sum(float(band.width_m) for band in rebuilt_bands if band.side in {"left", "right"})),
        width_expanded=bool(program.width_expanded),
        width_reallocation_reason=str(program.width_reallocation_reason),
        poi_fit_feasible=bool(program.poi_fit_feasible),
        poi_fit_report=dict(program.poi_fit_report),
        theme_segments=tuple(program.theme_segments),
        building_strategy_summary=dict(program.building_strategy_summary),
    )


def _find_bands_by_kind(bands: Sequence[StreetBand], band_kind: str) -> List[StreetBand]:
    return [band for band in bands if band.kind == band_kind]


def _allowed_band_map(constraint_set: ConstraintSet) -> Dict[str, Tuple[str, ...]]:
    allowed: Dict[str, Tuple[str, ...]] = {}
    for rule in constraint_set.rules:
        if rule.target != "category_allowed_band":
            continue
        category = str(_rule_parameter(rule, "category", "all"))
        values = tuple(str(item) for item in (rule.value or ()))
        allowed[category] = values
    return allowed


def _reserved_band_map(constraint_set: ConstraintSet) -> Dict[str, str]:
    reserved: Dict[str, str] = {}
    for rule in constraint_set.rules:
        if rule.target != "reserved_band_category":
            continue
        band_kind = str(_rule_parameter(rule, "band_kind", "")).strip()
        if band_kind:
            reserved[band_kind] = str(rule.value)
    return reserved


def _required_categories(constraint_set: ConstraintSet) -> Set[str]:
    required: Set[str] = set()
    for rule in constraint_set.rules:
        if rule.target in {"category_min_count", "slot_count_min"} and rule.mode == "hard":
            category = str(_rule_parameter(rule, "category", "")).strip()
            if category:
                required.add(category)
        if rule.target == "required_category_available" and rule.mode == "hard":
            category = str(_rule_parameter(rule, "category", "")).strip()
            if category:
                required.add(category)
    return required


def _band_bound_map(program: StreetProgram, constraint_set: ConstraintSet) -> Dict[str, Dict[str, Any]]:
    bounds: Dict[str, Dict[str, Any]] = {
        band.name: {
            "min_width_m": float((program.band_bounds.get(band.name) or {}).get("min_width_m", max(0.5, float(band.width_m)))),
            "max_width_m": float((program.band_bounds.get(band.name) or {}).get("max_width_m", max(0.5, float(band.width_m)))),
            "active_constraint_names": [],
        }
        for band in program.bands
    }
    for band in program.bands:
        bounds[band.name]["min_width_m"] = min(float(bounds[band.name]["min_width_m"]), float(bounds[band.name]["max_width_m"]))
        bounds[band.name]["max_width_m"] = max(float(bounds[band.name]["min_width_m"]), float(bounds[band.name]["max_width_m"]))

    for rule in constraint_set.rules:
        if rule.target not in {"band_min_width", "band_max_width"}:
            continue
        band_kind = str(_rule_parameter(rule, "band_kind", "")).strip()
        band_name = str(_rule_parameter(rule, "band_name", "")).strip()
        matched = False
        for band in program.bands:
            if band_name and band.name != band_name:
                continue
            if band_kind and band.kind != band_kind:
                continue
            if not band_name and not band_kind:
                continue
            matched = True
            entry = bounds[band.name]
            if rule.target == "band_min_width":
                entry["min_width_m"] = max(float(entry["min_width_m"]), float(rule.value))
            else:
                entry["max_width_m"] = min(float(entry["max_width_m"]), float(rule.value))
            if str(rule.name):
                entry["active_constraint_names"].append(str(rule.name))
            entry["max_width_m"] = max(float(entry["max_width_m"]), float(entry["min_width_m"]))
        if matched:
            continue
    return bounds


def _row_width_budget(program: StreetProgram, constraint_set: ConstraintSet) -> Tuple[float, Tuple[str, ...]]:
    budget = float(program.row_width_m) if float(program.row_width_m) > 0.0 else float(sum(float(band.width_m) for band in program.bands))
    active: List[str] = []
    for rule in constraint_set.rules:
        if rule.target != "total_row_width_budget":
            continue
        source = str(_rule_parameter(rule, "source", "")).strip().lower()
        if source == "program_row_width":
            value = float(program.row_width_m) if float(program.row_width_m) > 0.0 else budget
        else:
            try:
                value = float(rule.value)
            except (TypeError, ValueError):
                value = budget
        if str(rule.operator) == "<=":
            budget = min(float(budget), float(value))
        elif str(rule.operator) == ">=":
            budget = max(float(budget), float(value))
        else:
            budget = float(value)
        active.append(str(rule.name))
    return float(budget), tuple(dict.fromkeys(active))


def _throughput_requirements_from_rules(program: StreetProgram, constraint_set: ConstraintSet) -> Dict[str, float]:
    required = {
        key: float(value)
        for key, value in dict(program.throughput_requirements).items()
        if float(value) > 0.0
    }
    for rule in constraint_set.rules:
        if rule.target != "mode_throughput_min":
            continue
        mode = str(_rule_parameter(rule, "mode", "")).strip()
        if not mode:
            continue
        base = float(required.get(mode, 0.0))
        multiplier = float(rule.value) if isinstance(rule.value, (int, float)) else 1.0
        if base > 0.0:
            required[mode] = max(base, base * multiplier)
        elif multiplier > 0.0:
            required[mode] = multiplier
    return required


def _throughput_actuals(program: StreetProgram) -> Dict[str, float]:
    actuals: Dict[str, float] = {}
    clear_widths = [float(band.width_m) for band in program.bands if band.kind == "clear_path"]
    if clear_widths:
        actuals["ped_clear_path"] = float(min(clear_widths))
    carriageway = next((band for band in program.bands if band.kind == "carriageway"), None)
    if carriageway is not None:
        actuals["vehicle_carriageway"] = float(carriageway.width_m)
    transit_edges = [float(band.width_m) for band in program.bands if band.kind == "transit_edge"]
    if transit_edges:
        actuals["transit_edge"] = float(max(transit_edges))
    return actuals


def _throughput_feasibility(program: StreetProgram, constraint_set: ConstraintSet) -> Dict[str, Any]:
    required = _throughput_requirements_from_rules(program, constraint_set)
    actual = _throughput_actuals(program)
    by_mode: Dict[str, Dict[str, Any]] = {}
    overall = True
    for mode, required_value in required.items():
        actual_value = float(actual.get(mode, 0.0))
        satisfied = actual_value >= float(required_value) - 1e-6
        overall = overall and satisfied
        by_mode[str(mode)] = {
            "required": float(required_value),
            "actual": float(actual_value),
            "satisfied": bool(satisfied),
        }
    return {
        "overall_satisfied": bool(overall),
        "by_mode": by_mode,
    }


def _objective_band_weights(program: StreetProgram, objective_profile: str) -> Dict[str, float]:
    profile = str(objective_profile).strip().lower() or "balanced"
    kind_weights: Dict[str, float]
    if profile == "greening":
        kind_weights = {"furnishing": 1.45, "clear_path": 1.05, "transit_edge": 1.0, "carriageway": 0.95}
    elif profile == "commerce":
        kind_weights = {"furnishing": 1.1, "clear_path": 1.45, "transit_edge": 1.15, "carriageway": 0.9}
    elif profile == "transit":
        kind_weights = {"furnishing": 0.95, "clear_path": 1.15, "transit_edge": 1.65, "carriageway": 1.2}
    else:
        kind_weights = {"furnishing": 1.0, "clear_path": 1.05, "transit_edge": 1.1, "carriageway": 1.0}
    weights: Dict[str, float] = {}
    for band in program.bands:
        weight = float(kind_weights.get(band.kind, 1.0))
        if profile == "greening" and band.kind == "furnishing":
            weight += 0.1
        if profile == "commerce" and band.kind == "clear_path":
            weight += 0.05
        if profile == "transit" and band.name == "right_transit_edge":
            weight += 0.15
        weights[band.name] = float(weight)
    return weights


def _solve_widths_greedily(
    *,
    program: StreetProgram,
    band_bounds: Mapping[str, Mapping[str, Any]],
    budget_m: float,
    objective_weights: Mapping[str, float],
) -> Dict[str, float]:
    widths = {band.name: float(band_bounds[band.name]["min_width_m"]) for band in program.bands}
    remaining = float(budget_m) - sum(widths.values())
    if remaining <= 1e-6:
        return widths
    expandable = sorted(
        program.bands,
        key=lambda band: float(objective_weights.get(band.name, 1.0)),
        reverse=True,
    )
    for band in expandable:
        if remaining <= 1e-6:
            break
        max_width = float(band_bounds[band.name]["max_width_m"])
        current = float(widths[band.name])
        delta = min(float(remaining), max(0.0, max_width - current))
        widths[band.name] = current + delta
        remaining -= delta
    return widths


def _solve_band_geometry(
    *,
    solver_input: LayoutSolverInput,
    program: StreetProgram,
    conflicts: List[LayoutConflict],
) -> Tuple[StreetProgram, Tuple[BandSolution, ...], Tuple[str, ...], Dict[str, Any], Dict[str, float]]:
    band_bounds = _band_bound_map(program, solver_input.constraint_set)
    throughput_required = _throughput_requirements_from_rules(program, solver_input.constraint_set)
    for band in program.bands:
        entry = band_bounds[band.name]
        if band.kind == "clear_path" and throughput_required.get("ped_clear_path", 0.0) > 0.0:
            entry["min_width_m"] = max(float(entry["min_width_m"]), float(throughput_required["ped_clear_path"]))
            entry["active_constraint_names"].append("pedestrian_throughput_floor")
        if band.kind == "carriageway" and throughput_required.get("vehicle_carriageway", 0.0) > 0.0:
            entry["min_width_m"] = max(float(entry["min_width_m"]), float(throughput_required["vehicle_carriageway"]))
            entry["active_constraint_names"].append("vehicle_throughput_floor")
        if band.kind == "transit_edge" and throughput_required.get("transit_edge", 0.0) > 0.0:
            entry["min_width_m"] = max(float(entry["min_width_m"]), float(throughput_required["transit_edge"]))
            entry["active_constraint_names"].append("transit_throughput_floor")
        entry["max_width_m"] = max(float(entry["max_width_m"]), float(entry["min_width_m"]))

    budget_m, budget_rules = _row_width_budget(program, solver_input.constraint_set)
    objective_weights = _objective_band_weights(program, program.objective_profile)
    min_sum = sum(float(entry["min_width_m"]) for entry in band_bounds.values())
    if min_sum - float(budget_m) > 1e-6:
        conflicts.append(
            LayoutConflict(
                rule_name="total_row_width_budget",
                severity="hard",
                message=(
                    f"Minimum feasible band widths ({min_sum:.2f}m) exceed row budget ({float(budget_m):.2f}m)"
                ),
                affected_target="row_width",
            )
        )
        scale = float(budget_m) / float(min_sum) if min_sum > 0.0 else 1.0
        for entry in band_bounds.values():
            entry["min_width_m"] = max(0.2, float(entry["min_width_m"]) * scale)

    widths: Dict[str, float]
    if pulp is not None:
        problem = pulp.LpProblem("RoadGen3DHybridBandWidths", pulp.LpMaximize)
        width_vars = {
            band.name: pulp.LpVariable(
                f"w_{band.name}",
                lowBound=float(band_bounds[band.name]["min_width_m"]),
                upBound=float(band_bounds[band.name]["max_width_m"]),
            )
            for band in program.bands
        }
        problem += pulp.lpSum(float(objective_weights.get(band.name, 1.0)) * width_vars[band.name] for band in program.bands)
        problem += pulp.lpSum(width_vars[band.name] for band in program.bands) <= float(budget_m)
        if "left_clear_path" in width_vars and "right_clear_path" in width_vars:
            problem += width_vars["left_clear_path"] == width_vars["right_clear_path"]
        if "left_furnishing" in width_vars and "right_furnishing" in width_vars:
            problem += width_vars["left_furnishing"] == width_vars["right_furnishing"]
        status = problem.solve(pulp.PULP_CBC_CMD(msg=False))
        if pulp.LpStatus.get(status, "") in {"Optimal", "Integer Feasible", "Feasible"}:
            widths = {
                band.name: float(getattr(width_vars[band.name], "value", lambda: float(band.width_m))())
                for band in program.bands
            }
        else:
            conflicts.append(
                LayoutConflict(
                    rule_name="hybrid_band_lp",
                    severity="hard",
                    message=f"Hybrid band solve status: {pulp.LpStatus.get(status, 'Unknown')}",
                    affected_target="band_widths",
                )
            )
            widths = _solve_widths_greedily(
                program=program,
                band_bounds=band_bounds,
                budget_m=float(budget_m),
                objective_weights=objective_weights,
            )
    else:
        widths = _solve_widths_greedily(
            program=program,
            band_bounds=band_bounds,
            budget_m=float(budget_m),
            objective_weights=objective_weights,
        )

    rebuilt_bands = []
    for band in program.bands:
        rebuilt_bands.append(replace(band, width_m=float(widths.get(band.name, band.width_m))))
    resolved_program = _rebuild_program(
        program,
        lane_count=int(program.lane_count),
        bands=tuple(rebuilt_bands),
        requirements=dict(program.furniture_requirements),
    )
    throughput_feasibility = _throughput_feasibility(resolved_program, solver_input.constraint_set)
    band_solutions = tuple(
        BandSolution(
            band_name=band.name,
            band_kind=band.kind,
            side=band.side,
            width_m=float(band.width_m),
            min_width_m=float(band_bounds[band.name]["min_width_m"]),
            max_width_m=float(band_bounds[band.name]["max_width_m"]),
            slack_m=float(max(0.0, float(band_bounds[band.name]["max_width_m"]) - float(band.width_m))),
            objective_weight=float(objective_weights.get(band.name, 1.0)),
            active_constraint_names=tuple(dict.fromkeys(str(name) for name in band_bounds[band.name]["active_constraint_names"] if str(name))),
        )
        for band in resolved_program.bands
    )
    active_constraints = list(budget_rules)
    for solution in band_solutions:
        active_constraints.extend(solution.active_constraint_names)
        if abs(float(solution.width_m) - float(solution.min_width_m)) <= 1e-4:
            active_constraints.append(f"{solution.band_name}:min")
        if abs(float(solution.width_m) - float(solution.max_width_m)) <= 1e-4:
            active_constraints.append(f"{solution.band_name}:max")
    if not bool(throughput_feasibility.get("overall_satisfied", True)):
        active_constraints.extend(
            f"throughput:{mode}"
            for mode, data in dict(throughput_feasibility.get("by_mode", {})).items()
            if not bool(data.get("satisfied", True))
        )
    objective_score_breakdown = {
        "total_width_score": float(sum(float(solution.width_m) * float(solution.objective_weight) for solution in band_solutions)),
        "unused_row_budget_m": float(max(0.0, float(budget_m) - sum(float(solution.width_m) for solution in band_solutions))),
        "slot_mix_bias": float(
            sum(
                float(program.furniture_requirements.get(category, 0))
                for category in ("tree", "bench", "bus_stop", "bollard")
            )
        ),
    }
    return (
        resolved_program,
        band_solutions,
        tuple(dict.fromkeys(active_constraints)),
        throughput_feasibility,
        objective_score_breakdown,
    )


def _default_band_solutions(program: StreetProgram, constraint_set: ConstraintSet) -> Tuple[BandSolution, ...]:
    band_bounds = _band_bound_map(program, constraint_set)
    objective_weights = _objective_band_weights(program, program.objective_profile)
    return tuple(
        BandSolution(
            band_name=band.name,
            band_kind=band.kind,
            side=band.side,
            width_m=float(band.width_m),
            min_width_m=float(band_bounds[band.name]["min_width_m"]),
            max_width_m=float(band_bounds[band.name]["max_width_m"]),
            slack_m=float(max(0.0, float(band_bounds[band.name]["max_width_m"]) - float(band.width_m))),
            objective_weight=float(objective_weights.get(band.name, 1.0)),
            active_constraint_names=tuple(dict.fromkeys(str(name) for name in band_bounds[band.name]["active_constraint_names"] if str(name))),
        )
        for band in program.bands
    )


def _keepout_rules(constraint_set: ConstraintSet, placement_context: object | None) -> Tuple[Dict[str, object], ...]:
    if placement_context is None:
        return ()
    points_by_type = extract_poi_points_by_type(placement_context)
    rules: List[Dict[str, object]] = []
    for rule in constraint_set.rules:
        if rule.target != "keepout_radius":
            continue
        poi_type = str(_rule_parameter(rule, "poi_type", "")).strip()
        if not poi_type:
            continue
        points = tuple((float(point[0]), float(point[1])) for point in points_by_type.get(poi_type, ()))
        if not points:
            continue
        rules.append(
            {
                "rule_name": str(rule.name),
                "category": str(_rule_parameter(rule, "category", "all")).strip().lower() or "all",
                "poi_type": poi_type,
                "radius_m": float(rule.value),
                "points": points,
            }
        )
    return tuple(rules)


def _slot_violates_keepout(slot: LayoutSlotPlan, keepout_rules: Sequence[Dict[str, object]]) -> bool:
    for rule in keepout_rules:
        category = str(rule.get("category", "all")).strip().lower()
        if category not in {"all", str(slot.category).strip().lower()}:
            continue
        for point in rule.get("points", ()):
            dist = ((float(slot.x_center_m) - float(point[0])) ** 2 + (float(slot.z_center_m) - float(point[1])) ** 2) ** 0.5
            if dist < float(rule.get("radius_m", 0.0)):
                return True
    return False


def _band_order(program: StreetProgram) -> Tuple[str, ...]:
    ordered = sorted(
        program.bands,
        key=lambda band: (
            0 if band.side == "left" else 1 if band.side == "center" else 2,
            -float(band.z_center_m) if band.side == "left" else float(band.z_center_m) if band.side == "right" else 0.0,
            str(band.name),
        ),
    )
    return tuple(band.name for band in ordered)


def _bands_are_adjacent(program: StreetProgram, left_name: str, right_name: str) -> bool:
    ordered = _band_order(program)
    if left_name not in ordered or right_name not in ordered:
        return False
    return abs(ordered.index(left_name) - ordered.index(right_name)) == 1


def _bands_are_separated(program: StreetProgram, left_name: str, right_name: str, separator_name: str) -> bool:
    ordered = _band_order(program)
    if left_name not in ordered or right_name not in ordered or separator_name not in ordered:
        return False
    left_idx = ordered.index(left_name)
    right_idx = ordered.index(right_name)
    sep_idx = ordered.index(separator_name)
    lo = min(left_idx, right_idx)
    hi = max(left_idx, right_idx)
    return lo < sep_idx < hi


def _apply_objective_profile_preferences(
    requirements: Dict[str, int],
    *,
    objective_profile: str,
    available_categories: Set[str],
    edits: List[LayoutEdit],
) -> None:
    profile = str(objective_profile).strip().lower()
    if profile == "balanced":
        return
    scales_by_profile: Dict[str, Dict[str, float]] = {
        "greening": {"tree": 2.0, "bench": 1.1, "lamp": 0.8, "bollard": 0.45},
        "commerce": {"bench": 1.5, "trash": 1.3, "lamp": 1.2, "tree": 0.85, "bollard": 0.5},
        "transit": {"bus_stop": 1.6, "lamp": 1.25, "bollard": 1.35, "bench": 0.6},
    }
    scale_map = scales_by_profile.get(profile, {})
    for category, scale in scale_map.items():
        if category not in available_categories or category not in requirements:
            continue
        before = int(requirements.get(category, 0))
        after = max(0, int(round(float(before) * float(scale))))
        if category == "bus_stop" and before > 0:
            after = max(1, after)
        if before == after:
            continue
        requirements[category] = after
        edits.append(
            LayoutEdit(
                action="objective_bias",
                target=category,
                before=str(before),
                after=str(after),
                reason=f"objective_profile={profile}: adjusted preferred slot count",
            )
        )


def _default_band_order(category: str, bands: Sequence[StreetBand]) -> List[StreetBand]:
    placeable = [
        band
        for band in bands
        if set(coerce_band_rule_kinds(band.name, band.kind)).intersection({"furnishing", "transit_edge"})
    ]
    if category in {"bus_stop", "mailbox", "hydrant"}:
        priority = [
            band for band in placeable if band.side == "right" and category in band.allowed_categories
        ]
        fallback = [
            band for band in placeable if band.side != "right" and category in band.allowed_categories
        ]
        return priority + fallback

    if SIDE_PREF.get(category, "both") == "both":
        left = [band for band in placeable if band.side == "left" and category in band.allowed_categories]
        right = [band for band in placeable if band.side == "right" and category in band.allowed_categories]
        merged: List[StreetBand] = []
        max_len = max(len(left), len(right))
        for idx in range(max_len):
            if idx < len(left):
                merged.append(left[idx])
            if idx < len(right):
                merged.append(right[idx])
        return merged

    return [band for band in placeable if category in band.allowed_categories]


def _apply_category_availability_rules(
    requirements: Dict[str, int],
    available_categories: Set[str],
    rules: Sequence[DesignRuleSpec],
    edits: List[LayoutEdit],
    conflicts: List[LayoutConflict],
    rule_effects: Dict[str, Dict[str, List[str]]],
) -> None:
    for rule in rules:
        if rule.target != "required_category_available":
            continue
        category = str(_rule_parameter(rule, "category", "")).strip()
        if not category:
            continue
        if category in available_categories:
            continue
        substitutes = tuple(_rule_parameter(rule, "substitute_categories", CATEGORY_SUBSTITUTIONS.get(category, ())))
        replacement = next((candidate for candidate in substitutes if candidate in available_categories), "")
        if replacement:
            count = max(1, int(requirements.get(category, 0) or 1))
            requirements[replacement] = max(int(requirements.get(replacement, 0)), count)
            requirements[category] = 0
            edits.append(
                LayoutEdit(
                    action="replace",
                    target=category,
                    before=category,
                    after=replacement,
                    reason=f"{rule.name}: inventory missing {category}, substituted with {replacement}",
                )
            )
            rule_effects.setdefault(rule.name, {"edits": [], "conflicts": []})["edits"].append(replacement)
            continue
        message = f"{rule.name}: inventory missing required category '{category}'"
        conflicts.append(
            LayoutConflict(
                rule_name=rule.name,
                severity=str(rule.mode),
                message=message,
                affected_target=category,
            )
        )
        rule_effects.setdefault(rule.name, {"edits": [], "conflicts": []})["conflicts"].append(category)


def _compile_program(
    solver_input: LayoutSolverInput,
) -> Tuple[StreetProgram, Dict[str, Dict[str, List[str]]], List[LayoutEdit], List[LayoutConflict]]:
    lane_count = int(solver_input.program.lane_count)
    bands = list(solver_input.program.bands)
    requirements = dict(solver_input.program.furniture_requirements)
    edits: List[LayoutEdit] = []
    conflicts: List[LayoutConflict] = []
    rule_effects: Dict[str, Dict[str, List[str]]] = {}
    available = set(solver_input.available_categories)

    for rule in solver_input.constraint_set.rules:
        if rule.target == "lane_count":
            desired, changed = _apply_numeric_rule(float(lane_count), str(rule.operator), float(rule.value))
            if changed:
                edits.append(
                    LayoutEdit(
                        action="program_update",
                        target="lane_count",
                        before=str(lane_count),
                        after=str(int(desired)),
                        reason=f"{rule.name}: adjusted lane count to satisfy {rule.operator} {rule.value}",
                    )
                )
                rule_effects.setdefault(rule.name, {"edits": [], "conflicts": []})["edits"].append("lane_count")
                lane_count = int(desired)
        elif rule.target in {"band_min_width", "band_max_width"}:
            band_kind = str(_rule_parameter(rule, "band_kind", "")).strip()
            band_name = str(_rule_parameter(rule, "band_name", "")).strip()
            for idx, band in enumerate(list(bands)):
                if band_name and band.name != band_name:
                    continue
                if band_kind and band.kind != band_kind:
                    continue
                if not band_name and not band_kind:
                    continue
                desired, changed = _apply_numeric_rule(float(band.width_m), str(rule.operator), float(rule.value))
                if changed:
                    bands[idx] = replace(band, width_m=float(desired))
                    edits.append(
                        LayoutEdit(
                            action="program_update",
                            target=f"band:{band.name}",
                            before=f"{band.width_m:.2f}",
                            after=f"{desired:.2f}",
                            reason=f"{rule.name}: adjusted {band.name} to satisfy {rule.operator} {rule.value}",
                        )
                    )
                    rule_effects.setdefault(rule.name, {"edits": [], "conflicts": []})["edits"].append(band.name)
        elif rule.target in {"category_min_count", "slot_count_min", "slot_count_max"}:
            category = str(_rule_parameter(rule, "category", "")).strip()
            if not category:
                continue
            if category not in available:
                message = f"{rule.name}: cannot require '{category}' because the asset inventory does not contain it"
                conflicts.append(
                    LayoutConflict(
                        rule_name=rule.name,
                        severity=str(rule.mode),
                        message=message,
                        affected_target=category,
                    )
                )
                rule_effects.setdefault(rule.name, {"edits": [], "conflicts": []})["conflicts"].append(category)
                continue
            current = int(requirements.get(category, 0))
            desired, changed = _apply_numeric_rule(float(current), str(rule.operator), float(rule.value))
            if changed:
                requirements[category] = int(desired)
                edits.append(
                    LayoutEdit(
                        action="program_update",
                        target=category,
                        before=str(current),
                        after=str(int(desired)),
                        reason=f"{rule.name}: adjusted {category} count to satisfy {rule.operator} {rule.value}",
                    )
                )
                rule_effects.setdefault(rule.name, {"edits": [], "conflicts": []})["edits"].append(category)

    _apply_category_availability_rules(
        requirements=requirements,
        available_categories=available,
        rules=solver_input.constraint_set.rules,
        edits=edits,
        conflicts=conflicts,
        rule_effects=rule_effects,
    )
    _apply_objective_profile_preferences(
        requirements=requirements,
        objective_profile=str(solver_input.program.objective_profile),
        available_categories=available,
        edits=edits,
    )
    resolved_program = _rebuild_program(
        solver_input.program,
        lane_count=lane_count,
        bands=bands,
        requirements=requirements,
    )
    return resolved_program, rule_effects, edits, conflicts


def _resolve_allowed_bands(
    *,
    category: str,
    default_bands: Sequence[StreetBand],
    constraint_set: ConstraintSet,
    program_reserved_band_categories: Dict[str, str],
    rule_effects: Dict[str, Dict[str, List[str]]],
    edits: List[LayoutEdit],
    conflicts: List[LayoutConflict],
) -> List[StreetBand]:
    band_rules = _allowed_band_map(constraint_set)
    reserved_bands = _reserved_band_map(constraint_set)
    all_allowed = band_rules.get("all")
    specific_allowed = band_rules.get(category)

    def _band_allowed(band: StreetBand) -> bool:
        band_rule_kinds = set(coerce_band_rule_kinds(band.name, band.kind))
        if specific_allowed is not None and not band_rule_kinds.intersection(set(specific_allowed)):
            return False
        if all_allowed is not None and not band_rule_kinds.intersection(set(all_allowed)):
            return False
        reserved_band_name = program_reserved_band_categories.get(band.name)
        if reserved_band_name and reserved_band_name != category:
            return False
        for band_rule_kind in band_rule_kinds:
            reserved_category = reserved_bands.get(band_rule_kind)
            if reserved_category and reserved_category != category:
                return False
        return True

    filtered = [band for band in default_bands if _band_allowed(band)]
    if filtered:
        if filtered[0].name != default_bands[0].name:
            reason = f"band rule moved {category} from {default_bands[0].name} to {filtered[0].name}"
            edits.append(
                LayoutEdit(
                    action="move",
                    target=category,
                    before=default_bands[0].name,
                    after=filtered[0].name,
                    reason=reason,
                )
            )
            for rule in constraint_set.rules:
                if rule.target == "category_allowed_band":
                    target_category = str(_rule_parameter(rule, "category", "all"))
                    if target_category in {category, "all"}:
                        rule_effects.setdefault(rule.name, {"edits": [], "conflicts": []})["edits"].append(category)
                        break
        return filtered

    message = f"No valid bands remain for category '{category}' after applying design rules"
    conflicts.append(
        LayoutConflict(
            rule_name="category_allowed_band",
            severity="hard",
            message=message,
            affected_target=category,
        )
    )
    edits.append(
        LayoutEdit(
            action="remove",
            target=category,
            before="planned_slots",
            after="none",
            reason=message,
        )
    )
    rule_effects.setdefault("category_allowed_band", {"edits": [], "conflicts": []})["conflicts"].append(category)
    return []


def _balanced_band_sequence(
    *,
    category: str,
    allowed_bands: Sequence[StreetBand],
    remaining_count: int,
    bilateral_side_counts: Mapping[str, int],
) -> List[StreetBand]:
    if (
        str(SIDE_PREF.get(category, "both")) != "both"
        or remaining_count <= 0
    ):
        return [allowed_bands[idx % len(allowed_bands)] for idx in range(max(remaining_count, 0))]
    left_bands = [band for band in allowed_bands if band.side == "left"]
    right_bands = [band for band in allowed_bands if band.side == "right"]
    if not left_bands or not right_bands:
        return [allowed_bands[idx % len(allowed_bands)] for idx in range(max(remaining_count, 0))]
    side_counts = {
        "left": int(bilateral_side_counts.get("left", 0) or 0),
        "right": int(bilateral_side_counts.get("right", 0) or 0),
    }
    side_indices = {"left": 0, "right": 0}
    ordered: List[StreetBand] = []
    for _idx in range(int(remaining_count)):
        preferred_side = "left" if side_counts["left"] <= side_counts["right"] else "right"
        side_pool = left_bands if preferred_side == "left" else right_bands
        side_index = side_indices[preferred_side] % len(side_pool)
        band = side_pool[side_index]
        ordered.append(band)
        side_indices[preferred_side] += 1
        side_counts[preferred_side] += 1
    return ordered


def _repair_bilateral_slot_plans(
    slot_plans: Sequence[LayoutSlotPlan],
    *,
    allowed_bands_by_category: Mapping[str, Sequence[StreetBand]],
) -> Tuple[LayoutSlotPlan, ...]:
    bilateral_slots = [
        slot
        for slot in slot_plans
        if str(slot.category) in _BALANCED_FURNITURE_CATEGORIES and str(slot.side) in {"left", "right"}
    ]
    side_counts = Counter(str(slot.side) for slot in bilateral_slots)
    if side_counts.get("left", 0) > 0 and side_counts.get("right", 0) > 0:
        return tuple(slot_plans)
    compatible_sides = {
        str(band.side)
        for category, bands in allowed_bands_by_category.items()
        if str(category) in _BALANCED_FURNITURE_CATEGORIES
        for band in bands
        if str(band.side) in {"left", "right"}
    }
    if not {"left", "right"} <= compatible_sides:
        return tuple(slot_plans)
    missing_side = "left" if side_counts.get("left", 0) == 0 else "right"
    source_side = "right" if missing_side == "left" else "left"
    candidates = sorted(
        (
            (idx, slot)
            for idx, slot in enumerate(slot_plans)
            if str(slot.category) in _BALANCED_FURNITURE_CATEGORIES
            and str(slot.side) == source_side
            and not bool(slot.anchor_poi_type)
        ),
        key=lambda item: (bool(item[1].required), float(item[1].priority), float(item[1].x_center_m)),
    )
    repaired = list(slot_plans)
    for idx, slot in candidates:
        replacement_band = next(
            (
                band
                for band in allowed_bands_by_category.get(str(slot.category), ())
                if str(band.side) == missing_side
            ),
            None,
        )
        if replacement_band is None:
            continue
        repaired[idx] = replace(
            slot,
            band_name=str(replacement_band.name),
            z_center_m=float(replacement_band.z_center_m),
            side=str(replacement_band.side),
        )
        break
    return tuple(repaired)


def _replacement_band_for_side(
    *,
    category: str,
    side: str,
    allowed_bands_by_category: Mapping[str, Sequence[StreetBand]],
) -> StreetBand | None:
    preferred = [
        band
        for band in allowed_bands_by_category.get(str(category), ())
        if str(band.side) == str(side)
    ]
    if preferred:
        return preferred[0]
    fallback = list(allowed_bands_by_category.get(str(category), ()))
    return fallback[0] if fallback else None


def _retarget_slot_category(
    slot: LayoutSlotPlan,
    *,
    new_category: str,
    allowed_bands_by_category: Mapping[str, Sequence[StreetBand]],
    target_side: str | None = None,
) -> LayoutSlotPlan | None:
    replacement_band = _replacement_band_for_side(
        category=str(new_category),
        side=str(target_side or slot.side),
        allowed_bands_by_category=allowed_bands_by_category,
    )
    if replacement_band is None:
        return None
    return replace(
        slot,
        category=str(new_category),
        band_name=str(replacement_band.name),
        z_center_m=float(replacement_band.z_center_m),
        side=str(replacement_band.side),
    )


def _enrich_balanced_slot_plans(
    slot_plans: Sequence[LayoutSlotPlan],
    *,
    allowed_bands_by_category: Mapping[str, Sequence[StreetBand]],
    available_categories: Sequence[str],
    edits: List[LayoutEdit],
) -> Tuple[LayoutSlotPlan, ...]:
    balanced_available = [
        category
        for category in sorted(_BALANCED_FURNITURE_CATEGORIES)
        if str(category) in set(str(item) for item in available_categories)
        and str(category) in allowed_bands_by_category
    ]
    if not balanced_available:
        return tuple(slot_plans)

    updated = list(slot_plans)

    def _core_slots() -> List[Tuple[int, LayoutSlotPlan]]:
        return [
            (idx, slot)
            for idx, slot in enumerate(updated)
            if str(slot.category) in balanced_available
            and str(slot.side) in {"left", "right"}
        ]

    def _replace_candidate(
        *,
        source_side: str | None,
        missing_categories: Sequence[str],
        min_unique_target: int,
    ) -> None:
        if not missing_categories:
            return
        current_core = _core_slots()
        side_counts = Counter(str(slot.side) for _idx, slot in current_core)
        if source_side is not None and side_counts.get(str(source_side), 0) < int(min_unique_target):
            return
        category_counts = Counter(str(slot.category) for _idx, slot in current_core)
        side_category_counts = Counter(
            (str(slot.side), str(slot.category))
            for _idx, slot in current_core
        )
        candidates = [
            (idx, slot)
            for idx, slot in current_core
            if not bool(slot.anchor_poi_type)
            and (source_side is None or str(slot.side) == str(source_side))
            and category_counts.get(str(slot.category), 0) > 1
            and (source_side is None or side_category_counts.get((str(slot.side), str(slot.category)), 0) > 1)
        ]
        candidates.sort(
            key=lambda item: (
                bool(item[1].required),
                float(item[1].priority),
                float(item[1].x_center_m),
                str(item[1].slot_id),
            )
        )
        for missing_category in missing_categories:
            for idx, slot in candidates:
                replacement = _retarget_slot_category(
                    slot,
                    new_category=str(missing_category),
                    allowed_bands_by_category=allowed_bands_by_category,
                    target_side=str(source_side or slot.side),
                )
                if replacement is None:
                    continue
                updated[idx] = replacement
                edits.append(
                    LayoutEdit(
                        action="rebalance_core_slot_mix",
                        target=str(slot.slot_id),
                        before=str(slot.category),
                        after=str(missing_category),
                        reason=(
                            f"enforced richer bilateral core furniture mix"
                            if source_side is None
                            else f"enforced richer {source_side} core furniture mix"
                        ),
                    )
                )
                return

    current_core = _core_slots()
    global_target = min(3, len(balanced_available), len(current_core))
    unique_global = {str(slot.category) for _idx, slot in current_core}
    while len(unique_global) < global_target:
        missing = [category for category in balanced_available if category not in unique_global]
        if not missing:
            break
        before = len(unique_global)
        _replace_candidate(source_side=None, missing_categories=missing, min_unique_target=0)
        unique_global = {str(slot.category) for _idx, slot in _core_slots()}
        if len(unique_global) == before:
            break

    for side in ("left", "right"):
        current_core = _core_slots()
        side_slots = [slot for _idx, slot in current_core if str(slot.side) == side]
        side_available = [
            category
            for category in balanced_available
            if any(str(band.side) == side for band in allowed_bands_by_category.get(str(category), ()))
        ]
        side_target = min(2, len(side_available), len(side_slots))
        side_unique = {str(slot.category) for slot in side_slots}
        while len(side_unique) < side_target:
            missing = [category for category in side_available if category not in side_unique]
            if not missing:
                break
            before = len(side_unique)
            _replace_candidate(source_side=side, missing_categories=missing, min_unique_target=side_target)
            side_slots = [slot for _idx, slot in _core_slots() if str(slot.side) == side]
            side_unique = {str(slot.category) for slot in side_slots}
            if len(side_unique) == before:
                break
    return tuple(updated)


def _build_slot_plans(
    solver_input: LayoutSolverInput,
    resolved_program: StreetProgram,
    rule_effects: Dict[str, Dict[str, List[str]]],
    edits: List[LayoutEdit],
    conflicts: List[LayoutConflict],
) -> Tuple[LayoutSlotPlan, ...]:
    slot_plans: List[LayoutSlotPlan] = []
    required_categories = _required_categories(solver_input.constraint_set)
    placement_context = solver_input.placement_context
    bilateral_side_counts: Dict[str, int] = {"left": 0, "right": 0}
    allowed_bands_by_category: Dict[str, Tuple[StreetBand, ...]] = {}

    def _poi_anchor_data(category: str) -> List[Tuple[str, List[Tuple[float, float]]]]:
        if placement_context is None:
            return []
        clustered_points = cluster_asset_backed_poi_points(extract_poi_points_by_type(placement_context))
        results: List[Tuple[str, List[Tuple[float, float]]]] = []
        for poi_type in asset_backed_poi_types_for_category(category):
            results.append((
                poi_type,
                [
                    (float(point[0]), float(point[1]))
                    for point in clustered_points.get(poi_type, ())
                ],
            ))
        return results

    for category, count in resolved_program.furniture_requirements.items():
        count = int(count)
        if count <= 0 or category not in solver_input.available_categories:
            continue
        default_bands = _default_band_order(category, resolved_program.bands)
        if not default_bands:
            conflicts.append(
                LayoutConflict(
                    rule_name="band_assignment",
                    severity="hard",
                    message=f"No placeable band is available for category '{category}'",
                    affected_target=category,
                )
            )
            continue
        allowed_bands = _resolve_allowed_bands(
            category=category,
            default_bands=default_bands,
            constraint_set=solver_input.constraint_set,
            program_reserved_band_categories=dict(resolved_program.reserved_band_categories),
            rule_effects=rule_effects,
            edits=edits,
            conflicts=conflicts,
        )
        if not allowed_bands:
            continue
        allowed_bands_by_category[str(category)] = tuple(allowed_bands)
        anchor_entries = _poi_anchor_data(category)
        anchor_slot_count = 0
        for poi_type, anchor_points in anchor_entries:
            for idx, point in enumerate(anchor_points):
                band = min(
                    allowed_bands,
                    key=lambda item: abs(float(item.z_center_m) - float(point[1])),
                )
                anchor_x = float(point[0])
                anchor_z = float(band.z_center_m)
                slot_plans.append(
                    LayoutSlotPlan(
                        slot_id=f"{category}_{poi_type}_poi_{idx:03d}",
                        category=category,
                        band_name=band.name,
                        x_center_m=anchor_x,
                        z_center_m=anchor_z,
                        spacing_m=float(max(solver_input.config.segment_length_m, 1.0)),
                        side=str(band.side),
                        priority=2.0,
                        required=True,
                        anchor_poi_type=poi_type,
                        anchor_position_xz=(anchor_x, anchor_z),
                    )
                )
                anchor_slot_count += 1
                if str(category) in _BALANCED_FURNITURE_CATEGORIES and str(band.side) in {"left", "right"}:
                    bilateral_side_counts[str(band.side)] = bilateral_side_counts.get(str(band.side), 0) + 1

        remaining_count = max(0, count - anchor_slot_count)
        if remaining_count <= 0:
            continue
        segment = float(solver_input.config.length_m) / float(max(remaining_count, 1))
        ordered_bands = _balanced_band_sequence(
            category=str(category),
            allowed_bands=allowed_bands,
            remaining_count=remaining_count,
            bilateral_side_counts=bilateral_side_counts,
        )
        for idx, band in enumerate(ordered_bands):
            slot_plans.append(
                LayoutSlotPlan(
                    slot_id=f"{category}_{idx:03d}",
                    category=category,
                    band_name=band.name,
                    x_center_m=-float(solver_input.config.length_m) / 2.0 + (idx + 0.5) * segment,
                    z_center_m=float(band.z_center_m),
                    spacing_m=float(segment),
                    side=str(band.side),
                    priority=1.0 if category in required_categories else 0.5,
                    required=category in required_categories,
                )
            )
            if str(category) in _BALANCED_FURNITURE_CATEGORIES and str(band.side) in {"left", "right"}:
                bilateral_side_counts[str(band.side)] = bilateral_side_counts.get(str(band.side), 0) + 1

    repaired_slot_plans = list(
        _repair_bilateral_slot_plans(
            slot_plans,
            allowed_bands_by_category=allowed_bands_by_category,
        )
    )
    repaired_slot_plans = list(
        _enrich_balanced_slot_plans(
            repaired_slot_plans,
            allowed_bands_by_category=allowed_bands_by_category,
            available_categories=solver_input.available_categories,
            edits=edits,
        )
    )
    repaired_slot_plans.sort(key=lambda slot: (slot.category, slot.x_center_m, slot.z_center_m))
    return tuple(repaired_slot_plans)


def _evaluate_rule(
    *,
    rule: DesignRuleSpec,
    resolved_program: StreetProgram,
    solver_input: LayoutSolverInput,
    band_solutions: Sequence[BandSolution],
    slot_plans: Sequence[LayoutSlotPlan],
    rule_effects: Dict[str, Dict[str, List[str]]],
    conflicts: Sequence[LayoutConflict],
    throughput_feasibility: Mapping[str, Any],
    keepout_rules: Sequence[Dict[str, object]],
) -> RuleEvaluation:
    hard_conflict = next((conflict for conflict in conflicts if conflict.rule_name == rule.name), None)
    if hard_conflict is not None:
        return RuleEvaluation(
            rule_name=rule.name,
            status="conflict",
            mode=str(rule.mode),
            score=0.0,
            explanation=str(hard_conflict.message),
            affected_categories=(str(hard_conflict.affected_target),) if hard_conflict.affected_target else (),
        )

    edits = tuple(rule_effects.get(rule.name, {}).get("edits", []))
    if rule.target == "lane_count":
        lane_count = float(resolved_program.lane_count)
        satisfied = (
            lane_count <= float(rule.value)
            if str(rule.operator) == "<="
            else lane_count >= float(rule.value)
            if str(rule.operator) == ">="
            else lane_count == float(rule.value)
        )
    elif rule.target == "band_min_width":
        band_kind = str(_rule_parameter(rule, "band_kind", ""))
        widths = [float(band.width_m) for band in resolved_program.bands if band.kind == band_kind]
        if not widths:
            satisfied = False
        else:
            satisfied = min(widths) >= float(rule.value)
    elif rule.target == "band_max_width":
        band_kind = str(_rule_parameter(rule, "band_kind", ""))
        widths = [float(band.width_m) for band in resolved_program.bands if band.kind == band_kind]
        if not widths:
            satisfied = False
        else:
            satisfied = max(widths) <= float(rule.value) + 1e-6
    elif rule.target in {"category_min_count", "slot_count_min"}:
        category = str(_rule_parameter(rule, "category", ""))
        actual = sum(1 for slot in slot_plans if slot.category == category)
        satisfied = actual >= int(rule.value)
    elif rule.target == "slot_count_max":
        category = str(_rule_parameter(rule, "category", ""))
        actual = sum(1 for slot in slot_plans if slot.category == category)
        satisfied = actual <= int(rule.value)
    elif rule.target == "required_category_available":
        category = str(_rule_parameter(rule, "category", ""))
        actual = sum(1 for slot in slot_plans if slot.category == category)
        if actual > 0:
            satisfied = True
        else:
            substitutes = tuple(_rule_parameter(rule, "substitute_categories", ()))
            satisfied = any(sum(1 for slot in slot_plans if slot.category == substitute) > 0 for substitute in substitutes)
    elif rule.target == "category_allowed_band":
        allowed = tuple(str(item) for item in (rule.value or ()))
        target_category = str(_rule_parameter(rule, "category", "all"))
        relevant = [slot for slot in slot_plans if target_category == "all" or slot.category == target_category]
        satisfied = all(
            bool(
                set(
                    coerce_band_rule_kinds(
                        str(getattr(resolve_band_by_alias(
                            resolved_program.bands,
                            band_name=slot.band_name,
                            side=getattr(slot, "side", ""),
                        ), "name", "") or slot.band_name),
                        str(getattr(resolve_band_by_alias(
                            resolved_program.bands,
                            band_name=slot.band_name,
                            side=getattr(slot, "side", ""),
                        ), "kind", "") or ""),
                    )
                ).intersection(set(allowed))
            )
            for slot in relevant
        )
    elif rule.target == "reserved_band_category":
        band_kind = str(_rule_parameter(rule, "band_kind", ""))
        relevant = [
            slot
            for slot in slot_plans
            if band_kind in coerce_band_rule_kinds(
                str(getattr(resolve_band_by_alias(
                    resolved_program.bands,
                    band_name=slot.band_name,
                    side=getattr(slot, "side", ""),
                ), "name", "") or slot.band_name),
                str(getattr(resolve_band_by_alias(
                    resolved_program.bands,
                    band_name=slot.band_name,
                    side=getattr(slot, "side", ""),
                ), "kind", "") or ""),
            )
        ]
        satisfied = all(slot.category == str(rule.value) for slot in relevant)
    elif rule.target == "total_row_width_budget":
        budget, _active = _row_width_budget(resolved_program, solver_input.constraint_set)
        satisfied = float(resolved_program.row_width_m) <= float(budget) + 1e-6
    elif rule.target == "mode_throughput_min":
        mode = str(_rule_parameter(rule, "mode", "")).strip()
        mode_payload = dict(throughput_feasibility.get("by_mode", {})).get(mode, {})
        satisfied = bool(mode_payload.get("satisfied", False))
    elif rule.target == "adjacency_required":
        band_name = str(_rule_parameter(rule, "band_name", "")).strip()
        adjacent_to = str(_rule_parameter(rule, "adjacent_to", "")).strip()
        satisfied = _bands_are_adjacent(resolved_program, band_name, adjacent_to)
    elif rule.target == "separation_required":
        left_name = str(_rule_parameter(rule, "left", "")).strip()
        right_name = str(_rule_parameter(rule, "right", "")).strip()
        separator_name = str(_rule_parameter(rule, "separator", "")).strip()
        satisfied = _bands_are_separated(resolved_program, left_name, right_name, separator_name)
    elif rule.target == "keepout_radius":
        rule_keepouts = [item for item in keepout_rules if str(item.get("rule_name", "")) == str(rule.name)]
        satisfied = not any(_slot_violates_keepout(slot, rule_keepouts) for slot in slot_plans)
    else:
        satisfied = True

    if edits:
        return RuleEvaluation(
            rule_name=rule.name,
            status="edited",
            mode=str(rule.mode),
            score=1.0 if satisfied else 0.5,
            explanation=f"{rule.name}: solver applied {len(edits)} explicit edit(s)",
            affected_categories=tuple(str(item) for item in edits),
        )
    return RuleEvaluation(
        rule_name=rule.name,
        status="satisfied" if satisfied else "violated",
        mode=str(rule.mode),
        score=1.0 if satisfied else 0.0,
        explanation=f"{rule.name}: {'satisfied' if satisfied else 'not satisfied'}",
        affected_categories=(),
    )


def _finalize_result(
    *,
    solver_input: LayoutSolverInput,
    resolved_program: StreetProgram,
    band_solutions: Sequence[BandSolution],
    slot_plans: Sequence[LayoutSlotPlan],
    rule_effects: Dict[str, Dict[str, List[str]]],
    edits: Sequence[LayoutEdit],
    conflicts: Sequence[LayoutConflict],
    active_constraints: Sequence[str] = (),
    throughput_feasibility: Mapping[str, Any] | None = None,
    objective_score_breakdown: Mapping[str, float] | None = None,
    backend_requested: str,
    backend_used: str,
    fallback_reason: str = "",
    road_segment_graph_summary: Dict[str, object] | None = None,
) -> LayoutSolverResult:
    keepout_rules = _keepout_rules(solver_input.constraint_set, solver_input.placement_context)
    throughput_payload = dict(throughput_feasibility or _throughput_feasibility(resolved_program, solver_input.constraint_set))
    evaluations = tuple(
        _evaluate_rule(
            rule=rule,
            resolved_program=resolved_program,
            solver_input=solver_input,
            band_solutions=tuple(band_solutions),
            slot_plans=tuple(slot_plans),
            rule_effects=rule_effects,
            conflicts=conflicts,
            throughput_feasibility=throughput_payload,
            keepout_rules=keepout_rules,
        )
        for rule in solver_input.constraint_set.rules
    )

    essential_checks = [
        int(resolved_program.lane_count) >= 1,
        any(band.kind == "carriageway" for band in resolved_program.bands),
        any(band.side == "left" and band.kind in {"furnishing", "transit_edge", "clear_path"} for band in resolved_program.bands),
        any(band.side == "right" and band.kind in {"furnishing", "transit_edge", "clear_path"} for band in resolved_program.bands),
    ]
    topology_validity = float(sum(1 for item in essential_checks if item) / len(essential_checks))

    band_widths_valid = all(float(band.width_m) > 0.0 for band in resolved_program.bands)
    cross_section_feasibility = 1.0 if band_widths_valid else 0.0
    if conflicts:
        cross_section_feasibility = max(0.0, cross_section_feasibility - 0.25 * len(conflicts))
    if not bool(throughput_payload.get("overall_satisfied", True)):
        cross_section_feasibility = max(0.0, cross_section_feasibility - 0.2)

    rule_satisfaction_rate = (
        sum(float(evaluation.score) for evaluation in evaluations) / len(evaluations)
        if evaluations
        else 1.0
    )
    editability = 1.0 if not edits else float(sum(1 for edit in edits if edit.reason.strip()) / len(edits))
    conflict_explainability = 1.0 if not conflicts else float(sum(1 for conflict in conflicts if conflict.message.strip()) / len(conflicts))

    return LayoutSolverResult(
        resolved_program=resolved_program,
        band_solutions=tuple(band_solutions),
        slot_plans=tuple(slot_plans),
        rule_evaluations=evaluations,
        edits=tuple(edits),
        conflicts=tuple(conflicts),
        topology_validity=float(topology_validity),
        cross_section_feasibility=float(max(0.0, min(cross_section_feasibility, 1.0))),
        rule_satisfaction_rate=float(max(0.0, min(rule_satisfaction_rate, 1.0))),
        editability=float(max(0.0, min(editability, 1.0))),
        conflict_explainability=float(max(0.0, min(conflict_explainability, 1.0))),
        active_constraints=tuple(dict.fromkeys(str(item) for item in active_constraints if str(item))),
        throughput_feasibility=throughput_payload,
        objective_profile=str(resolved_program.objective_profile),
        objective_score_breakdown=dict(objective_score_breakdown or {}),
        backend_requested=backend_requested,
        backend_used=backend_used,
        fallback_reason=fallback_reason,
        road_segment_graph_summary=dict(road_segment_graph_summary) if road_segment_graph_summary is not None else None,
    )


def solve_layout(solver_input: LayoutSolverInput) -> LayoutSolverResult:
    """Compile a StreetProgram plus rules into a constrained slot plan using the banded solver."""

    resolved_program, rule_effects, edits, conflicts = _compile_program(solver_input)
    band_solutions = _default_band_solutions(resolved_program, solver_input.constraint_set)
    throughput_feasibility = _throughput_feasibility(resolved_program, solver_input.constraint_set)
    slot_plans = _build_slot_plans(
        solver_input=solver_input,
        resolved_program=resolved_program,
        rule_effects=rule_effects,
        edits=edits,
        conflicts=conflicts,
    )
    return _finalize_result(
        solver_input=solver_input,
        resolved_program=resolved_program,
        band_solutions=band_solutions,
        slot_plans=slot_plans,
        rule_effects=rule_effects,
        edits=edits,
        conflicts=conflicts,
        active_constraints=tuple(name for solution in band_solutions for name in solution.active_constraint_names),
        throughput_feasibility=throughput_feasibility,
        objective_score_breakdown={
            "total_width_score": float(sum(float(solution.width_m) * float(solution.objective_weight) for solution in band_solutions)),
            "unused_row_budget_m": 0.0,
            "slot_mix_bias": float(sum(float(value) for value in resolved_program.furniture_requirements.values())),
        },
        backend_requested="banded",
        backend_used="banded",
    )


class LayoutSolverRuntime:
    """Dispatch between banded, milp_template_v1, and hybrid_milp_v1 layout-solving backends."""

    def __init__(self, backend: str = "banded") -> None:
        self.backend = str(backend).strip().lower() or "banded"

    def solve(self, solver_input: LayoutSolverInput) -> LayoutSolverResult:
        requested = str(self.backend)
        if requested == "banded":
            banded = solve_layout(solver_input)
            return LayoutSolverResult(
                resolved_program=banded.resolved_program,
                band_solutions=banded.band_solutions,
                slot_plans=banded.slot_plans,
                rule_evaluations=banded.rule_evaluations,
                edits=banded.edits,
                conflicts=banded.conflicts,
                topology_validity=banded.topology_validity,
                cross_section_feasibility=banded.cross_section_feasibility,
                rule_satisfaction_rate=banded.rule_satisfaction_rate,
                editability=banded.editability,
                conflict_explainability=banded.conflict_explainability,
                active_constraints=banded.active_constraints,
                throughput_feasibility=banded.throughput_feasibility,
                objective_profile=banded.objective_profile,
                objective_score_breakdown=banded.objective_score_breakdown,
                backend_requested=requested,
                backend_used="banded",
                fallback_reason="",
                road_segment_graph_summary=banded.road_segment_graph_summary,
            )

        if requested not in {"milp_template_v1", "hybrid_milp_v1"}:
            raise ValueError("layout solver backend must be 'banded', 'milp_template_v1', or 'hybrid_milp_v1'")

        resolved_program, rule_effects, edits, conflicts = _compile_program(solver_input)
        if requested == "hybrid_milp_v1":
            resolved_program, band_solutions, active_constraints, throughput_feasibility, objective_score_breakdown = _solve_band_geometry(
                solver_input=solver_input,
                program=resolved_program,
                conflicts=conflicts,
            )
        else:
            band_solutions = _default_band_solutions(resolved_program, solver_input.constraint_set)
            active_constraints = tuple(name for solution in band_solutions for name in solution.active_constraint_names)
            throughput_feasibility = _throughput_feasibility(resolved_program, solver_input.constraint_set)
            objective_score_breakdown = {
                "total_width_score": float(sum(float(solution.width_m) * float(solution.objective_weight) for solution in band_solutions)),
                "unused_row_budget_m": 0.0,
                "slot_mix_bias": float(sum(float(value) for value in resolved_program.furniture_requirements.values())),
            }
        preview_slot_plans = _build_slot_plans(
            solver_input=solver_input,
            resolved_program=resolved_program,
            rule_effects=rule_effects,
            edits=edits,
            conflicts=conflicts,
        )
        keepout_rules = _keepout_rules(solver_input.constraint_set, solver_input.placement_context)
        if any(slot.anchor_poi_type for slot in preview_slot_plans):
            graph_summary = (
                solver_input.road_segment_graph.summary()
                if solver_input.road_segment_graph is not None and hasattr(solver_input.road_segment_graph, "summary")
                else None
            )
            return _finalize_result(
                solver_input=solver_input,
                resolved_program=resolved_program,
                band_solutions=band_solutions,
                slot_plans=preview_slot_plans,
                rule_effects=rule_effects,
                edits=edits,
                conflicts=conflicts,
                active_constraints=active_constraints,
                throughput_feasibility=throughput_feasibility,
                objective_score_breakdown=objective_score_breakdown,
                backend_requested=requested,
                backend_used="banded",
                fallback_reason=f"{requested} does not support POI-backed anchored slots; fallback to banded",
                road_segment_graph_summary=graph_summary,
            )
        slot_plans, milp_conflicts, meta = solve_candidate_assignment(
            program=resolved_program,
            length_m=float(solver_input.config.length_m),
            segment_length_m=float(getattr(solver_input.config, "segment_length_m", 12.0)),
            graph=solver_input.road_segment_graph,
            requirements=dict(resolved_program.furniture_requirements),
            required_categories=_required_categories(solver_input.constraint_set),
            reserved_band_categories=dict(resolved_program.reserved_band_categories),
            keepout_rules=keepout_rules,
            objective_profile=str(resolved_program.objective_profile),
        )
        all_conflicts = list(conflicts) + list(milp_conflicts)
        if not slot_plans and bool(getattr(solver_input.config, "allow_solver_fallback", True)):
            fallback = solve_layout(solver_input)
            return LayoutSolverResult(
                resolved_program=fallback.resolved_program,
                band_solutions=fallback.band_solutions,
                slot_plans=fallback.slot_plans,
                rule_evaluations=fallback.rule_evaluations,
                edits=fallback.edits + tuple(edits),
                conflicts=fallback.conflicts + tuple(all_conflicts),
                topology_validity=fallback.topology_validity,
                cross_section_feasibility=fallback.cross_section_feasibility,
                rule_satisfaction_rate=fallback.rule_satisfaction_rate,
                editability=fallback.editability,
                conflict_explainability=fallback.conflict_explainability,
                active_constraints=fallback.active_constraints,
                throughput_feasibility=fallback.throughput_feasibility,
                objective_profile=fallback.objective_profile,
                objective_score_breakdown=fallback.objective_score_breakdown,
                backend_requested=requested,
                backend_used="banded",
                fallback_reason=f"{requested} produced no feasible slot assignment; fallback to banded",
                road_segment_graph_summary=meta.get("road_segment_graph_summary"),
            )

        return _finalize_result(
            solver_input=solver_input,
            resolved_program=resolved_program,
            band_solutions=band_solutions,
            slot_plans=slot_plans,
            rule_effects=rule_effects,
            edits=edits,
            conflicts=all_conflicts,
            active_constraints=active_constraints,
            throughput_feasibility=throughput_feasibility,
            objective_score_breakdown=objective_score_breakdown,
            backend_requested=requested,
            backend_used=requested,
            fallback_reason="",
            road_segment_graph_summary=meta.get("road_segment_graph_summary"),
        )
