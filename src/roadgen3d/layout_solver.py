"""Constraint-aware layout solver for the neuralsymbolic street pipeline."""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, Iterable, List, Sequence, Set, Tuple

from .street_priors import CATEGORY_SUBSTITUTIONS, SIDE_PREF
from .types import (
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


def _rebuild_program(
    program: StreetProgram,
    *,
    lane_count: int,
    bands: Sequence[StreetBand],
    requirements: Dict[str, int],
) -> StreetProgram:
    rebuilt_bands = _recompute_bands(bands)
    clear_widths = [float(band.width_m) for band in rebuilt_bands if band.kind == "clear_path"]
    furnishing_widths = [float(band.width_m) for band in rebuilt_bands if band.kind in {"furnishing", "transit_edge"}]
    return StreetProgram(
        query=program.query,
        road_type=program.road_type,
        city_context=program.city_context,
        target_standard=program.target_standard,
        lane_count=int(max(1, lane_count)),
        cross_section_type=program.cross_section_type,
        road_width_m=float(next((band.width_m for band in rebuilt_bands if band.kind == "carriageway"), program.road_width_m)),
        sidewalk_width_m=float(max(clear_widths) if clear_widths else program.sidewalk_width_m),
        furnishing_width_m=float(max(furnishing_widths) if furnishing_widths else program.furnishing_width_m),
        bands=rebuilt_bands,
        furniture_requirements=dict(requirements),
        control_points=program.control_points,
        design_goals=program.design_goals,
        context_conditions=dict(program.context_conditions),
        notes=program.notes,
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
        if rule.target == "category_min_count" and rule.mode == "hard":
            category = str(_rule_parameter(rule, "category", "")).strip()
            if category:
                required.add(category)
        if rule.target == "required_category_available" and rule.mode == "hard":
            category = str(_rule_parameter(rule, "category", "")).strip()
            if category:
                required.add(category)
    return required


def _default_band_order(category: str, bands: Sequence[StreetBand]) -> List[StreetBand]:
    placeable = [band for band in bands if band.kind in {"furnishing", "transit_edge"}]
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
        elif rule.target == "band_min_width":
            band_kind = str(_rule_parameter(rule, "band_kind", "")).strip()
            for idx, band in enumerate(list(bands)):
                if band.kind != band_kind:
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
                            reason=f"{rule.name}: widened {band.name} to satisfy {rule.operator} {rule.value}",
                        )
                    )
                    rule_effects.setdefault(rule.name, {"edits": [], "conflicts": []})["edits"].append(band.name)
        elif rule.target == "category_min_count":
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
                        reason=f"{rule.name}: raised {category} count to satisfy {rule.operator} {rule.value}",
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
    rule_effects: Dict[str, Dict[str, List[str]]],
    edits: List[LayoutEdit],
    conflicts: List[LayoutConflict],
) -> List[StreetBand]:
    band_rules = _allowed_band_map(constraint_set)
    reserved_bands = _reserved_band_map(constraint_set)
    all_allowed = band_rules.get("all")
    specific_allowed = band_rules.get(category)

    def _band_allowed(band: StreetBand) -> bool:
        if specific_allowed is not None and band.kind not in specific_allowed:
            return False
        if all_allowed is not None and band.kind not in all_allowed:
            return False
        reserved_category = reserved_bands.get(band.kind)
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


def _build_slot_plans(
    solver_input: LayoutSolverInput,
    resolved_program: StreetProgram,
    rule_effects: Dict[str, Dict[str, List[str]]],
    edits: List[LayoutEdit],
    conflicts: List[LayoutConflict],
) -> Tuple[LayoutSlotPlan, ...]:
    slot_plans: List[LayoutSlotPlan] = []
    required_categories = _required_categories(solver_input.constraint_set)

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
            rule_effects=rule_effects,
            edits=edits,
            conflicts=conflicts,
        )
        if not allowed_bands:
            continue

        segment = float(solver_input.config.length_m) / float(max(count, 1))
        for idx in range(count):
            band = allowed_bands[idx % len(allowed_bands)]
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

    slot_plans.sort(key=lambda slot: (slot.category, slot.x_center_m, slot.z_center_m))
    return tuple(slot_plans)


def _evaluate_rule(
    *,
    rule: DesignRuleSpec,
    resolved_program: StreetProgram,
    slot_plans: Sequence[LayoutSlotPlan],
    rule_effects: Dict[str, Dict[str, List[str]]],
    conflicts: Sequence[LayoutConflict],
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
    elif rule.target == "category_min_count":
        category = str(_rule_parameter(rule, "category", ""))
        actual = sum(1 for slot in slot_plans if slot.category == category)
        satisfied = actual >= int(rule.value)
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
        band_by_name = {band.name: band for band in resolved_program.bands}
        satisfied = all(band_by_name.get(slot.band_name, StreetBand("", "", "", 0.0, 0.0)).kind in allowed for slot in relevant)
    elif rule.target == "reserved_band_category":
        band_kind = str(_rule_parameter(rule, "band_kind", ""))
        band_by_name = {band.name: band for band in resolved_program.bands}
        relevant = [slot for slot in slot_plans if band_by_name.get(slot.band_name, StreetBand("", "", "", 0.0, 0.0)).kind == band_kind]
        satisfied = all(slot.category == str(rule.value) for slot in relevant)
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


def solve_layout(solver_input: LayoutSolverInput) -> LayoutSolverResult:
    """Compile a StreetProgram plus rules into a constrained slot plan."""

    resolved_program, rule_effects, edits, conflicts = _compile_program(solver_input)
    slot_plans = _build_slot_plans(
        solver_input=solver_input,
        resolved_program=resolved_program,
        rule_effects=rule_effects,
        edits=edits,
        conflicts=conflicts,
    )
    evaluations = tuple(
        _evaluate_rule(
            rule=rule,
            resolved_program=resolved_program,
            slot_plans=slot_plans,
            rule_effects=rule_effects,
            conflicts=conflicts,
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

    rule_satisfaction_rate = (
        sum(float(evaluation.score) for evaluation in evaluations) / len(evaluations)
        if evaluations
        else 1.0
    )
    editability = 1.0 if not edits else float(sum(1 for edit in edits if edit.reason.strip()) / len(edits))
    conflict_explainability = 1.0 if not conflicts else float(sum(1 for conflict in conflicts if conflict.message.strip()) / len(conflicts))

    return LayoutSolverResult(
        resolved_program=resolved_program,
        slot_plans=slot_plans,
        rule_evaluations=evaluations,
        edits=tuple(edits),
        conflicts=tuple(conflicts),
        topology_validity=float(topology_validity),
        cross_section_feasibility=float(max(0.0, min(cross_section_feasibility, 1.0))),
        rule_satisfaction_rate=float(max(0.0, min(rule_satisfaction_rate, 1.0))),
        editability=float(max(0.0, min(editability, 1.0))),
        conflict_explainability=float(max(0.0, min(conflict_explainability, 1.0))),
    )
