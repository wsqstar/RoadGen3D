"""Report writer for evaluation engine.

Generates JSON reports compatible with existing RoadGen3D tools.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping

from ..core.types import EvaluationResult


def write_evaluation_report(result: EvaluationResult, out_path: Path) -> None:
    """Write complete evaluation report to JSON.

    Args:
        result: EvaluationResult from EvalEngine
        out_path: Output file path
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=True),
        encoding="utf-8",
    )


def write_walkability_report(result: EvaluationResult, out_path: Path) -> None:
    """Write walkability-only report (backward compatible)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(result.walkability.to_dict(), indent=2, ensure_ascii=True),
        encoding="utf-8",
    )


def write_safety_report(result: EvaluationResult, out_path: Path) -> None:
    """Write safety-only report."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(result.safety.to_dict(), indent=2, ensure_ascii=True),
        encoding="utf-8",
    )


def write_beauty_report(result: EvaluationResult, out_path: Path) -> None:
    """Write beauty-only report."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(result.beauty.to_dict(), indent=2, ensure_ascii=True),
        encoding="utf-8",
    )


def write_comparison_report(
    current: EvaluationResult,
    previous: EvaluationResult,
    out_path: Path,
) -> None:
    """Write comparison report between two evaluations.

    Args:
        current: Current evaluation result
        previous: Previous evaluation result
        out_path: Output file path
    """
    comparison = {
        "current": current.to_dict(),
        "previous": previous.to_dict(),
        "delta": {
            "walkability_index": current.walkability.walkability_index - previous.walkability.walkability_index,
            "safety_score": current.safety.final_score - previous.safety.final_score,
            "beauty_score": current.beauty.final_score - previous.beauty.final_score,
            "evaluation_score": current.evaluation_score - previous.evaluation_score,
        },
        "improvements": [],
        "regressions": [],
    }

    # 识别改进和退步
    if comparison["delta"]["walkability_index"] > 0.01:
        comparison["improvements"].append("walkability")
    elif comparison["delta"]["walkability_index"] < -0.01:
        comparison["regressions"].append("walkability")

    if comparison["delta"]["safety_score"] > 0.01:
        comparison["improvements"].append("safety")
    elif comparison["delta"]["safety_score"] < -0.01:
        comparison["regressions"].append("safety")

    if comparison["delta"]["beauty_score"] > 0.01:
        comparison["improvements"].append("beauty")
    elif comparison["delta"]["beauty_score"] < -0.01:
        comparison["regressions"].append("beauty")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(comparison, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
