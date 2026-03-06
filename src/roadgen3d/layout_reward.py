"""Composite value functions and scene-level reward for M4 learned policy."""

from __future__ import annotations

from typing import Dict, List, Sequence, Set


def candidate_value(
    clip_score: float,
    is_unused: bool,
    is_category_fresh: bool,
    category_pool_remaining: int,
    category_pool_total: int,
) -> float:
    """Multi-objective value for a single candidate asset.

    Returns a scalar where higher = better choice for this slot.
    Components:
      - clip_score: FAISS retrieval similarity (query relevance)
      - diversity_bonus: prefer assets not yet placed in the scene
      - freshness_bonus: prefer assets not yet used in this category
      - scarcity_bonus: stronger diversity pressure when pool is running low
    """
    score_norm = max(0.0, min(float(clip_score), 1.0))
    diversity_bonus = 0.3 if is_unused else 0.0
    freshness_bonus = 0.2 if is_category_fresh else 0.0
    scarcity = 1.0 - (float(category_pool_remaining) / max(float(category_pool_total), 1.0))
    scarcity_bonus = 0.1 * scarcity if is_unused else 0.0
    return score_norm + diversity_bonus + freshness_bonus + scarcity_bonus


def compute_optimal_index(
    candidate_asset_ids: Sequence[str],
    candidate_scores: Sequence[float],
    candidate_categories: Sequence[str],
    target_category: str,
    used_asset_ids_before: Set[str],
    category_pool_total: int,
) -> int:
    """Pick the candidate index that maximises composite value.

    Only same-category candidates are eligible.  Falls back to the
    highest-scoring same-category candidate if none remain unused.
    Returns -1 when no same-category candidate exists.
    """
    n = min(len(candidate_asset_ids), len(candidate_scores), len(candidate_categories))
    if n == 0:
        return -1

    category_pool_remaining = max(
        0,
        category_pool_total - len(used_asset_ids_before),
    )

    best_idx = -1
    best_val = -float("inf")
    for i in range(n):
        if candidate_categories[i].strip().lower() != target_category.strip().lower():
            continue
        aid = candidate_asset_ids[i]
        val = candidate_value(
            clip_score=float(candidate_scores[i]),
            is_unused=aid not in used_asset_ids_before,
            is_category_fresh=aid not in used_asset_ids_before,
            category_pool_remaining=category_pool_remaining,
            category_pool_total=category_pool_total,
        )
        if val > best_val:
            best_val = val
            best_idx = i

    return best_idx


def compute_scene_reward(
    placements: Sequence[Dict[str, object]],
) -> float:
    """Scene-level quality reward in [0, 1] combining diversity and score.

    Used for optional reward-weighted CE during training.
    """
    if not placements:
        return 0.0

    # Diversity component: unique assets / total
    asset_ids = [str(p.get("asset_id", "")) for p in placements]
    unique = len(set(asset_ids))
    diversity = float(unique) / float(len(asset_ids))

    # Score component: mean CLIP score of non-fallback placements
    scores = [float(p.get("score", 0.0)) for p in placements if float(p.get("score", 0.0)) > 0.0]
    mean_score = (sum(scores) / len(scores)) if scores else 0.0

    # Composite (both in roughly [0, 1])
    return 0.6 * diversity + 0.4 * min(mean_score, 1.0)
