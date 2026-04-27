"""Rule-based optimization directives for branch design runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence

from .design_types import DEFAULT_COMPOSE_CONFIG_PATCH_VALUES, sanitize_compose_config_patch


_DEMAND_ORDER = ("low", "medium", "high")


@dataclass(frozen=True)
class OptimizationDirective:
    """One bounded optimization instruction that LLM candidates may follow."""

    directive_id: str
    target_metric: str
    problem: str
    direction: str
    allowed_fields: tuple[str, ...]
    suggested_delta: Dict[str, Any] = field(default_factory=dict)
    bounds: Dict[str, Dict[str, float]] = field(default_factory=dict)
    enum_values: Dict[str, tuple[str, ...]] = field(default_factory=dict)
    forbidden_fields: tuple[str, ...] = ()
    risk: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "directive_id": self.directive_id,
            "target_metric": self.target_metric,
            "problem": self.problem,
            "direction": self.direction,
            "allowed_fields": list(self.allowed_fields),
            "suggested_delta": dict(self.suggested_delta),
            "bounds": {key: dict(value) for key, value in self.bounds.items()},
            "enum_values": {key: list(value) for key, value in self.enum_values.items()},
            "forbidden_fields": list(self.forbidden_fields),
            "risk": self.risk,
        }


class RuleBasedOptimizationPlanner:
    """Turn evaluation signals into bounded, inspectable edit directives."""

    def plan(
        self,
        *,
        evaluation: Mapping[str, Any] | None,
        current_patch: Mapping[str, Any] | None,
        generation_diagnostics: Mapping[str, Any] | None = None,
        constraints: Mapping[str, Any] | None = None,
    ) -> List[OptimizationDirective]:
        patch = {
            **DEFAULT_COMPOSE_CONFIG_PATCH_VALUES,
            **sanitize_compose_config_patch(current_patch),
        }
        indicators = dict((evaluation or {}).get("indicators", {}) or {})
        directives: List[OptimizationDirective] = []

        self._append_walkability_directives(directives, indicators, patch)
        self._append_visual_directives(directives, evaluation or {}, patch)
        self._append_constraint_directives(directives, constraints or generation_diagnostics or {}, patch)

        if not directives:
            directives.append(
                OptimizationDirective(
                    directive_id="maintain-balanced-small-step",
                    target_metric="overall",
                    problem="No severe weak metric was detected.",
                    direction="Keep the current design intent and only make small diversity-preserving changes.",
                    allowed_fields=("density", "style_preset", "query"),
                    suggested_delta={"density": 0.05},
                    bounds={"density": _numeric_bounds(patch, "density", step=0.05, minimum=0.4, maximum=1.4)},
                    risk="Large edits are not justified by the current evaluation.",
                )
            )
        return directives[:6]

    def sanitize_candidate_patch(
        self,
        candidate_patch: Mapping[str, Any] | None,
        *,
        current_patch: Mapping[str, Any] | None,
        directives: Sequence[OptimizationDirective | Mapping[str, Any]],
    ) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        raw_patch = sanitize_compose_config_patch(candidate_patch)
        current = {
            **DEFAULT_COMPOSE_CONFIG_PATCH_VALUES,
            **sanitize_compose_config_patch(current_patch),
        }
        normalized_directives = [
            item if isinstance(item, OptimizationDirective) else _directive_from_mapping(item)
            for item in directives
        ]
        allowed_fields = {field for directive in normalized_directives for field in directive.allowed_fields}
        forbidden_fields = {field for directive in normalized_directives for field in directive.forbidden_fields}
        bounds: Dict[str, Dict[str, float]] = {}
        enum_values: Dict[str, set[str]] = {}
        for directive in normalized_directives:
            for field, value in directive.bounds.items():
                if field not in bounds:
                    bounds[field] = dict(value)
                else:
                    bounds[field]["min"] = min(bounds[field].get("min", value.get("min", 0.0)), value.get("min", 0.0))
                    bounds[field]["max"] = max(bounds[field].get("max", value.get("max", 0.0)), value.get("max", 0.0))
            for field, values in directive.enum_values.items():
                enum_values.setdefault(field, set()).update(str(value) for value in values)

        accepted: Dict[str, Any] = {}
        rejected: List[Dict[str, Any]] = []
        for field, value in raw_patch.items():
            if field == "query":
                accepted[field] = value
                continue
            if field in forbidden_fields:
                rejected.append(_rejection(field, value, "forbidden_by_rule_direction"))
                continue
            if allowed_fields and field not in allowed_fields:
                rejected.append(_rejection(field, value, "not_allowed_by_rule_direction"))
                continue
            if field in bounds:
                try:
                    numeric_value = float(value)
                except (TypeError, ValueError):
                    rejected.append(_rejection(field, value, "not_numeric"))
                    continue
                min_value = float(bounds[field].get("min", numeric_value))
                max_value = float(bounds[field].get("max", numeric_value))
                if numeric_value < min_value or numeric_value > max_value:
                    rejected.append(_rejection(field, value, f"outside_bounds_{min_value:g}_{max_value:g}"))
                    numeric_value = max(min_value, min(max_value, numeric_value))
                accepted[field] = numeric_value
                continue
            if field in enum_values:
                text_value = str(value).strip().lower()
                if text_value not in enum_values[field]:
                    rejected.append(_rejection(field, value, "enum_value_not_allowed"))
                    continue
                accepted[field] = text_value
                continue
            accepted[field] = value

        if "query" in current and "query" not in accepted:
            accepted["query"] = current["query"]
        return accepted, rejected

    def _append_walkability_directives(
        self,
        directives: List[OptimizationDirective],
        indicators: Mapping[str, Any],
        patch: Mapping[str, Any],
    ) -> None:
        if _score(indicators.get("tree_shading_rate")) < 0.55:
            directives.append(
                OptimizationDirective(
                    directive_id="improve-tree-shade",
                    target_metric="TREE_SHADE",
                    problem="Tree shading is below the target comfort range.",
                    direction="Increase greening intensity without overfilling the clear path.",
                    allowed_fields=("density", "objective_profile", "ped_demand_level", "query"),
                    suggested_delta={"density": 0.10, "objective_profile": "greening"},
                    bounds={"density": _numeric_bounds(patch, "density", step=0.12, minimum=0.5, maximum=1.45)},
                    enum_values={"objective_profile": ("balanced", "greening"), "ped_demand_level": _DEMAND_ORDER},
                    risk="Too much density can lower clear-path comfort and visual order.",
                )
            )
        if _score(indicators.get("protection")) < 55 or _score(indicators.get("sidewalk_adequacy")) < 0.55:
            directives.append(
                OptimizationDirective(
                    directive_id="restore-clear-sidewalk",
                    target_metric="SID_CLR",
                    problem="Sidewalk clear width or protection is weak.",
                    direction="Increase pedestrian clear width in small steps and avoid adding furniture density.",
                    allowed_fields=("sidewalk_width_m", "ped_demand_level", "query"),
                    forbidden_fields=("density", "road_width_m"),
                    suggested_delta={"sidewalk_width_m": 0.30, "ped_demand_level": "high"},
                    bounds={
                        "sidewalk_width_m": _numeric_bounds(
                            patch,
                            "sidewalk_width_m",
                            step=0.35,
                            minimum=2.0,
                            maximum=5.0,
                        )
                    },
                    enum_values={"ped_demand_level": _DEMAND_ORDER},
                    risk="Increasing assets instead of clear width can make the evaluation worse.",
                )
            )
        if _score(indicators.get("comfort")) < 55:
            directives.append(
                OptimizationDirective(
                    directive_id="improve-comfort",
                    target_metric="comfort",
                    problem="Walkability comfort is below target.",
                    direction="Prefer sidewalk and greening improvements over road widening.",
                    allowed_fields=("sidewalk_width_m", "density", "objective_profile", "query"),
                    suggested_delta={"sidewalk_width_m": 0.20, "density": 0.05},
                    bounds={
                        "sidewalk_width_m": _numeric_bounds(patch, "sidewalk_width_m", step=0.25, minimum=2.0, maximum=5.0),
                        "density": _numeric_bounds(patch, "density", step=0.08, minimum=0.5, maximum=1.35),
                    },
                    enum_values={"objective_profile": ("balanced", "greening")},
                    risk="Do not widen vehicle space when the weak dimension is pedestrian comfort.",
                )
            )
        if _score(indicators.get("delight")) < 55 or _score(indicators.get("furniture_density")) < 0.45:
            directives.append(
                OptimizationDirective(
                    directive_id="increase-amenity-delight",
                    target_metric="FURN_D",
                    problem="Amenity density or street delight is weak.",
                    direction="Add amenities with a capped density increase.",
                    allowed_fields=("density", "objective_profile", "style_preset", "query"),
                    suggested_delta={"density": 0.10},
                    bounds={"density": _numeric_bounds(patch, "density", step=0.12, minimum=0.45, maximum=1.35)},
                    enum_values={"objective_profile": ("balanced", "commerce", "greening")},
                    risk="Excess density can increase blocked slots and hurt clear-path scores.",
                )
            )
        if _score(indicators.get("vehicle_throughput_compliance")) < 0.5:
            directives.append(
                OptimizationDirective(
                    directive_id="protect-throughput",
                    target_metric="vehicle_throughput_compliance",
                    problem="Vehicle or transit throughput is not compliant.",
                    direction="Adjust demand profile instead of making large geometry changes.",
                    allowed_fields=("transit_demand_level", "vehicle_demand_level", "lane_count", "query"),
                    suggested_delta={"transit_demand_level": "high"},
                    bounds={"lane_count": _numeric_bounds(patch, "lane_count", step=1.0, minimum=1, maximum=4)},
                    enum_values={"transit_demand_level": _DEMAND_ORDER, "vehicle_demand_level": _DEMAND_ORDER},
                    risk="Lane-count changes are allowed only by one step.",
                )
            )

    def _append_visual_directives(
        self,
        directives: List[OptimizationDirective],
        evaluation: Mapping[str, Any],
        patch: Mapping[str, Any],
    ) -> None:
        if evaluation.get("safety") is not None and float(evaluation.get("safety") or 0) < 60:
            directives.append(
                OptimizationDirective(
                    directive_id="improve-visual-safety",
                    target_metric="safety",
                    problem="Visual safety score is below target.",
                    direction="Increase pedestrian priority and lighting/visibility related design intent.",
                    allowed_fields=("design_rule_profile", "ped_demand_level", "transit_demand_level", "query"),
                    enum_values={
                        "design_rule_profile": ("balanced_complete_street_v1", "pedestrian_priority_v1"),
                        "ped_demand_level": _DEMAND_ORDER,
                        "transit_demand_level": _DEMAND_ORDER,
                    },
                    risk="Safety visual scores require rendered views; do not optimize this if visual input was unavailable.",
                )
            )
        if evaluation.get("beauty") is not None and float(evaluation.get("beauty") or 0) < 60:
            directives.append(
                OptimizationDirective(
                    directive_id="improve-visual-beauty",
                    target_metric="beauty",
                    problem="Visual beauty score is below target.",
                    direction="Use style and greening/commercial intent rather than large geometry edits.",
                    allowed_fields=("style_preset", "objective_profile", "density", "query"),
                    bounds={"density": _numeric_bounds(patch, "density", step=0.08, minimum=0.5, maximum=1.3)},
                    enum_values={"objective_profile": ("balanced", "greening", "commerce")},
                    risk="Large density increases may cause clutter and reduce beauty.",
                )
            )

    def _append_constraint_directives(
        self,
        directives: List[OptimizationDirective],
        constraints: Mapping[str, Any],
        patch: Mapping[str, Any],
    ) -> None:
        conflicts = list(constraints.get("conflicts", []) or constraints.get("flagged_rule_evaluations", []) or [])
        if conflicts:
            directives.append(
                OptimizationDirective(
                    directive_id="resolve-hard-constraints",
                    target_metric="constraint_solver",
                    problem="Constraint solver reported conflicts or failed rules.",
                    direction="Reduce density and avoid changing road width until constraints recover.",
                    allowed_fields=("density", "sidewalk_width_m", "query"),
                    forbidden_fields=("road_width_m", "lane_count"),
                    suggested_delta={"density": -0.10, "sidewalk_width_m": 0.15},
                    bounds={
                        "density": _numeric_bounds(patch, "density", step=0.12, minimum=0.35, maximum=1.2),
                        "sidewalk_width_m": _numeric_bounds(patch, "sidewalk_width_m", step=0.2, minimum=2.0, maximum=5.0),
                    },
                    risk="LLM edits that ignore active constraints should be rejected.",
                )
            )


def _score(value: Any) -> float:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"excellent", "pass", "high", "good"}:
            return 1.0
        if lowered in {"medium", "fair"}:
            return 0.55
        if lowered in {"low", "very low", "fail", "poor"}:
            return 0.0
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 1.0
    if numeric > 1.0:
        return numeric
    return numeric


def _numeric_bounds(
    patch: Mapping[str, Any],
    field: str,
    *,
    step: float,
    minimum: float,
    maximum: float,
) -> Dict[str, float]:
    try:
        current = float(patch.get(field, DEFAULT_COMPOSE_CONFIG_PATCH_VALUES.get(field, 0.0)) or 0.0)
    except (TypeError, ValueError):
        current = float(DEFAULT_COMPOSE_CONFIG_PATCH_VALUES.get(field, 0.0) or 0.0)
    return {
        "min": max(float(minimum), current - float(step)),
        "max": min(float(maximum), current + float(step)),
    }


def _directive_from_mapping(payload: Mapping[str, Any]) -> OptimizationDirective:
    return OptimizationDirective(
        directive_id=str(payload.get("directive_id", "directive")),
        target_metric=str(payload.get("target_metric", "")),
        problem=str(payload.get("problem", "")),
        direction=str(payload.get("direction", "")),
        allowed_fields=tuple(str(item) for item in payload.get("allowed_fields", []) or []),
        suggested_delta=dict(payload.get("suggested_delta", {}) or {}),
        bounds={str(key): dict(value) for key, value in dict(payload.get("bounds", {}) or {}).items()},
        enum_values={
            str(key): tuple(str(item) for item in (value or ()))
            for key, value in dict(payload.get("enum_values", {}) or {}).items()
        },
        forbidden_fields=tuple(str(item) for item in payload.get("forbidden_fields", []) or []),
        risk=str(payload.get("risk", "")),
    )


def _rejection(field: str, value: Any, reason: str) -> Dict[str, Any]:
    return {"field": field, "value": value, "reason": reason}
