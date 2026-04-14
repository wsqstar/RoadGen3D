"""Standalone evaluation engine for RoadGen3D scenes.

This module is fully decoupled from RoadGen3D internals and can evolve independently.
It uses universal parameters through EvalConfig for all thresholds and weights.

Usage:
    from roadgen3d.eval_engine import EvalEngine, EvalConfig

    # Load scene
    import json
    payload = json.loads(Path("scene_layout.json").read_text())

    # Configure (optional)
    config = EvalConfig.from_dict({
        "walkability": {"protection_weight": 0.45},
        "enable_llm_eval": True,
    })

    # Evaluate
    engine = EvalEngine(config)
    result = engine.evaluate(payload)
    print(result.evaluation_score)
"""

from .core.config import EvalConfig
from .core.types import (
    AudioProfile,
    BeautyReport,
    EvaluationResult,
    SafetyReport,
    SceneLayout,
    WalkabilityIndicators,
)
from .core.engine import EvalEngine
from . import migration
from .reports import writer

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
