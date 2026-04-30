"""Canonical street-asset scaling helpers."""

from __future__ import annotations

from typing import Any, Dict, Mapping

VALID_ASSET_SCALE_MODES = {"canonical_v1", "native_raw"}

_CANONICAL_PRIORS: Dict[str, Dict[str, Any]] = {
    "tree": {
        "primary_fit": "height_m",
        "target": {"height_m": 7.0, "canopy_width_m": 4.5},
        "secondary_fit": "canopy_width_m",
        "scale_range": (0.02, 20.0),
        "sanity_bounds": {"height_m": (4.0, 12.0), "canopy_width_m": (0.5, 8.0)},
    },
    "bench": {
        "primary_fit": "width_m",
        "target": {"width_m": 1.8, "height_m": 0.8},
        "secondary_fit": "height_m",
        "scale_range": (0.02, 12.0),
        "sanity_bounds": {"width_m": (1.2, 2.4), "height_m": (0.35, 1.3)},
    },
    "lamp": {
        "primary_fit": "height_m",
        "target": {"height_m": 6.0},
        "secondary_fit": "",
        "scale_range": (0.02, 20.0),
        "sanity_bounds": {"height_m": (3.5, 8.0), "width_m": (0.05, 2.0), "depth_m": (0.05, 2.0)},
    },
    "trash": {
        "primary_fit": "height_m",
        "target": {"height_m": 1.1, "width_m": 0.55},
        "secondary_fit": "width_m",
        "scale_range": (0.02, 12.0),
        "sanity_bounds": {"height_m": (0.6, 1.6), "width_m": (0.25, 1.0)},
    },
    "mailbox": {
        "primary_fit": "height_m",
        "target": {"height_m": 1.15, "width_m": 0.45},
        "secondary_fit": "width_m",
        "scale_range": (0.02, 12.0),
        "sanity_bounds": {"height_m": (0.7, 1.6), "width_m": (0.25, 1.0)},
    },
    "hydrant": {
        "primary_fit": "height_m",
        "target": {"height_m": 0.75},
        "secondary_fit": "",
        "scale_range": (0.02, 12.0),
        "sanity_bounds": {"height_m": (0.45, 1.1), "width_m": (0.15, 0.9), "depth_m": (0.15, 0.9)},
    },
    "bollard": {
        "primary_fit": "height_m",
        "target": {"height_m": 0.95},
        "secondary_fit": "",
        "scale_range": (0.02, 12.0),
        "sanity_bounds": {"height_m": (0.55, 1.4), "width_m": (0.08, 0.7), "depth_m": (0.08, 0.7)},
    },
    "bus_stop": {
        "primary_fit": "width_m",
        "target": {"width_m": 3.2, "height_m": 2.6},
        "secondary_fit": "height_m",
        "scale_range": (0.02, 12.0),
        "sanity_bounds": {"width_m": (2.2, 5.0), "height_m": (2.0, 3.5), "depth_m": (0.8, 3.0)},
    },
}


def asset_scale_prior(category: str) -> Dict[str, Any]:
    return dict(_CANONICAL_PRIORS.get(str(category).strip().lower(), {}))


def native_size_payload(*, width_m: float, depth_m: float, height_m: float) -> Dict[str, float]:
    return {
        "width_m": float(max(width_m, 0.0)),
        "depth_m": float(max(depth_m, 0.0)),
        "height_m": float(max(height_m, 0.0)),
        "canopy_width_m": float(max(width_m, depth_m, 0.0)),
    }


