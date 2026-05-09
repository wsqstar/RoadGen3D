"""Scenario-specific deterministic rubric evaluation.

This module sits above road-metrics: it reuses the existing structural
walkability/safety/beauty scores, then applies scenario thresholds and
semantic gates from a machine-readable rubric JSON.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, Mapping, Sequence

from .json_safe import make_json_safe


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_RUBRIC_PATH = ROOT / "data" / "scenario_designs" / "hkust_gz_gate_evaluation_rubric.json"
_MPL_DIR = Path(tempfile.gettempdir()) / "roadgen3d_matplotlib"
_CACHE_DIR = Path(tempfile.gettempdir()) / "roadgen3d_cache"
_MPL_DIR.mkdir(parents=True, exist_ok=True)
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_MPL_DIR))
os.environ.setdefault("XDG_CACHE_HOME", str(_CACHE_DIR))

from .eval_engine_ext.road_metrics.core.config import EvalConfig  # noqa: E402
from .eval_engine_ext.road_metrics.core.engine import EvalEngine  # noqa: E402

RUBRIC_RESULT_SCHEMA_VERSION = "roadgen3d_scenario_rubric_result_v1"
RUBRIC_BATCH_SCHEMA_VERSION = "roadgen3d_scenario_rubric_batch_v1"


class ScenarioRubricError(RuntimeError):
    """Raised when a scenario rubric is malformed or cannot be evaluated."""


@dataclass(frozen=True)
class MetricObservation:
    metric: str
    value: float | None
    source: str
    dimension: str


class ScenarioRubricEvaluator:
    """Evaluate a scene layout against a scenario-specific rubric."""

    def __init__(
        self,
        *,
        rubric_path: str | Path | None = None,
        rubric_config: Mapping[str, Any] | None = None,
        eval_engine: EvalEngine | None = None,
    ) -> None:
        self.rubric_path = Path(rubric_path or DEFAULT_SCENARIO_RUBRIC_PATH).expanduser().resolve()
        self.rubric = load_scenario_rubric(self.rubric_path) if rubric_config is None else validate_scenario_rubric(rubric_config)
        if eval_engine is None:
            eval_config = EvalConfig.default()
            eval_config.enable_llm_eval = False
            eval_config.enable_audio_profile = False
            self.eval_engine = EvalEngine(eval_config)
        else:
            self.eval_engine = eval_engine

    def evaluate_layout_path(
        self,
        layout_path: str | Path,
        scenario_id: str,
        *,
        force_disabled: bool = False,
    ) -> Dict[str, Any]:
        path = Path(layout_path).expanduser()
        if not path.exists():
            raise ScenarioRubricError(f"scene_layout.json not found: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ScenarioRubricError(f"Invalid scene layout JSON: {path}: {exc}") from exc
        result = self.evaluate_layout(payload, scenario_id, force_disabled=force_disabled)
        result["layout_path"] = str(path.resolve())
        return result

    def evaluate_layout(
        self,
        payload: Mapping[str, Any],
        scenario_id: str,
        *,
        force_disabled: bool = False,
    ) -> Dict[str, Any]:
        scenario = _scenario_by_id(self.rubric, scenario_id)
        if scenario is None:
            raise ScenarioRubricError(f"Scenario rubric not found: {scenario_id}")

        if scenario.get("enabled") is False and not force_disabled:
            return make_json_safe({
                "schema_version": RUBRIC_RESULT_SCHEMA_VERSION,
                "scenario_id": scenario_id,
                "status": "NotApplicable",
                "reason": str(scenario.get("disabled_reason") or "Scenario is disabled in rubric."),
                "future_ready": bool(scenario.get("future_ready", False)),
                "capability_gaps": list(scenario.get("capability_gaps") or []),
                "total_score": None,
                "dimension_scores": {},
                "metric_results": [],
                "semantic_gates": [],
                "missing_metrics": [],
                "evidence": {
                    "mode": "structural_only",
                    "llm_used": False,
                    "source_of_truth": str(self.rubric.get("source_of_truth") or self.rubric_path),
                },
            })

        result = self.eval_engine.evaluate(payload)
        result_dict = result.to_dict()
        observations = _collect_metric_observations(payload, result_dict)
        semantic_design_layers = _semantic_design_layers(payload)
        profile_pair = str(semantic_design_layers.get("profile_pair") or "").strip()
        profile_pair_override = _profile_pair_override(self.rubric, scenario, profile_pair)
        metric_thresholds = _merged_metric_thresholds(self.rubric, scenario, profile_pair_override)
        lower_is_better = set(self.rubric.get("lower_is_better_metrics") or [])

        metric_results = [
            _score_metric(metric_name, threshold, observations.get(metric_name), lower_is_better)
            for metric_name, threshold in sorted(metric_thresholds.items())
        ]
        missing_metrics = [
            item["metric"]
            for item in metric_results
            if item["status"] == "Missing"
        ]

        dimension_scores = {
            "Walkability": _round4(result.walkability.walkability_index),
            "Safety": _round4(result.safety.structural_score),
            "PlaceQuality": _round4(result.beauty.structural_score),
        }
        dimension_weights = _merged_dimension_weights(self.rubric, scenario, profile_pair_override)
        total_score = _weighted_sum(dimension_scores, dimension_weights)
        thresholds = _merged_total_thresholds(self.rubric, scenario, profile_pair_override)
        semantic_gate_specs = [
            gate
            for gate in scenario.get("semantic_gates") or []
            if isinstance(gate, Mapping)
        ]
        semantic_gate_specs.extend(
            gate
            for gate in profile_pair_override.get("semantic_gates", []) or []
            if isinstance(gate, Mapping)
        )
        semantic_gates = [
            _evaluate_gate(payload, gate)
            for gate in semantic_gate_specs
        ]

        status, reasons = _classify_status(
            total_score=total_score,
            thresholds=thresholds,
            metric_results=metric_results,
            semantic_gates=semantic_gates,
            missing_metrics=missing_metrics,
        )

        return make_json_safe({
            "schema_version": RUBRIC_RESULT_SCHEMA_VERSION,
            "scenario_id": scenario_id,
            "status": status,
            "status_reasons": reasons,
            "total_score": total_score,
            "dimension_scores": dimension_scores,
            "dimension_weights": dimension_weights,
            "thresholds": thresholds,
            "semantic_design_layers": semantic_design_layers,
            "profile_pair": profile_pair,
            "profile_pair_threshold_applied": bool(profile_pair_override),
            "metric_results": metric_results,
            "semantic_gates": semantic_gates,
            "missing_metrics": missing_metrics,
            "capability_gaps": list(scenario.get("capability_gaps") or []),
            "evidence": {
                "mode": "structural_only",
                "llm_used": False,
                "source_of_truth": str(self.rubric.get("source_of_truth") or self.rubric_path),
                "road_metrics_evaluation_score": _round4(result.evaluation_score),
                "generation_quality_score": result_dict.get("generation_quality_score"),
                "safety_llm_status": result_dict.get("safety", {}).get("llm_status", {}),
                "beauty_llm_status": result_dict.get("beauty", {}).get("llm_status", {}),
            },
        })


def load_scenario_rubric(path: str | Path = DEFAULT_SCENARIO_RUBRIC_PATH) -> Dict[str, Any]:
    rubric_path = Path(path).expanduser().resolve()
    if not rubric_path.exists():
        raise ScenarioRubricError(f"Scenario rubric JSON not found: {rubric_path}")
    try:
        payload = json.loads(rubric_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScenarioRubricError(f"Invalid scenario rubric JSON: {rubric_path}: {exc}") from exc
    return validate_scenario_rubric(payload)


def validate_scenario_rubric(payload: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ScenarioRubricError("Scenario rubric must be a JSON object.")
    schema_version = str(payload.get("schema_version") or "").strip()
    if schema_version != "roadgen3d_scenario_rubric_v1":
        raise ScenarioRubricError("Scenario rubric schema_version must be roadgen3d_scenario_rubric_v1.")
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, Sequence) or isinstance(scenarios, (str, bytes)):
        raise ScenarioRubricError("Scenario rubric scenarios must be an array.")
    ids: list[str] = []
    normalized: list[Dict[str, Any]] = []
    for item in scenarios:
        if not isinstance(item, Mapping):
            raise ScenarioRubricError("Each scenario rubric entry must be an object.")
        scenario_id = str(item.get("scenario_id") or "").strip()
        if not scenario_id:
            raise ScenarioRubricError("Each scenario rubric entry must define scenario_id.")
        ids.append(scenario_id)
        normalized.append(dict(item))
    if len(ids) != len(set(ids)):
        raise ScenarioRubricError("Scenario rubric contains duplicate scenario_id values.")
    defaults = payload.get("defaults")
    if not isinstance(defaults, Mapping):
        raise ScenarioRubricError("Scenario rubric defaults must be an object.")
    return {**dict(payload), "defaults": dict(defaults), "scenarios": normalized}


def missing_layout_evaluation(scenario_id: str, layout_path: str, error: str) -> Dict[str, Any]:
    return make_json_safe({
        "schema_version": RUBRIC_RESULT_SCHEMA_VERSION,
        "scenario_id": scenario_id,
        "status": "Review",
        "status_reasons": ["scene_layout.json could not be read; rubric evaluation is incomplete."],
        "total_score": None,
        "dimension_scores": {},
        "dimension_weights": {},
        "thresholds": {},
        "metric_results": [],
        "semantic_gates": [],
        "missing_metrics": ["scene_layout.json"],
        "capability_gaps": [str(error)],
        "layout_path": layout_path,
        "evidence": {
            "mode": "structural_only",
            "llm_used": False,
        },
    })


def summarize_scenario_evaluations(items: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    evaluations = [
        dict(item.get("scenario_evaluation") or {})
        for item in items
        if isinstance(item, Mapping) and isinstance(item.get("scenario_evaluation"), Mapping)
    ]
    counts = {"Pass": 0, "Review": 0, "Fail": 0, "NotApplicable": 0}
    totals: list[float] = []
    by_scenario: Dict[str, Dict[str, Any]] = {}
    for result in evaluations:
        status = str(result.get("status") or "Review")
        counts[status] = counts.get(status, 0) + 1
        total = _safe_float(result.get("total_score"))
        if total is not None:
            totals.append(total)
        scenario_id = str(result.get("scenario_id") or "")
        if not scenario_id:
            continue
        entry = by_scenario.setdefault(
            scenario_id,
            {"scenario_id": scenario_id, "count": 0, "status_counts": {}, "total_scores": []},
        )
        entry["count"] += 1
        entry["status_counts"][status] = entry["status_counts"].get(status, 0) + 1
        if total is not None:
            entry["total_scores"].append(total)

    scenario_rows = []
    for entry in by_scenario.values():
        scores = entry.pop("total_scores")
        entry["mean_total_score"] = _round4(mean(scores)) if scores else None
        scenario_rows.append(entry)
    scenario_rows.sort(
        key=lambda row: (-1.0 if row.get("mean_total_score") is None else -float(row["mean_total_score"]), row["scenario_id"])
    )

    return make_json_safe({
        "schema_version": "roadgen3d_scenario_evaluation_summary_v1",
        "evaluated_items": len(evaluations),
        "status_counts": counts,
        "mean_total_score": _round4(mean(totals)) if totals else None,
        "scenario_summaries": scenario_rows,
    })


def build_calibration_table(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    by_scenario: Dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        result = record.get("scenario_evaluation") if "scenario_evaluation" in record else record
        if not isinstance(result, Mapping):
            continue
        scenario_id = str(result.get("scenario_id") or "")
        if scenario_id:
            by_scenario.setdefault(scenario_id, []).append(result)

    rows: list[Dict[str, Any]] = []
    for scenario_id, results in sorted(by_scenario.items()):
        totals = [_safe_float(result.get("total_score")) for result in results]
        totals = [value for value in totals if value is not None]
        status_counts: Dict[str, int] = {}
        failed_gate_count = 0
        gate_count = 0
        metric_values: Dict[str, list[float]] = {}
        for result in results:
            status = str(result.get("status") or "Review")
            status_counts[status] = status_counts.get(status, 0) + 1
            for gate in result.get("semantic_gates") or []:
                if not isinstance(gate, Mapping):
                    continue
                gate_count += 1
                if gate.get("status") == "Fail":
                    failed_gate_count += 1
            for metric in result.get("metric_results") or []:
                if not isinstance(metric, Mapping):
                    continue
                value = _safe_float(metric.get("value"))
                if value is not None:
                    metric_values.setdefault(str(metric.get("metric") or ""), []).append(value)
        rows.append({
            "scenario_id": scenario_id,
            "n": len(results),
            "status_counts": status_counts,
            "mean_total_score": _round4(mean(totals)) if totals else None,
            "min_total_score": _round4(min(totals)) if totals else None,
            "max_total_score": _round4(max(totals)) if totals else None,
            "gate_failure_rate": _round4(failed_gate_count / gate_count) if gate_count else 0.0,
            "metric_distributions": {
                name: {
                    "n": len(values),
                    "mean": _round4(mean(values)),
                    "min": _round4(min(values)),
                    "max": _round4(max(values)),
                }
                for name, values in sorted(metric_values.items())
            },
        })
    return {"schema_version": "roadgen3d_scenario_calibration_table_v1", "rows": rows}


def write_evaluations_csv(records: Sequence[Mapping[str, Any]], path: str | Path) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "run_id",
        "scenario_id",
        "sample_index",
        "status",
        "total_score",
        "walkability",
        "safety",
        "place_quality",
        "failed_gates",
        "missing_metrics",
        "layout_path",
    ]
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            result = record.get("scenario_evaluation") if "scenario_evaluation" in record else record
            if not isinstance(result, Mapping):
                continue
            dimensions = dict(result.get("dimension_scores") or {})
            writer.writerow({
                "run_id": record.get("run_id", ""),
                "scenario_id": result.get("scenario_id", record.get("scenario_id", "")),
                "sample_index": record.get("sample_index", ""),
                "status": result.get("status", ""),
                "total_score": result.get("total_score", ""),
                "walkability": dimensions.get("Walkability", ""),
                "safety": dimensions.get("Safety", ""),
                "place_quality": dimensions.get("PlaceQuality", ""),
                "failed_gates": ";".join(
                    str(gate.get("gate_id") or "")
                    for gate in result.get("semantic_gates") or []
                    if isinstance(gate, Mapping) and gate.get("status") == "Fail"
                ),
                "missing_metrics": ";".join(str(item) for item in result.get("missing_metrics") or []),
                "layout_path": result.get("layout_path", record.get("scene_layout_path", "")),
            })


def write_expert_scoring_template(records: Sequence[Mapping[str, Any]], path: str | Path) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "run_id",
        "scenario_id",
        "sample_index",
        "layout_path",
        "expert_total",
        "expert_walkability",
        "expert_safety",
        "expert_place_quality",
        "expert_notes",
    ]
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            result = record.get("scenario_evaluation") if "scenario_evaluation" in record else {}
            writer.writerow({
                "run_id": record.get("run_id", ""),
                "scenario_id": record.get("scenario_id", result.get("scenario_id", "")),
                "sample_index": record.get("sample_index", ""),
                "layout_path": record.get("scene_layout_path", result.get("layout_path", "")),
                "expert_total": "",
                "expert_walkability": "",
                "expert_safety": "",
                "expert_place_quality": "",
                "expert_notes": "",
            })


def _scenario_by_id(rubric: Mapping[str, Any], scenario_id: str) -> Dict[str, Any] | None:
    wanted = str(scenario_id or "").strip()
    for item in rubric.get("scenarios") or []:
        if isinstance(item, Mapping) and str(item.get("scenario_id") or "").strip() == wanted:
            return dict(item)
    return None


def _semantic_design_layers(payload: Mapping[str, Any]) -> Dict[str, Any]:
    summary = payload.get("summary")
    if isinstance(summary, Mapping) and isinstance(summary.get("semantic_design_layers"), Mapping):
        return dict(summary.get("semantic_design_layers") or {})
    top_level = payload.get("semantic_design_layers")
    if isinstance(top_level, Mapping):
        return dict(top_level)
    config = payload.get("config")
    if isinstance(config, Mapping):
        skeleton = str(config.get("skeleton_design_profile") or "").strip()
        furniture = str(config.get("street_furniture_profile") or "").strip()
        if skeleton or furniture:
            return {
                "skeleton_design_profile": skeleton,
                "street_furniture_profile": furniture,
                "profile_pair": f"{skeleton}+{furniture}" if skeleton and furniture else "",
            }
    return {}


def _merge_profile_pair_config(base: Dict[str, Any], override: Mapping[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if key in {"total_thresholds", "dimension_weights"} and isinstance(value, Mapping):
            result[key] = {**dict(result.get(key) or {}), **dict(value)}
        elif key == "metric_thresholds" and isinstance(value, Mapping):
            merged_metrics = {
                str(metric): dict(threshold)
                for metric, threshold in dict(result.get(key) or {}).items()
                if isinstance(threshold, Mapping)
            }
            for metric, threshold in value.items():
                if isinstance(threshold, Mapping):
                    metric_key = str(metric)
                    merged_metrics[metric_key] = {
                        **merged_metrics.get(metric_key, {}),
                        **dict(threshold),
                    }
            result[key] = merged_metrics
        elif key == "semantic_gates" and isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            result[key] = list(result.get(key) or []) + [item for item in value if isinstance(item, Mapping)]
        else:
            result[str(key)] = value
    return result


def _profile_pair_override(
    rubric: Mapping[str, Any],
    scenario: Mapping[str, Any],
    profile_pair: str,
) -> Dict[str, Any]:
    if not profile_pair:
        return {}
    merged: Dict[str, Any] = {}
    for source in (rubric.get("defaults") or {}, scenario):
        if not isinstance(source, Mapping):
            continue
        overrides = source.get("profile_pair_thresholds")
        if not isinstance(overrides, Mapping):
            continue
        item = overrides.get(profile_pair)
        if isinstance(item, Mapping):
            merged = _merge_profile_pair_config(merged, item)
    return merged


def _merged_total_thresholds(
    rubric: Mapping[str, Any],
    scenario: Mapping[str, Any],
    profile_pair_override: Mapping[str, Any] | None = None,
) -> Dict[str, float]:
    defaults = dict(rubric.get("defaults", {}).get("total_thresholds") or {})
    thresholds = {
        **defaults,
        **dict(scenario.get("total_thresholds") or {}),
        **dict((profile_pair_override or {}).get("total_thresholds") or {}),
    }
    return {key: float(value) for key, value in thresholds.items()}


def _merged_dimension_weights(
    rubric: Mapping[str, Any],
    scenario: Mapping[str, Any],
    profile_pair_override: Mapping[str, Any] | None = None,
) -> Dict[str, float]:
    defaults = dict(rubric.get("defaults", {}).get("dimension_weights") or {})
    weights = {
        **defaults,
        **dict(scenario.get("dimension_weights") or {}),
        **dict((profile_pair_override or {}).get("dimension_weights") or {}),
    }
    total = sum(float(value) for value in weights.values()) or 1.0
    return {str(key): _round4(float(value) / total) for key, value in weights.items()}


def _merged_metric_thresholds(
    rubric: Mapping[str, Any],
    scenario: Mapping[str, Any],
    profile_pair_override: Mapping[str, Any] | None = None,
) -> Dict[str, Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {
        str(key): dict(value)
        for key, value in dict(rubric.get("defaults", {}).get("metric_thresholds") or {}).items()
        if isinstance(value, Mapping)
    }
    for key, value in dict(scenario.get("metric_thresholds") or {}).items():
        if isinstance(value, Mapping):
            merged[str(key)] = {**merged.get(str(key), {}), **dict(value)}
    for key, value in dict((profile_pair_override or {}).get("metric_thresholds") or {}).items():
        if isinstance(value, Mapping):
            merged[str(key)] = {**merged.get(str(key), {}), **dict(value)}
    return merged


def _collect_metric_observations(
    payload: Mapping[str, Any],
    eval_result: Mapping[str, Any],
) -> Dict[str, MetricObservation]:
    observations: Dict[str, MetricObservation] = {}
    walkability = dict(eval_result.get("walkability") or {})
    for name, value in dict(walkability.get("indicators") or {}).items():
        observations[str(name)] = MetricObservation(str(name), _safe_float(value), "road_metrics.walkability.indicators", "Walkability")
    safety = dict(eval_result.get("safety") or {})
    for name, value in dict(safety.get("features") or {}).items():
        observations[str(name)] = MetricObservation(str(name), _safe_float(value), "road_metrics.safety.features", "Safety")
    beauty = dict(eval_result.get("beauty") or {})
    for name, value in dict(beauty.get("features") or {}).items():
        observations[str(name)] = MetricObservation(str(name), _safe_float(value), "road_metrics.beauty.features", "PlaceQuality")

    composition = dict(dict(payload.get("summary") or {}).get("composition_report") or {})
    for name in ("style_coherence", "spacing_rhythm", "focal_readability", "presentation_score", "visual_clutter"):
        if name in composition:
            observations[name] = MetricObservation(name, _safe_float(composition.get(name)), "scene_layout.summary.composition_report", "PlaceQuality")
    return observations


def _score_metric(
    metric_name: str,
    threshold: Mapping[str, Any],
    observation: MetricObservation | None,
    lower_is_better: set[str],
) -> Dict[str, Any]:
    direction = "lower" if metric_name in lower_is_better else "higher"
    dimension = str(threshold.get("dimension") or (observation.dimension if observation else ""))
    minimum = _safe_float(threshold.get("minimum"))
    target = _safe_float(threshold.get("target"))
    excellent = _safe_float(threshold.get("excellent"))
    if observation is None or observation.value is None:
        return {
            "metric": metric_name,
            "dimension": dimension,
            "value": None,
            "minimum": minimum,
            "target": target,
            "excellent": excellent,
            "direction": direction,
            "status": "Missing",
            "source": str(threshold.get("source") or ""),
        }
    value = observation.value
    if direction == "lower":
        if target is not None and value <= target:
            status = "Pass"
        elif minimum is not None and value <= minimum:
            status = "Review"
        else:
            status = "Fail"
    else:
        if target is not None and value >= target:
            status = "Pass"
        elif minimum is not None and value >= minimum:
            status = "Review"
        else:
            status = "Fail"
    return {
        "metric": metric_name,
        "dimension": dimension,
        "value": _round4(value),
        "minimum": minimum,
        "target": target,
        "excellent": excellent,
        "direction": direction,
        "status": status,
        "source": observation.source,
    }


def _evaluate_gate(payload: Mapping[str, Any], gate: Mapping[str, Any]) -> Dict[str, Any]:
    gate_id = str(gate.get("gate_id") or gate.get("id") or gate.get("type") or "semantic_gate")
    gate_type = str(gate.get("type") or "contains_any")
    severity = str(gate.get("severity") or "review")
    if gate_type == "numeric_max":
        value = _value_at_path(payload, gate.get("path"))
        observed = _safe_float(value)
        limit = _safe_float(gate.get("max"))
        passed = observed is not None and limit is not None and observed <= limit
        message = f"{observed} <= {limit}" if observed is not None else "value missing"
    elif gate_type == "numeric_min":
        value = _value_at_path(payload, gate.get("path"))
        observed = _safe_float(value)
        limit = _safe_float(gate.get("min"))
        passed = observed is not None and limit is not None and observed >= limit
        message = f"{observed} >= {limit}" if observed is not None else "value missing"
    elif gate_type == "profile_equals":
        semantic_layers = _semantic_design_layers(payload)
        layer = str(gate.get("layer") or "").strip().lower()
        if layer in {"a", "skeleton", "skeleton_design", "skeleton_design_profile"}:
            observed = str(semantic_layers.get("skeleton_design_profile") or "")
        elif layer in {"b", "furniture", "street_furniture", "street_furniture_profile"}:
            observed = str(semantic_layers.get("street_furniture_profile") or "")
        else:
            observed = str(_value_at_path(payload, gate.get("path")) or "")
        expected_values = [
            str(item).strip()
            for item in (gate.get("any") or gate.get("profiles") or [gate.get("profile") or gate.get("equals")])
            if str(item).strip()
        ]
        passed = observed in set(expected_values)
        message = f"{observed or 'missing'} in {expected_values}" if expected_values else "expected profile missing"
    else:
        tokens = [str(item).strip().lower() for item in gate.get("any") or gate.get("tokens") or [] if str(item).strip()]
        sources = [str(item).strip() for item in gate.get("sources") or ["surface_annotations", "functional_zones", "scenario_design"] if str(item).strip()]
        minimum = int(gate.get("minimum") or gate.get("min_count") or 1)
        matches = _count_token_matches(payload, sources, tokens)
        observed = matches
        passed = matches >= minimum
        message = f"{matches} matching record(s), need {minimum}"
    return {
        "gate_id": gate_id,
        "description": str(gate.get("description") or ""),
        "type": gate_type,
        "severity": severity,
        "status": "Pass" if passed else "Fail",
        "observed": observed,
        "message": message,
    }


def _classify_status(
    *,
    total_score: float,
    thresholds: Mapping[str, Any],
    metric_results: Sequence[Mapping[str, Any]],
    semantic_gates: Sequence[Mapping[str, Any]],
    missing_metrics: Sequence[str],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    minimum_value = thresholds.get("minimum")
    target_value = thresholds.get("target")
    minimum = 0.60 if minimum_value is None else float(minimum_value)
    target = 0.75 if target_value is None else float(target_value)
    severe_gate_failures = [
        str(gate.get("gate_id") or "")
        for gate in semantic_gates
        if gate.get("status") == "Fail" and str(gate.get("severity") or "review") == "fail"
    ]
    review_gate_failures = [
        str(gate.get("gate_id") or "")
        for gate in semantic_gates
        if gate.get("status") == "Fail" and str(gate.get("severity") or "review") != "fail"
    ]
    failed_metrics = [str(item.get("metric") or "") for item in metric_results if item.get("status") == "Fail"]
    review_metrics = [str(item.get("metric") or "") for item in metric_results if item.get("status") == "Review"]

    if total_score < minimum:
        reasons.append(f"total_score {total_score:.4f} < minimum {minimum:.4f}")
    if severe_gate_failures:
        reasons.append("failed required semantic gates: " + ", ".join(severe_gate_failures))
    if failed_metrics:
        reasons.append("metrics below minimum: " + ", ".join(failed_metrics))
    if reasons:
        return "Fail", reasons

    if total_score < target:
        reasons.append(f"total_score {total_score:.4f} < target {target:.4f}")
    if missing_metrics:
        reasons.append("missing metrics: " + ", ".join(missing_metrics))
    if review_metrics:
        reasons.append("metrics need review: " + ", ".join(review_metrics))
    if review_gate_failures:
        reasons.append("semantic gates need review: " + ", ".join(review_gate_failures))
    if reasons:
        return "Review", reasons
    return "Pass", ["total_score and required semantic gates meet target."]


def _weighted_sum(scores: Mapping[str, float], weights: Mapping[str, float]) -> float:
    return _round4(sum(float(scores.get(key, 0.0)) * float(weight) for key, weight in weights.items()))


def _value_at_path(payload: Mapping[str, Any], path_value: Any) -> Any:
    if not isinstance(path_value, Sequence) or isinstance(path_value, (str, bytes)):
        return None
    current: Any = payload
    for key in path_value:
        if not isinstance(current, Mapping):
            return None
        current = current.get(str(key))
    return current


def _count_token_matches(payload: Mapping[str, Any], sources: Sequence[str], tokens: Sequence[str]) -> int:
    if not tokens:
        return 0
    count = 0
    for source in sources:
        records = _records_for_source(payload, source)
        for record in records:
            text = _flatten_for_match(record)
            if any(token in text for token in tokens):
                count += 1
    return count


def _records_for_source(payload: Mapping[str, Any], source: str) -> list[Any]:
    if source == "surface_annotations":
        return list(payload.get("surface_annotations") or [])
    if source == "functional_zones":
        return list(payload.get("functional_zones") or [])
    if source == "scenario_design":
        value = payload.get("scenario_design")
        return [value] if isinstance(value, Mapping) else []
    if source == "visual_surface_role_count":
        roles = dict(dict(payload.get("summary") or {}).get("visual_surface_role_count") or {})
        return [{"role": key, "count": value} for key, value in roles.items() if value]
    if source == "config":
        value = payload.get("config")
        return [value] if isinstance(value, Mapping) else []
    if source == "summary":
        value = payload.get("summary")
        return [value] if isinstance(value, Mapping) else []
    return []


def _flatten_for_match(value: Any) -> str:
    chunks: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                chunks.append(str(key))
                visit(nested)
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            for nested in item:
                visit(nested)
        elif item is not None:
            chunks.append(str(item))

    visit(value)
    return " ".join(chunks).lower()


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _round4(value: float) -> float:
    return round(float(value), 4)


__all__ = [
    "DEFAULT_SCENARIO_RUBRIC_PATH",
    "RUBRIC_BATCH_SCHEMA_VERSION",
    "RUBRIC_RESULT_SCHEMA_VERSION",
    "ScenarioRubricError",
    "ScenarioRubricEvaluator",
    "build_calibration_table",
    "load_scenario_rubric",
    "missing_layout_evaluation",
    "summarize_scenario_evaluations",
    "validate_scenario_rubric",
    "write_evaluations_csv",
    "write_expert_scoring_template",
]
