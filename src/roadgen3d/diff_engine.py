"""Diff engine for comparing two scene layouts or generation pipelines.

Provides structured diff for:
  - configuration parameters
  - summary metrics
  - placement changes (additions, deletions, spatial shifts)
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Sequence, Tuple


def _is_numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except Exception:
        return default


def compute_config_diff(
    old_config: Mapping[str, Any],
    new_config: Mapping[str, Any],
) -> Dict[str, Any]:
    """Compute a field-level diff between two config objects.

    Returns a dict with keys:
      - "added":   fields present in *new* but not *old*
      - "removed": fields present in *old* but not *new*
      - "changed": fields present in both but with different values,
                   stored as ``{"old": ..., "new": ...}``
    """
    diff: Dict[str, Any] = {"added": {}, "removed": {}, "changed": {}}
    all_keys = set(old_config) | set(new_config)
    for key in sorted(all_keys):
        in_old = key in old_config
        in_new = key in new_config
        if in_old and not in_new:
            diff["removed"][key] = old_config[key]
        elif in_new and not in_old:
            diff["added"][key] = new_config[key]
        elif old_config[key] != new_config[key]:
            diff["changed"][key] = {"old": old_config[key], "new": new_config[key]}
    return diff


def compute_metrics_diff(
    old_summary: Mapping[str, Any],
    new_summary: Mapping[str, Any],
) -> Dict[str, Any]:
    """Compare numeric metrics in two summaries.

    Returns a list of metric entries:
      { key, old, new, delta, delta_pct }
    Only keys where at least one side is numeric are included.
    """
    all_keys = set(old_summary) | set(new_summary)
    results: List[Dict[str, Any]] = []
    for key in sorted(all_keys):
        old_val = old_summary.get(key)
        new_val = new_summary.get(key)
        old_num = _safe_float(old_val) if _is_numeric(old_val) else None
        new_num = _safe_float(new_val) if _is_numeric(new_val) else None
        if old_num is None and new_num is None:
            continue
        old_f = old_num if old_num is not None else 0.0
        new_f = new_num if new_num is not None else 0.0
        delta = new_f - old_f
        delta_pct = 0.0
        if old_f != 0.0 and math.isfinite(old_f):
            delta_pct = delta / old_f
        elif new_f != 0.0 and math.isfinite(new_f):
            delta_pct = math.copysign(1.0, delta) * float("inf") if delta != 0 else 0.0
        results.append(
            {
                "key": key,
                "old": old_val if old_num is not None else None,
                "new": new_val if new_num is not None else None,
                "delta": round(delta, 6),
                "delta_pct": round(delta_pct, 6) if math.isfinite(delta_pct) else None,
            }
        )
    return {"metrics": results}


def position_xz(placement: Mapping[str, Any]) -> Tuple[float, float]:
    pos = placement.get("position_xyz") or []
    if len(pos) >= 2:
        return float(pos[0]), float(pos[2])
    return 0.0, 0.0


def match_placements_greedy(
    a_placements: Sequence[Mapping[str, Any]],
    b_placements: Sequence[Mapping[str, Any]],
) -> Tuple[List[Tuple[int, int]], List[int], List[int]]:
    """Greedy nearest-neighbour matching in XZ plane.

    Returns (matched_pairs, a_unmatched_indices, b_unmatched_indices).
    """
    if not a_placements or not b_placements:
        return [], list(range(len(a_placements))), list(range(len(b_placements)))

    a_positions = [position_xz(p) for p in a_placements]
    b_positions = [position_xz(p) for p in b_placements]

    matched: List[Tuple[int, int]] = []
    a_matched: set = set()
    b_matched: set = set()

    # Build all pair distances
    pairs: List[Tuple[float, int, int]] = []
    for i, (ax, az) in enumerate(a_positions):
        for j, (bx, bz) in enumerate(b_positions):
            dist = math.hypot(ax - bx, az - bz)
            pairs.append((dist, i, j))
    pairs.sort(key=lambda x: x[0])

    for dist, i, j in pairs:
        if i in a_matched or j in b_matched:
            continue
        matched.append((i, j))
        a_matched.add(i)
        b_matched.add(j)

    a_unmatched = [i for i in range(len(a_placements)) if i not in a_matched]
    b_unmatched = [j for j in range(len(b_placements)) if j not in b_matched]
    return matched, a_unmatched, b_unmatched


def match_placements_identity_first(
    a_placements: Sequence[Mapping[str, Any]],
    b_placements: Sequence[Mapping[str, Any]],
) -> Tuple[List[Tuple[int, int, str]], List[int], List[int]]:
    """Match unique durable instance IDs first, then legacy ID-less rows by proximity."""

    a_ids: Dict[str, int] = {}
    b_ids: Dict[str, int] = {}
    duplicate_a: set[str] = set()
    duplicate_b: set[str] = set()
    for index, placement in enumerate(a_placements):
        instance_id = str(placement.get("instance_id", "") or "").strip()
        if instance_id in a_ids:
            duplicate_a.add(instance_id)
        elif instance_id:
            a_ids[instance_id] = index
    for index, placement in enumerate(b_placements):
        instance_id = str(placement.get("instance_id", "") or "").strip()
        if instance_id in b_ids:
            duplicate_b.add(instance_id)
        elif instance_id:
            b_ids[instance_id] = index
    durable_ids = sorted((set(a_ids) & set(b_ids)) - duplicate_a - duplicate_b)
    matched: List[Tuple[int, int, str]] = [
        (a_ids[instance_id], b_ids[instance_id], "instance_id")
        for instance_id in durable_ids
    ]
    used_a = {item[0] for item in matched}
    used_b = {item[1] for item in matched}
    if (
        len(a_ids) == len(a_placements)
        and len(b_ids) == len(b_placements)
        and not duplicate_a
        and not duplicate_b
    ):
        return (
            matched,
            [index for index in range(len(a_placements)) if index not in used_a],
            [index for index in range(len(b_placements)) if index not in used_b],
        )
    remaining_a_indices = [index for index in range(len(a_placements)) if index not in used_a]
    remaining_b_indices = [index for index in range(len(b_placements)) if index not in used_b]
    remaining_a = [a_placements[index] for index in remaining_a_indices]
    remaining_b = [b_placements[index] for index in remaining_b_indices]
    legacy_matches, legacy_a_unmatched, legacy_b_unmatched = match_placements_greedy(
        remaining_a,
        remaining_b,
    )
    matched.extend(
        (
            remaining_a_indices[a_index],
            remaining_b_indices[b_index],
            "legacy_proximity",
        )
        for a_index, b_index in legacy_matches
    )
    return (
        matched,
        [remaining_a_indices[index] for index in legacy_a_unmatched],
        [remaining_b_indices[index] for index in legacy_b_unmatched],
    )


def compute_placements_diff(
    a_payload: Mapping[str, Any],
    b_payload: Mapping[str, Any],
) -> Dict[str, Any]:
    """Compare placements between two layouts.

    Returns per-category statistics and lists of added/deleted/moved instances.
    """
    a_placements = list(a_payload.get("placements", []) or [])
    b_placements = list(b_payload.get("placements", []) or [])

    # Group by category
    a_by_cat: Dict[str, List[Mapping[str, Any]]] = {}
    b_by_cat: Dict[str, List[Mapping[str, Any]]] = {}
    for p in a_placements:
        cat = str(p.get("category", "unknown")).strip().lower() or "unknown"
        a_by_cat.setdefault(cat, []).append(p)
    for p in b_placements:
        cat = str(p.get("category", "unknown")).strip().lower() or "unknown"
        b_by_cat.setdefault(cat, []).append(p)

    all_cats = sorted(set(a_by_cat) | set(b_by_cat))

    category_stats: List[Dict[str, Any]] = []
    added_instances: List[Dict[str, Any]] = []
    deleted_instances: List[Dict[str, Any]] = []
    moved_instances: List[Dict[str, Any]] = []

    for cat in all_cats:
        a_list = a_by_cat.get(cat, [])
        b_list = b_by_cat.get(cat, [])
        matched, a_unmatched, b_unmatched = match_placements_identity_first(a_list, b_list)

        shifts: List[float] = []
        for ai, bi, match_method in matched:
            ax, az = position_xz(a_list[ai])
            bx, bz = position_xz(b_list[bi])
            dist = math.hypot(ax - bx, az - bz)
            shifts.append(dist)
            # Moved threshold: 0.3 m
            if dist > 0.3:
                moved_instances.append(
                    {
                        "category": cat,
                        "instance_id": str(a_list[ai].get("instance_id") or b_list[bi].get("instance_id") or ""),
                        "match_method": match_method,
                        "distance_m": round(dist, 4),
                        "a": {"position_xyz": a_list[ai].get("position_xyz")},
                        "b": {"position_xyz": b_list[bi].get("position_xyz")},
                    }
                )

        for ai in a_unmatched:
            deleted_instances.append(
                {
                    "category": cat,
                    "instance_id": str(a_list[ai].get("instance_id") or ""),
                    "position_xyz": a_list[ai].get("position_xyz"),
                }
            )

        for bi in b_unmatched:
            added_instances.append(
                {
                    "category": cat,
                    "instance_id": str(b_list[bi].get("instance_id") or ""),
                    "position_xyz": b_list[bi].get("position_xyz"),
                }
            )

        mean_shift = sum(shifts) / len(shifts) if shifts else 0.0
        category_stats.append(
            {
                "category": cat,
                "count_a": len(a_list),
                "count_b": len(b_list),
                "delta": len(b_list) - len(a_list),
                "matched": len(matched),
                "added": len(b_unmatched),
                "deleted": len(a_unmatched),
                "moved": sum(1 for s in shifts if s > 0.3),
                "mean_position_shift_m": round(mean_shift, 4),
            }
        )

    total_a = sum(s["count_a"] for s in category_stats)
    total_b = sum(s["count_b"] for s in category_stats)

    return {
        "total_count_a": total_a,
        "total_count_b": total_b,
        "total_delta": total_b - total_a,
        "category_stats": category_stats,
        "added_instances": added_instances,
        "deleted_instances": deleted_instances,
        "moved_instances": moved_instances,
    }


def compute_scene_diff(
    layout_a: Mapping[str, Any],
    layout_b: Mapping[str, Any],
) -> Dict[str, Any]:
    """High-level entry point: compute all diffs between two layout payloads."""
    summary_a = dict(layout_a.get("summary", {}) or {})
    summary_b = dict(layout_b.get("summary", {}) or {})
    config_a = dict(layout_a.get("config", {}) or {})
    config_b = dict(layout_b.get("config", {}) or {})

    return {
        "config_diff": compute_config_diff(config_a, config_b),
        "metrics_diff": compute_metrics_diff(summary_a, summary_b),
        "placements_diff": compute_placements_diff(layout_a, layout_b),
    }
