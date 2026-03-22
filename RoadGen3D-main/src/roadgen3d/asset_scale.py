"""Canonical street-asset scaling helpers."""

from __future__ import annotations

from typing import Any, Dict, Mapping

VALID_ASSET_SCALE_MODES = {"canonical_v1", "native_raw"}

_CANONICAL_PRIORS: Dict[str, Dict[str, Any]] = {
    "tree": {
        "primary_fit": "height_m",
        "target": {"height_m": 7.0, "canopy_width_m": 4.5},
        "secondary_fit": "canopy_width_m",
        "scale_range": (0.35, 8.0),
    },
    "bench": {
        "primary_fit": "width_m",
        "target": {"width_m": 1.8, "height_m": 0.8},
        "secondary_fit": "height_m",
        "scale_range": (0.35, 6.0),
    },
    "lamp": {
        "primary_fit": "height_m",
        "target": {"height_m": 6.0},
        "secondary_fit": "",
        "scale_range": (0.35, 6.0),
    },
    "trash": {
        "primary_fit": "height_m",
        "target": {"height_m": 1.1, "width_m": 0.55},
        "secondary_fit": "width_m",
        "scale_range": (0.35, 6.0),
    },
    "mailbox": {
        "primary_fit": "height_m",
        "target": {"height_m": 1.15, "width_m": 0.45},
        "secondary_fit": "width_m",
        "scale_range": (0.35, 6.0),
    },
    "hydrant": {
        "primary_fit": "height_m",
        "target": {"height_m": 0.75},
        "secondary_fit": "",
        "scale_range": (0.35, 6.0),
    },
    "bollard": {
        "primary_fit": "height_m",
        "target": {"height_m": 0.95},
        "secondary_fit": "",
        "scale_range": (0.35, 6.0),
    },
    "bus_stop": {
        "primary_fit": "width_m",
        "target": {"width_m": 3.2, "height_m": 2.6},
        "secondary_fit": "height_m",
        "scale_range": (0.35, 6.0),
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
            "canonical_target": dict(prior.get("target", {})),
            "scale_fallback_used": False,
            "asset_scale_mode": normalized_mode,
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
            "canonical_target": targets,
            "scale_fallback_used": True,
            "asset_scale_mode": normalized_mode,
        }

    applied_scale = float(primary_target / primary_native)
    secondary_target = float(targets.get(secondary_fit, 0.0) or 0.0)
    secondary_native = float(native_size.get(secondary_fit, 0.0) or 0.0)
    if secondary_fit and secondary_target > 1e-6 and secondary_native > 1e-6:
        applied_scale = min(applied_scale, float(secondary_target / secondary_native))
    applied_scale = max(float(min_scale), min(float(max_scale), float(applied_scale)))
    return {
        "applied_scale": float(applied_scale),
        "native_size_m": native_size,
        "canonical_target": targets,
        "scale_fallback_used": False,
        "asset_scale_mode": normalized_mode,
    }


def summarize_asset_scales(placements: list[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, list[float]] = {}
    fallback_counts: Dict[str, int] = {}
    for placement in placements:
        category = str(placement.get("category", "") or "").strip().lower()
        if category not in _CANONICAL_PRIORS:
            continue
        scale_raw = placement.get("scale", 1.0)
        if isinstance(scale_raw, (list, tuple)):
            scale_value = float(scale_raw[0]) if scale_raw else 1.0
        else:
            scale_value = float(scale_raw or 1.0)
        grouped.setdefault(category, []).append(scale_value)
        if bool(placement.get("scale_fallback_used", False)):
            fallback_counts[category] = fallback_counts.get(category, 0) + 1

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
        }
    return summary