def compute_asset_scale(
    *,
    category: str,
    width_m: float,
    depth_m: float,
    height_m: float,
    mode: str,
) -> Dict[str, Any]:
    normalized_mode = str(mode or "canonical_v1").strip().lower()
    native_size = native_size_payload(width_m=width_m, depth_m=depth_m, height_m=height_m)
    prior = asset_scale_prior(category)
    if normalized_mode != "canonical_v1" or not prior:
        return {
            "applied_scale": 1.0,
            "native_size_m": native_size,
            "final_size_m": dict(native_size),
            "canonical_target": dict(prior.get("target", {})),
            "scale_fallback_used": False,
            "asset_scale_mode": normalized_mode,
            "scale_gate_failed": False,
            "scale_gate_blocking": False,
            "scale_gate_reason": "",
        }

    primary_fit = str(prior.get("primary_fit", "") or "")
    secondary_fit = str(prior.get("secondary_fit", "") or "")
    targets = dict(prior.get("target", {}) or {})
    min_scale, max_scale = prior.get("scale_range", (0.35, 6.0))
    primary_native = float(native_size.get(primary_fit, 0.0) or 0.0)
    primary_target = float(targets.get(primary_fit, 0.0) or 0.0)
    if primary_native <= 1e-6 or primary_target <= 1e-6:
        return {
            "applied_scale": 1.0,
            "native_size_m": native_size,
            "final_size_m": dict(native_size),
            "canonical_target": targets,
            "scale_fallback_used": True,
            "asset_scale_mode": normalized_mode,
            "scale_gate_failed": False,
            "scale_gate_blocking": False,
            "scale_gate_reason": "",
        }

    applied_scale = float(primary_target / primary_native)
    secondary_target = float(targets.get(secondary_fit, 0.0) or 0.0)
    secondary_native = float(native_size.get(secondary_fit, 0.0) or 0.0)
    if secondary_fit and secondary_target > 1e-6 and secondary_native > 1e-6:
        secondary_scale = float(secondary_target / secondary_native)
        candidate_scale = min(applied_scale, secondary_scale)
        primary_bounds = dict(prior.get("sanity_bounds", {}) or {}).get(primary_fit)
        if primary_bounds:
            primary_min = float(primary_bounds[0])
            if primary_native * candidate_scale >= primary_min:
                applied_scale = candidate_scale
        else:
            applied_scale = candidate_scale
    applied_scale = max(float(min_scale), min(float(max_scale), float(applied_scale)))
    final_size = {
        key: float(value) * float(applied_scale)
        for key, value in native_size.items()
    }
    scale_gate_failed = False
    scale_gate_blocking = False
    gate_reasons = []
    for key, bounds in dict(prior.get("sanity_bounds", {}) or {}).items():
        if key not in final_size:
            continue
        min_allowed, max_allowed = bounds
        value = float(final_size.get(key, 0.0) or 0.0)
        if value < float(min_allowed) or value > float(max_allowed):
            scale_gate_failed = True
            if key == primary_fit:
                scale_gate_blocking = True
            gate_reasons.append(f"{key}_outside_{float(min_allowed):.2f}_{float(max_allowed):.2f}")
    return {
        "applied_scale": float(applied_scale),
        "native_size_m": native_size,
        "final_size_m": final_size,
        "canonical_target": targets,
        "scale_fallback_used": False,
        "asset_scale_mode": normalized_mode,
        "scale_gate_failed": bool(scale_gate_failed),
        "scale_gate_blocking": bool(scale_gate_blocking),
        "scale_gate_reason": ",".join(gate_reasons),
    }


def summarize_asset_scales(placements: list[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, list[float]] = {}
    fallback_counts: Dict[str, int] = {}
    source_scaled_counts: Dict[str, int] = {}
    metric_source_counts: Dict[str, int] = {}
    source_scale_rejected_counts: Dict[str, int] = {}
    scale_gate_failed_counts: Dict[str, int] = {}
    for placement in placements:
        category = str(placement.get("category", "") or "").strip().lower()
        if not category:
            continue
        scale_raw = placement.get("scale", 1.0)
        if isinstance(scale_raw, (list, tuple)):
            scale_value = float(scale_raw[0]) if scale_raw else 1.0
        else:
            scale_value = float(scale_raw or 1.0)
        grouped.setdefault(category, []).append(scale_value)
        if bool(placement.get("scale_fallback_used", False)):
            fallback_counts[category] = fallback_counts.get(category, 0) + 1
        if abs(float(placement.get("source_scale", 1.0) or 1.0) - 1.0) > 1e-6:
            source_scaled_counts[category] = source_scaled_counts.get(category, 0) + 1
        if str(placement.get("source_scale_source", "") or "").startswith("metric_"):
            metric_source_counts[category] = metric_source_counts.get(category, 0) + 1
        if str(placement.get("source_scale_rejected_reason", "") or "").strip():
            source_scale_rejected_counts[category] = source_scale_rejected_counts.get(category, 0) + 1
        if bool(placement.get("scale_gate_failed", False)):
            scale_gate_failed_counts[category] = scale_gate_failed_counts.get(category, 0) + 1

    summary: Dict[str, Dict[str, Any]] = {}
    for category, values in grouped.items():
        ordered = sorted(float(value) for value in values)
        if not ordered:
            continue
        mid = len(ordered) // 2
        median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0
        summary[category] = {
            "count": int(len(ordered)),
            "median_scale": round(float(median), 3),
            "min_scale": round(float(ordered[0]), 3),
            "max_scale": round(float(ordered[-1]), 3),
            "fallback_count": int(fallback_counts.get(category, 0)),
            "source_scaled_count": int(source_scaled_counts.get(category, 0)),
            "metric_source_count": int(metric_source_counts.get(category, 0)),
            "source_scale_rejected_count": int(source_scale_rejected_counts.get(category, 0)),
            "scale_gate_failed_count": int(scale_gate_failed_counts.get(category, 0)),
        }
    return summary
