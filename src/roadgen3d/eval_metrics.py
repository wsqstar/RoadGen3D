"""Engineering evaluation metrics for RoadGen3D scene composition (M4)."""

from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Tuple


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
    }
    # M5 compliance keys (optional)
    for k in ("compliance_rate_total", "avg_feasibility_score", "avg_constraint_penalty"):
        if k in rule_summary or k in learned_summary:
            keys.add(k)
    delta: Dict[str, float] = {}
    for key in sorted(keys):
        delta[f"delta_{key}"] = float(learned_summary.get(key, 0.0) - rule_summary.get(key, 0.0))
    return delta
