"""Compatibility facade for the active RoadGen3D evaluation engine.

The maintained implementation lives in
``roadgen3d.eval_engine_ext.road_metrics``.  This package remains as a thin
import surface for older code that still imports ``roadgen3d.eval_engine``.
"""

from ..eval_engine_ext.road_metrics.core.config import EvalConfig
from ..eval_engine_ext.road_metrics.core.types import (
    AudioProfile,
    BeautyReport,
    EvaluationResult,
    SafetyReport,
    SceneLayout,
    WalkabilityIndicators,
)
from ..eval_engine_ext.road_metrics.core.engine import EvalEngine
from ..eval_engine_ext.road_metrics.reports import writer
from . import migration

__all__ = [
    # Core
    "EvalEngine",
    "EvalConfig",
    # Types
    "SceneLayout",
    "WalkabilityIndicators",
    "SafetyReport",
    "BeautyReport",
    "AudioProfile",
    "EvaluationResult",
    # Migration
    "migration",
    # Reports
    "writer",
]
