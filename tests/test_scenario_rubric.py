from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.scenario_rubric import (  # noqa: E402
    ScenarioRubricEvaluator,
    load_scenario_rubric,
)


def test_scenario_rubric_loads_json_and_evaluates_all_scenarios():
    rubric = load_scenario_rubric()
    assert len(rubric["scenarios"]) == 7

    evaluator = ScenarioRubricEvaluator(rubric_config=rubric)
    result = evaluator.evaluate_layout(
        {},
        "scenario_05_furniture_enriched_activity_street",
    )

    assert result["status"] != "NotApplicable"
    assert result["metric_results"]


def test_scenario_rubric_lower_is_better_and_missing_metric_review():
    evaluator = ScenarioRubricEvaluator(rubric_config=_custom_rubric({
        "visual_clutter": {
            "dimension": "PlaceQuality",
            "minimum": 0.5,
            "target": 0.35,
            "excellent": 0.2,
        },
        "NOT_A_REAL_METRIC": {
            "dimension": "Walkability",
            "minimum": 0.1,
            "target": 0.2,
            "excellent": 0.3,
        },
    }))

    result = evaluator.evaluate_layout(_minimal_layout(lane_count=2, visual_clutter=0.4), "scenario_test")

    by_metric = {item["metric"]: item for item in result["metric_results"]}
    assert by_metric["visual_clutter"]["direction"] == "lower"
    assert by_metric["visual_clutter"]["status"] == "Review"
    assert by_metric["NOT_A_REAL_METRIC"]["status"] == "Missing"
    assert result["status"] == "Review"
    assert result["missing_metrics"] == ["NOT_A_REAL_METRIC"]


def test_scenario_rubric_required_semantic_gate_can_fail_status():
    rubric = _custom_rubric({})
    rubric["scenarios"][0]["semantic_gates"] = [
        {
            "gate_id": "lane_count_le_2",
            "type": "numeric_max",
            "path": ["config", "lane_count"],
            "max": 2,
            "severity": "fail",
        }
    ]
    evaluator = ScenarioRubricEvaluator(rubric_config=rubric)

    result = evaluator.evaluate_layout(_minimal_layout(lane_count=4), "scenario_test")

    assert result["status"] == "Fail"
    assert result["semantic_gates"][0]["status"] == "Fail"
    assert any("lane_count_le_2" in reason for reason in result["status_reasons"])


def test_scenario_rubric_profile_pair_threshold_overrides_default_status():
    rubric = _custom_rubric({})
    rubric["scenarios"][0]["profile_pair_thresholds"] = {
        "walkable_commercial+commercial_vitality": {
            "total_thresholds": {"minimum": 1.1, "target": 1.1},
        }
    }
    evaluator = ScenarioRubricEvaluator(rubric_config=rubric)

    matched = evaluator.evaluate_layout(
        _minimal_layout(
            skeleton_design_profile="walkable_commercial",
            street_furniture_profile="commercial_vitality",
        ),
        "scenario_test",
    )
    fallback = evaluator.evaluate_layout(
        _minimal_layout(
            skeleton_design_profile="green_walkable",
            street_furniture_profile="park_landscape",
        ),
        "scenario_test",
    )

    assert matched["profile_pair"] == "walkable_commercial+commercial_vitality"
    assert matched["profile_pair_threshold_applied"] is True
    assert matched["status"] == "Fail"
    assert fallback["profile_pair_threshold_applied"] is False
    assert fallback["status"] == "Pass"


def test_scenario_rubric_profile_equals_gate_reads_a_or_b_layer():
    rubric = _custom_rubric({})
    rubric["scenarios"][0]["semantic_gates"] = [
        {
            "gate_id": "requires_transit_furniture",
            "type": "profile_equals",
            "layer": "street_furniture",
            "profile": "transit_priority",
            "severity": "fail",
        }
    ]
    evaluator = ScenarioRubricEvaluator(rubric_config=rubric)

    result = evaluator.evaluate_layout(
        _minimal_layout(
            skeleton_design_profile="transit_priority",
            street_furniture_profile="commercial_vitality",
        ),
        "scenario_test",
    )

    assert result["semantic_gates"][0]["status"] == "Fail"
    assert result["status"] == "Fail"


def _custom_rubric(metric_thresholds):
    return {
        "schema_version": "roadgen3d_scenario_rubric_v1",
        "lower_is_better_metrics": ["visual_clutter"],
        "defaults": {
            "total_thresholds": {"minimum": 0.0, "target": 0.0, "excellent": 1.0},
            "dimension_weights": {"Walkability": 0.4, "Safety": 0.35, "PlaceQuality": 0.25},
            "metric_thresholds": metric_thresholds,
            "profile_pair_thresholds": {},
        },
        "scenarios": [
            {
                "scenario_id": "scenario_test",
                "enabled": True,
                "semantic_gates": [],
            }
        ],
    }


def _minimal_layout(
    *,
    lane_count: int = 2,
    visual_clutter: float = 0.1,
    skeleton_design_profile: str = "quiet_residential",
    street_furniture_profile: str = "balanced_complete",
):
    return {
        "config": {
            "lane_count": lane_count,
            "length_m": 80.0,
            "road_width_m": 10.0,
            "sidewalk_width_m": 3.0,
            "density": 0.8,
        },
        "summary": {
            "semantic_design_layers": {
                "skeleton_design_profile": skeleton_design_profile,
                "street_furniture_profile": street_furniture_profile,
                "profile_pair": f"{skeleton_design_profile}+{street_furniture_profile}",
            },
            "length_m": 80.0,
            "road_width_m": 10.0,
            "sidewalk_width_m": 3.0,
            "left_clear_path_width_m": 2.8,
            "right_clear_path_width_m": 2.8,
            "left_furnishing_width_m": 1.0,
            "right_furnishing_width_m": 1.0,
            "mean_entrance_openness": 1.0,
            "dropped_slot_rate": 0.0,
            "composition_report": {
                "presentation_score": 0.8,
                "style_coherence": 0.9,
                "visual_clutter": visual_clutter,
                "spacing_rhythm": 0.8,
                "focal_readability": 0.8,
            },
        },
        "placements": [
            {"category": "tree", "x": 0, "z": 3},
            {"category": "lamp", "x": 10, "z": 3},
            {"category": "bollard", "x": 20, "z": 3},
        ],
        "surface_annotations": [
            {"id": "test_bike_lane", "surface_role": "bike_lane", "kind": "colored_pavement"}
        ],
    }
