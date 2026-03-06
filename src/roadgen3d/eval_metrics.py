"""Engineering evaluation metrics for RoadGen3D scene composition (M4)."""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Sequence, Tuple


def aabb_intersects(a: Sequence[float], b: Sequence[float]) -> bool:
    return not (a[1] <= b[0] or b[1] <= a[0] or a[3] <= b[2] or b[3] <= a[2])


def compute_overlap_rate(bboxes: Sequence[Sequence[float]]) -> float:
    """Pair-wise intersection ratio over all bbox pairs."""
    n = len(bboxes)
    if n <= 1:
        return 0.0
    pairs = 0
    overlaps = 0
    for i in range(n):
        for j in range(i + 1, n):
            pairs += 1
            if aabb_intersects(bboxes[i], bboxes[j]):
                overlaps += 1
    return float(overlaps / pairs) if pairs > 0 else 0.0


def compute_dropped_slot_rate(instance_count: int, dropped_slots: int) -> float:
    total = int(instance_count) + int(dropped_slots)
    if total <= 0:
        return 0.0
    return float(dropped_slots / total)


def compute_latency_ms_per_instance(latency_ms_total: float, instance_count: int) -> float:
    if int(instance_count) <= 0:
        return 0.0
    return float(latency_ms_total / max(int(instance_count), 1))


def evaluate_topk_category_hits(predictions: List[Dict[str, object]], topk: int = 3) -> float:
    """Top-k category hit metric aligned with m2_12 evaluation definition."""
    if topk <= 0:
        raise ValueError("topk must be >= 1")
    if not predictions:
        return 0.0

    success = 0
    for item in predictions:
        target = str(item.get("target_category", "")).strip().lower()
        hits = item.get("hits", []) or []
        top_hits = hits[:topk]
        matched = any(str(hit.get("category", "")).strip().lower() == target for hit in top_hits)
        if matched:
            success += 1
    return float(success / len(predictions))


# ---------------------------------------------------------------------------
# Policy-sensitive metrics (M4 fix)
# ---------------------------------------------------------------------------

_BOTH_SIDE_CATEGORIES = {"bench", "lamp", "trash", "tree", "bollard"}


def compute_spacing_uniformity(placements: Sequence[Dict[str, object]]) -> float:
    """Per-category spacing uniformity along the x axis. 1.0 = perfectly even."""
    from collections import defaultdict

    by_cat: Dict[str, List[float]] = defaultdict(list)
    for p in placements:
        cat = str(p.get("category", ""))
        pos = p.get("position_xyz") or [0.0, 0.0, 0.0]
        by_cat[cat].append(float(pos[0]))

    uniformities: List[float] = []
    for cat, xs in by_cat.items():
        if len(xs) < 2:
            continue
        xs_sorted = sorted(xs)
        gaps = [xs_sorted[i + 1] - xs_sorted[i] for i in range(len(xs_sorted) - 1)]
        mean_gap = sum(gaps) / len(gaps)
        if mean_gap <= 1e-6:
            continue
        std_gap = math.sqrt(sum((g - mean_gap) ** 2 for g in gaps) / len(gaps))
        cv = std_gap / mean_gap  # coefficient of variation
        uniformities.append(max(0.0, 1.0 - cv))

    return float(sum(uniformities) / len(uniformities)) if uniformities else 1.0


def compute_style_consistency(placements: Sequence[Dict[str, object]]) -> float:
    """Mean CLIP score of placed assets (excluding fallback_pool with score 0)."""
    scores = [
        float(p.get("score", 0.0))
        for p in placements
        if float(p.get("score", 0.0)) > 0.0
    ]
    return float(sum(scores) / len(scores)) if scores else 0.0


def compute_balance_score(placements: Sequence[Dict[str, object]]) -> float:
    """Left/right balance for categories that use both sides. 1.0 = balanced."""
    left = 0
    right = 0
    for p in placements:
        cat = str(p.get("category", ""))
        if cat not in _BOTH_SIDE_CATEGORIES:
            continue
        pos = p.get("position_xyz") or [0.0, 0.0, 0.0]
        z = float(pos[2])
        if z > 0.0:
            left += 1
        elif z < 0.0:
            right += 1
    total = left + right
    if total == 0:
        return 1.0
    return 1.0 - abs(left - right) / float(total)


def compute_rule_satisfaction_rate(evaluations: Sequence[object]) -> float:
    """Mean rule score from solver evaluations."""
    if not evaluations:
        return 1.0
    scores: List[float] = []
    for evaluation in evaluations:
        if isinstance(evaluation, (int, float)):
            scores.append(float(evaluation))
            continue
        if isinstance(evaluation, dict):
            scores.append(float(evaluation.get("score", 0.0)))
            continue
        score = getattr(evaluation, "score", 0.0)
        scores.append(float(score))
    return float(sum(scores) / len(scores)) if scores else 1.0


