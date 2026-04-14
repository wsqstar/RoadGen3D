"""Migration layer for backward compatibility with eval_quality.py.

This module provides drop-in replacements for existing eval_quality functions,
redirecting them to the new decoupled eval_engine.

Usage:
    # Old code:
    from roadgen3d.eval_quality import compute_walkability_indicators

    # New code (drop-in replacement):
    from roadgen3d.eval_engine.migration import compute_walkability_indicators
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from .core.engine import EvalEngine
from .core.config import EvalConfig
from .core.types import (
    EvaluationResult,
    WalkabilityIndicators,
    SafetyReport,
    BeautyReport,
)
from .reports.writer import (
    write_evaluation_report,
    write_walkability_report,
    write_safety_report,
    write_beauty_report,
)


# Global engine instance (lazy initialization)
_engine: EvalEngine | None = None


def _get_engine(config: EvalConfig | None = None) -> EvalEngine:
    """Get or create global engine instance."""
    global _engine
    if _engine is None:
        _engine = EvalEngine(config)
    return _engine


def compute_walkability_indicators(layout_payload: Mapping[str, Any]) -> WalkabilityIndicators:
    """Drop-in replacement for eval_quality.compute_walkability_indicators.

    Args:
        layout_payload: scene_layout.json payload

    Returns:
        WalkabilityIndicators (compatible with old format)
    """
    engine = _get_engine()
    result = engine.evaluate(layout_payload)
    return result.walkability


def compute_structured_safety_report(
    layout_payload: Mapping[str, Any],
    walkability: WalkabilityIndicators | None = None,
    llm_scores: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Drop-in replacement for eval_quality.compute_structured_safety_report.

    Args:
        layout_payload: scene_layout.json payload
        walkability: Optional walkability indicators (ignored, recomputed)
        llm_scores: Optional LLM scores (not yet supported in migration)

    Returns:
        Safety report dict (compatible with old format)
    """
    engine = _get_engine()
    result = engine.evaluate(layout_payload)
    return result.safety.to_dict()


def compute_structured_beauty_report(
    layout_payload: Mapping[str, Any],
    llm_scores: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Drop-in replacement for eval_quality.compute_structured_beauty_report.

    Args:
        layout_payload: scene_layout.json payload
        llm_scores: Optional LLM scores (not yet supported in migration)

    Returns:
        Beauty report dict (compatible with old format)
    """
    engine = _get_engine()
    result = engine.evaluate(layout_payload)
    return result.beauty.to_dict()


def write_walkability_report(result: WalkabilityIndicators, out_path: Path) -> None:
    """Drop-in replacement for eval_quality.write_walkability_report."""
    from ..reports.writer import write_walkability_report as _write

    # Wrap in EvaluationResult for compatibility
    eval_result = EvaluationResult(
        walkability=result,
        safety=SafetyReport(),
        beauty=BeautyReport(),
    )
    _write(eval_result, out_path)


def write_json_report(data: Mapping[str, Any], out_path: Path) -> None:
    """Write arbitrary dict to JSON (backward compatible)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )


def evaluate_scene_full(
    layout_payload: Mapping[str, Any],
    config: EvalConfig | None = None,
) -> EvaluationResult:
    """New API: Full evaluation with custom config.

    Args:
        layout_payload: scene_layout.json payload
        config: Custom evaluation parameters

    Returns:
        Complete EvaluationResult
    """
    engine = EvalEngine(config)
    return engine.evaluate(layout_payload)
