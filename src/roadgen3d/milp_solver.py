"""Discrete MILP-style layout solving for template and OSM segment graphs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

from .types import LayoutConflict, LayoutEdit, LayoutSlotPlan, RoadSegmentGraph, StreetBand, StreetProgram

try:
    import pulp
except ImportError:
    pulp = None


@dataclass(frozen=True)
class _Candidate:
    candidate_id: str
    band_name: str
    side: str
    x_center_m: float
    z_center_m: float
    spacing_m: float
    allowed_categories: Tuple[str, ...]
    priority: float
    segment_id: str = ""
    poi_types: Tuple[str, ...] = ()


def _template_candidates(program: StreetProgram, length_m: float, segment_length_m: float) -> List[_Candidate]:
    candidates: List[_Candidate] = []
    placeable_bands = [band for band in program.bands if band.kind in {"furnishing", "transit_edge"}]
    if not placeable_bands:
        return candidates
    segment_count = max(1, int(round(float(length_m) / max(float(segment_length_m), 4.0))))
    segment = float(length_m) / float(segment_count)
    for band in placeable_bands:
        for idx in range(segment_count):
            x_center = -float(length_m) / 2.0 + (idx + 0.5) * segment
            candidates.append(
                _Candidate(
                    candidate_id=f"{band.name}_{idx:03d}",
                    band_name=band.name,
                    side=band.side,
                    x_center_m=float(x_center),
                    z_center_m=float(band.z_center_m),
                    spacing_m=float(segment),
                    allowed_categories=tuple(band.allowed_categories),
                    priority=1.0,
                )
            )
    return candidates


def _graph_candidates(graph: RoadSegmentGraph) -> List[_Candidate]:
    candidates: List[_Candidate] = []
    for node in graph.nodes:
        for band in node.bands:
            candidates.append(
                _Candidate(
                    candidate_id=str(band.band_id),
                    band_name=str(band.kind),
                    side=str(band.side),
                    x_center_m=float(node.center_xy[0]),
                    z_center_m=float(node.center_xy[1]),
                    spacing_m=float(node.length_m),
                    allowed_categories=tuple(band.allowed_categories),
                    priority=1.0 if not node.is_junction else 0.85,
                    segment_id=str(node.segment_id),
                    poi_types=tuple(band.nearest_poi_types),
                )
            )
    return candidates


def _candidate_utility(candidate: _Candidate, category: str, required: bool) -> float:
    utility = float(candidate.priority)
    if category == "bus_stop" and "bus_stop" in candidate.poi_types:
        utility += 0.5
    if category == "hydrant" and "fire" in candidate.poi_types:
        utility += 0.25
    if category in {"bench", "trash"} and "entrance" in candidate.poi_types:
        utility -= 0.15
    if category == "tree" and "entrance" in candidate.poi_types:
        utility -= 0.2
    if required:
        utility += 0.4
    if candidate.side == "right" and category in {"bus_stop", "mailbox", "hydrant"}:
        utility += 0.15
    return utility


def _greedy_select(
    *,
    candidates: Sequence[_Candidate],
    requirements: Dict[str, int],
    required_categories: Iterable[str],
    reserved_band_categories: Dict[str, str],
) -> Tuple[List[Tuple[_Candidate, str]], List[LayoutConflict]]:
    required_set = set(required_categories)
    remaining = {category: int(count) for category, count in requirements.items() if int(count) > 0}
    selections: List[Tuple[_Candidate, str]] = []
    conflicts: List[LayoutConflict] = []
    used_candidates: set[str] = set()

    for category, count in sorted(remaining.items(), key=lambda item: (item[0] not in required_set, item[0])):
        allowed = [
            candidate for candidate in candidates
            if candidate.candidate_id not in used_candidates
            and category in candidate.allowed_categories
            and (
                reserved_band_categories.get(candidate.band_name, category) == category
                or candidate.band_name not in reserved_band_categories
            )
        ]
        ranked = sorted(allowed, key=lambda candidate: _candidate_utility(candidate, category, category in required_set), reverse=True)
        picked = ranked[:count]
        for candidate in picked:
            selections.append((candidate, category))
            used_candidates.add(candidate.candidate_id)
        if len(picked) < count and category in required_set:
            conflicts.append(
                LayoutConflict(
                    rule_name="milp_capacity",
                    severity="hard",
                    message=f"Not enough feasible candidates for required category '{category}'",
                    affected_target=category,
                )
            )
    return selections, conflicts


def _pulp_select(
    *,
    candidates: Sequence[_Candidate],
    requirements: Dict[str, int],
    required_categories: Iterable[str],
    reserved_band_categories: Dict[str, str],
) -> Tuple[List[Tuple[_Candidate, str]], List[LayoutConflict]]:
    if pulp is None:
        return _greedy_select(
            candidates=candidates,
            requirements=requirements,
            required_categories=required_categories,
            reserved_band_categories=reserved_band_categories,
        )

    required_set = set(required_categories)
    feasible_pairs: List[Tuple[int, str, _Candidate]] = []
    for idx, candidate in enumerate(candidates):
        for category, count in requirements.items():
            if count <= 0:
                continue
            if category not in candidate.allowed_categories:
                continue
            reserved = reserved_band_categories.get(candidate.band_name)
            if reserved is not None and reserved != category:
                continue
            feasible_pairs.append((idx, category, candidate))

    problem = pulp.LpProblem("RoadGen3DLayoutMILP", pulp.LpMaximize)
    variables: Dict[Tuple[int, str], object] = {
        (idx, category): pulp.LpVariable(f"x_{idx}_{category}", cat="Binary")
        for idx, category, _candidate in feasible_pairs
    }

    problem += pulp.lpSum(
        _candidate_utility(candidate, category, category in required_set) * variables[(idx, category)]
        for idx, category, candidate in feasible_pairs
    )

    for idx, _candidate in enumerate(candidates):
        vars_for_candidate = [variables[(pair_idx, category)] for pair_idx, category, _ in feasible_pairs if pair_idx == idx]
        if vars_for_candidate:
            problem += pulp.lpSum(vars_for_candidate) <= 1

    conflicts: List[LayoutConflict] = []
    for category, count in requirements.items():
        if count <= 0:
            continue
        vars_for_category = [variables[(idx, cat)] for idx, cat, _ in feasible_pairs if cat == category]
        if not vars_for_category:
            if category in required_set:
                conflicts.append(
                    LayoutConflict(
                        rule_name="milp_capacity",
                        severity="hard",
                        message=f"No feasible candidates for required category '{category}'",
                        affected_target=category,
                    )
                )
            continue
        if category in required_set:
            problem += pulp.lpSum(vars_for_category) == int(count)
        else:
            problem += pulp.lpSum(vars_for_category) <= int(count)

    status = problem.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus.get(status, "") not in {"Optimal", "Integer Feasible"}:
        conflicts.append(
            LayoutConflict(
                rule_name="milp_status",
                severity="hard",
                message=f"MILP solve status: {pulp.LpStatus.get(status, 'Unknown')}",
                affected_target="layout",
            )
        )
        return [], conflicts

    selections: List[Tuple[_Candidate, str]] = []
    for idx, category, candidate in feasible_pairs:
        var = variables[(idx, category)]
        if float(getattr(var, "value", lambda: 0.0)()) >= 0.5:
            selections.append((candidate, category))
    return selections, conflicts


def solve_candidate_assignment(
    *,
    program: StreetProgram,
    length_m: float,
    segment_length_m: float,
    graph: RoadSegmentGraph | None,
    requirements: Dict[str, int],
    required_categories: Iterable[str],
    reserved_band_categories: Dict[str, str],
) -> Tuple[Tuple[LayoutSlotPlan, ...], List[LayoutConflict], Dict[str, object]]:
    """Solve discrete slot activation/assignment for template or OSM graphs."""

    if graph is not None:
        candidates = _graph_candidates(graph)
        summary = graph.summary()
    else:
        candidates = _template_candidates(program, length_m=length_m, segment_length_m=segment_length_m)
        summary = None

    selected, conflicts = _pulp_select(
        candidates=candidates,
        requirements=requirements,
        required_categories=required_categories,
        reserved_band_categories=reserved_band_categories,
    )

    slot_plans = tuple(
        LayoutSlotPlan(
            slot_id=f"{category}_{index:03d}",
            category=category,
            band_name=candidate.band_name,
            x_center_m=float(candidate.x_center_m),
            z_center_m=float(candidate.z_center_m),
            spacing_m=float(candidate.spacing_m),
            side=str(candidate.side),
            priority=float(candidate.priority),
            required=category in set(required_categories),
        )
        for index, (candidate, category) in enumerate(
            sorted(selected, key=lambda item: (item[1], item[0].x_center_m, item[0].z_center_m))
        )
    )

    if not slot_plans and not conflicts:
        conflicts.append(
            LayoutConflict(
                rule_name="milp_empty",
                severity="hard",
                message="MILP candidate assignment produced zero slots",
                affected_target="layout",
            )
        )
    return slot_plans, conflicts, {"road_segment_graph_summary": summary}