def compute_topology_validity(value: float) -> float:
    """Clamp topology validity into [0, 1]."""
    return float(max(0.0, min(float(value), 1.0)))


def compute_cross_section_feasibility(value: float) -> float:
    """Clamp cross-section feasibility into [0, 1]."""
    return float(max(0.0, min(float(value), 1.0)))


def compute_editability(edits: Sequence[object]) -> float:
    """Share of edits that include an explanation."""
    if not edits:
        return 1.0
    explained = 0
    for edit in edits:
        if isinstance(edit, dict):
            reason = str(edit.get("reason", ""))
        else:
            reason = str(getattr(edit, "reason", ""))
        if reason.strip():
            explained += 1
    return float(explained / len(edits))


def compute_explainability(conflicts: Sequence[object]) -> float:
    """Share of conflicts that carry a human-readable explanation."""
    if not conflicts:
        return 1.0
    explained = 0
    for conflict in conflicts:
        if isinstance(conflict, dict):
            message = str(conflict.get("message", ""))
        else:
            message = str(getattr(conflict, "message", ""))
        if message.strip():
            explained += 1
    return float(explained / len(conflicts))


def aggregate_scene_rows(rows: Sequence[Dict[str, object]]) -> Dict[str, float]:
    if not rows:
        return {
            "scene_count": 0.0,
            "instance_count": 0.0,
            "dropped_slots": 0.0,
            "diversity_ratio": 0.0,
            "dropped_slot_rate": 0.0,
            "overlap_rate": 0.0,
            "retrieval_top3_category_hit": 0.0,
            "latency_ms_total": 0.0,
            "latency_ms_per_instance": 0.0,
            "spacing_uniformity": 0.0,
            "style_consistency": 0.0,
            "balance_score": 0.0,
            "rule_satisfaction_rate": 0.0,
            "topology_validity": 0.0,
            "cross_section_feasibility": 0.0,
            "editability": 0.0,
            "conflict_explainability": 0.0,
        }

    def _mean(key: str) -> float:
        values = [float(item.get(key, 0.0)) for item in rows]
        return float(sum(values) / len(values))

    result = {
        "scene_count": float(len(rows)),
        "instance_count": _mean("instance_count"),
        "dropped_slots": _mean("dropped_slots"),
        "diversity_ratio": _mean("diversity_ratio"),
        "dropped_slot_rate": _mean("dropped_slot_rate"),
        "overlap_rate": _mean("overlap_rate"),
        "retrieval_top3_category_hit": _mean("retrieval_top3_category_hit"),
        "latency_ms_total": _mean("latency_ms_total"),
        "latency_ms_per_instance": _mean("latency_ms_per_instance"),
        "spacing_uniformity": _mean("spacing_uniformity"),
        "style_consistency": _mean("style_consistency"),
        "balance_score": _mean("balance_score"),
        "rule_satisfaction_rate": _mean("rule_satisfaction_rate"),
        "topology_validity": _mean("topology_validity"),
        "cross_section_feasibility": _mean("cross_section_feasibility"),
        "editability": _mean("editability"),
        "conflict_explainability": _mean("conflict_explainability"),
    }

    # M5 compliance fields (optional – backward safe)
    _m5_keys = ("compliance_rate_total", "avg_feasibility_score", "avg_constraint_penalty")
    for key in _m5_keys:
        if any(key in item for item in rows):
            result[key] = _mean(key)

    return result


def compare_mode_reports(rule_summary: Dict[str, float], learned_summary: Dict[str, float]) -> Dict[str, float]:
    keys = {
        "instance_count",
        "diversity_ratio",
        "dropped_slot_rate",
        "overlap_rate",
        "retrieval_top3_category_hit",
        "latency_ms_total",
        "latency_ms_per_instance",
        "spacing_uniformity",
        "style_consistency",
        "balance_score",
        "rule_satisfaction_rate",
        "topology_validity",
        "cross_section_feasibility",
        "editability",
        "conflict_explainability",
    }
    # M5 compliance keys (optional)
    for k in ("compliance_rate_total", "avg_feasibility_score", "avg_constraint_penalty"):
        if k in rule_summary or k in learned_summary:
            keys.add(k)
    delta: Dict[str, float] = {}
    for key in sorted(keys):
        delta[f"delta_{key}"] = float(learned_summary.get(key, 0.0) - rule_summary.get(key, 0.0))
    return delta
