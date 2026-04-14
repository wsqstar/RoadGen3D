"""Safety metrics computation.

Supports both structural (no LLM) and LLM-enhanced evaluation.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Sequence

from ..core.config import SafetyConfig


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return float(max(lo, min(hi, value)))


def _mean(values: Sequence[float]) -> float:
    items = [float(v) for v in values if v is not None]
    return float(sum(items) / len(items)) if items else 0.0


def compute_structural_safety(
    light_uni: float,
    cross_prov: float,
    buffer_ratio: float,
    bollard_count: int,
    length_m: float,
    mean_openness: float = 1.0,
    dropped_slot_rate: float = 0.0,
    config: SafetyConfig | None = None,
) -> tuple[Dict[str, float], float]:
    """Compute structural safety score (no LLM).

    Returns:
        (features, structural_score)
    """
    cfg = config or SafetyConfig()

    # 提取特征
    bollard_density = _clamp((bollard_count / max(length_m, 1e-6)) / cfg.bollard_density_ideal)
    visibility_penalty = _clamp((1.0 - mean_openness) * dropped_slot_rate)

    features = {
        "LIGHT_UNI": light_uni,
        "CROSS_PROV": cross_prov,
        "BUFFER_RATIO": buffer_ratio,
        "BOLLARD_DENSITY": bollard_density,
        "VISIBILITY_PENALTY": visibility_penalty,
    }

    # 结构化评分
    structural = (
        cfg.cross_prov_weight * cross_prov
        + cfg.light_uni_weight * light_uni
        + cfg.buffer_ratio_weight * buffer_ratio
        + cfg.bollard_density_weight * bollard_density
        + max(0.0, cfg.visibility_weight - visibility_penalty)
    )

    return features, round(_clamp(structural), 4)


def compute_llm_enhanced_safety(
    features: Dict[str, float],
    llm_scores: Dict[str, float],
    config: SafetyConfig | None = None,
) -> tuple[float, bool]:
    """Compute LLM-enhanced safety score.

    Args:
        features: Structural features from compute_structural_safety
        llm_scores: LLM sub-dimension scores {lighting, visibility, protection, activation}
        config: Safety parameters

    Returns:
        (final_score, needs_review)
    """
    cfg = config or SafetyConfig()

    llm_mean = _mean([
        llm_scores.get("lighting", 0.0),
        llm_scores.get("visibility", 0.0),
        llm_scores.get("protection", 0.0),
        llm_scores.get("activation", 0.0),
    ])

    final_score = _clamp(
        cfg.llm_weight * llm_mean
        + cfg.llm_cross_prov_weight * features["CROSS_PROV"]
        + cfg.llm_light_uni_weight * features["LIGHT_UNI"]
        + cfg.llm_buffer_ratio_weight * features["BUFFER_RATIO"]
    )

    # 方差检查: 如果LLM子维度分数不一致,标记需要人工审查
    needs_review = False
    llm_values = [float(llm_scores.get(k, 0.0)) for k in ("lighting", "visibility", "protection", "activation")]
    if len(llm_values) > 1:
        mean_val = _mean(llm_values)
        variance = _mean([(v - mean_val) ** 2 for v in llm_values])
        stddev = math.sqrt(max(variance, 0.0))
        if stddev > cfg.llm_stddev_threshold:
            needs_review = True

    return round(final_score, 4), needs_review


def diagnose_safety(
    features: Dict[str, float],
    llm_scores: Dict[str, float] | None = None,
) -> Dict[str, Any]:
    """Identify the weakest safety dimension.

    Returns:
        Diagnosis with weakest feature and all scores
    """
    items = []
    for key in ("CROSS_PROV", "LIGHT_UNI", "BUFFER_RATIO", "BOLLARD_DENSITY", "VISIBILITY_PENALTY"):
        val = float(features.get(key, 0.0))
        # For visibility penalty, lower is better
        score = 1.0 - val if key == "VISIBILITY_PENALTY" else val
        items.append({"feature": key, "score": round(score, 4)})

    if llm_scores:
        for key in ("lighting", "visibility", "protection", "activation"):
            val = float(llm_scores.get(key, 0.0))
            items.append({"feature": f"llm_{key}", "score": round(val, 4)})

    if not items:
        return {"weakest": None, "score": 0.0}

    weakest = min(items, key=lambda x: x["score"])
    return {"weakest": weakest["feature"], "score": weakest["score"], "all_scores": items}
