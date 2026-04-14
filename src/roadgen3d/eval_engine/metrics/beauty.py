"""Beauty/aesthetics metrics computation.

Supports both structural (no LLM) and LLM-enhanced evaluation.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Sequence

from ..core.config import BeautyConfig


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return float(max(lo, min(hi, value)))


def _mean(values: Sequence[float]) -> float:
    items = [float(v) for v in values if v is not None]
    return float(sum(items) / len(items)) if items else 0.0


_ANCHOR_POI_WEIGHTS = {
    "restaurant": 1.0,
    "cafe": 0.9,
    "bar": 0.8,
    "food": 0.8,
    "cultural": 1.2,
    "museum": 1.2,
    "library": 1.0,
    "school": 0.8,
    "university": 1.0,
    "public_service": 1.1,
    "government": 1.0,
    "healthcare": 1.1,
    "recreation": 0.9,
    "park": 0.9,
    "open_space": 0.9,
    "retail": 0.9,
}


def compute_active_front_ratio(
    entrance_count: int,
    length_m: float,
    active_frontage_span_m: float = 4.0,
    active_frontage_ratio_ideal: float = 0.70,
) -> float:
    """Compute active frontage ratio from entrance count.

    Assumes each active frontage spans ~4m.
    """
    estimated_active_length = entrance_count * active_frontage_span_m
    total_frontage = max(length_m * 2.0, 1e-3)  # Both sides
    return _clamp(estimated_active_length / total_frontage / active_frontage_ratio_ideal)


def compute_anchor_poi_score(
    poi_points: Dict[str, Sequence[Sequence[float]]],
    length_m: float,
    anchor_poi_density_ideal: float = 0.12,
) -> float:
    """Compute anchor POI score with category weights."""
    weighted = 0.0
    for poi_type, points in poi_points.items():
        canonical = str(poi_type).strip().lower()
        weight = _ANCHOR_POI_WEIGHTS.get(canonical)

        if weight is None:
            # Approximate using prefixes
            for key, value in _ANCHOR_POI_WEIGHTS.items():
                if canonical.startswith(key):
                    weight = value
                    break

        if weight is None:
            continue

        weighted += weight * len(points or [])

    density = weighted / max(length_m, 1e-6)
    return _clamp(density / anchor_poi_density_ideal)


def compute_structural_beauty(
    presentation_score: float,
    active_front_ratio: float,
    anchor_poi_score: float,
    visual_clutter: float = 0.0,
    config: BeautyConfig | None = None,
) -> tuple[Dict[str, float], float]:
    """Compute structural beauty score (no LLM).

    Returns:
        (features, structural_score)
    """
    cfg = config or BeautyConfig()

    features = {
        "presentation_score": _clamp(presentation_score),
        "active_front_ratio": active_front_ratio,
        "anchor_poi_score": anchor_poi_score,
        "visual_clutter": _clamp(visual_clutter),
    }

    structural = _clamp(
        cfg.presentation_weight * presentation_score
        + cfg.active_front_weight * active_front_ratio
        + cfg.anchor_poi_weight * anchor_poi_score
        + cfg.visual_clutter_weight * (1.0 - visual_clutter)
    )

    return features, round(structural, 4)


def compute_llm_enhanced_beauty(
    features: Dict[str, float],
    llm_scores: Dict[str, float],
    config: BeautyConfig | None = None,
) -> tuple[float, bool]:
    """Compute LLM-enhanced beauty score.

    Args:
        features: Structural features from compute_structural_beauty
        llm_scores: LLM sub-dimension scores {coherence, human_scale, material_contrast, visual_interest}
        config: Beauty parameters

    Returns:
        (final_score, needs_review)
    """
    cfg = config or BeautyConfig()

    llm_mean = _mean([
        llm_scores.get("coherence", 0.0),
        llm_scores.get("human_scale", 0.0),
        llm_scores.get("material_contrast", 0.0),
        llm_scores.get("visual_interest", 0.0),
    ])

    final_score = _clamp(
        cfg.llm_weight * llm_mean
        + cfg.llm_presentation_weight * features["presentation_score"]
        + cfg.llm_active_front_weight * features["active_front_ratio"]
        + cfg.llm_anchor_poi_weight * features["anchor_poi_score"]
    )

    # 方差检查
    needs_review = False
    llm_values = [float(llm_scores.get(k, 0.0)) for k in ("coherence", "human_scale", "material_contrast", "visual_interest")]
    if len(llm_values) > 1:
        mean_val = _mean(llm_values)
        variance = _mean([(v - mean_val) ** 2 for v in llm_values])
        stddev = math.sqrt(max(variance, 0.0))
        if stddev > cfg.llm_stddev_threshold:
            needs_review = True

    return round(final_score, 4), needs_review


def diagnose_beauty(
    features: Dict[str, float],
    llm_scores: Dict[str, float] | None = None,
) -> Dict[str, Any]:
    """Identify the weakest beauty dimension.

    Returns:
        Diagnosis with weakest feature and all scores
    """
    items = []
    for key in ("presentation_score", "active_front_ratio", "anchor_poi_score", "style_coherence", "spacing_rhythm"):
        val = float(features.get(key, 0.0))
        items.append({"feature": key, "score": round(val, 4)})

    # visual_clutter is inverted (lower is better)
    clutter = float(features.get("visual_clutter", 0.0))
    items.append({"feature": "visual_clutter", "score": round(1.0 - clutter, 4)})

    if llm_scores:
        for key in ("coherence", "human_scale", "material_contrast", "visual_interest"):
            val = float(llm_scores.get(key, 0.0))
            items.append({"feature": f"llm_{key}", "score": round(val, 4)})

    if not items:
        return {"weakest": None, "score": 0.0}

    weakest = min(items, key=lambda x: x["score"])
    return {"weakest": weakest["feature"], "score": weakest["score"], "all_scores": items}
