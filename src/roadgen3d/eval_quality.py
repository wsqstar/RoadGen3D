"""Human-centric evaluation helpers for walkability, safety, and aesthetics."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Sequence

from .eval_metrics import compute_balance_score, compute_spacing_uniformity

try:
    from .llm.safety_eval import evaluate_safety
    from .llm.beauty_eval import evaluate_beauty
except Exception:
    evaluate_safety = None  # type: ignore[misc,assignment]
    evaluate_beauty = None  # type: ignore[misc,assignment]

AMENITY_CATEGORIES = {"bench", "lamp", "trash", "bus_stop", "mailbox", "hydrant"}
TREE_CANOPY_SIZE_M = (3.6, 3.6)
ANCHOR_POI_WEIGHTS = {
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


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return float(max(lo, min(hi, value)))


def _mean(values: Iterable[float]) -> float:
    items = [float(v) for v in values if v is not None]
    return float(sum(items) / len(items)) if items else 0.0


def _length_from_summary(summary: Mapping[str, Any], default: float = 80.0) -> float:
    value = summary.get("length_m")
    if value is None:
        value = (summary.get("config", {}) or {}).get("length_m", default)
    try:
        return float(value)
    except Exception:
        return float(default)


def _sidewalk_width(summary: Mapping[str, Any], default: float = 2.5) -> float:
    try:
        return float(summary.get("sidewalk_width_m", default))
    except Exception:
        return float(default)


def _road_width(summary: Mapping[str, Any], default: float = 8.0) -> float:
    try:
        return float(summary.get("road_width_m", summary.get("carriageway_width_m", default)))
    except Exception:
        return float(default)


def _placements(layout_payload: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    return list(layout_payload.get("placements", []) or [])


def _spatial_context(summary: Mapping[str, Any]) -> Mapping[str, Any]:
    return summary.get("spatial_context", {}) or {}


@dataclass
class WalkabilityResult:
    indicators: Dict[str, float] = field(default_factory=dict)
    pillar_scores: Dict[str, float] = field(default_factory=dict)
    walkability_index: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    top_contributors: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "indicators": self.indicators,
            "pillar_scores": self.pillar_scores,
            "walkability_index": float(self.walkability_index),
            "metadata": self.metadata,
            "top_contributors": self.top_contributors,
        }


def _spacing_cv(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    xs = sorted(values)
    gaps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
    mean_gap = _mean(gaps)
    if mean_gap <= 1e-6:
        return 0.0
    variance = _mean([(gap - mean_gap) ** 2 for gap in gaps])
    return math.sqrt(max(variance, 0.0)) / mean_gap


def _lamp_uniformity(placements: Sequence[Mapping[str, Any]]) -> float:
    lamp_xs = [
        float((placement.get("position_xyz") or [0.0])[0])
        for placement in placements
        if str(placement.get("category", "")).strip().lower() == "lamp"
        and isinstance(placement.get("position_xyz"), (list, tuple))
        and len(placement["position_xyz"]) >= 1
    ]
    if len(lamp_xs) < 2:
        return 1.0
    return _clamp(1.0 - _spacing_cv(lamp_xs))


def _amenity_density(placements: Sequence[Mapping[str, Any]], length_m: float) -> float:
    count = sum(1 for placement in placements if str(placement.get("category", "")).strip().lower() in AMENITY_CATEGORIES)
    density = count / max(length_m, 1e-6)
    return _clamp(density / 0.15)


def _tree_shade_fraction(placements: Sequence[Mapping[str, Any]], sidewalk_width_m: float, length_m: float) -> float:
    tree_count = sum(1 for placement in placements if str(placement.get("category", "")).strip().lower() == "tree")
    canopy_area = tree_count * (TREE_CANOPY_SIZE_M[0] * TREE_CANOPY_SIZE_M[1])
    sidewalk_area = max(2.0 * sidewalk_width_m * length_m, 1e-3)
    return _clamp(canopy_area / sidewalk_area)


def _transit_proximity(summary: Mapping[str, Any]) -> float:
    ctx = _spatial_context(summary)
    bus_points = ctx.get("bus_stop_points_xz") or []
    if not bus_points:
        return 0.0
    length_m = _length_from_summary(summary)
    road_half = _road_width(summary) / 2.0
    sidewalk_width = _sidewalk_width(summary)
    walkway_z = [road_half + sidewalk_width / 2.0, -(road_half + sidewalk_width / 2.0)]
    min_dist = math.inf
    for point in bus_points:
        if len(point) < 2:
            continue
        x, z = float(point[0]), float(point[1])
        for center_z in walkway_z:
            dist = math.hypot(x, z - center_z)
            min_dist = min(min_dist, dist)
    if not math.isfinite(min_dist):
        return 0.0
    return _clamp(math.exp(-min_dist / 60.0))


def _crossing_provision(summary: Mapping[str, Any], length_m: float) -> float:
    ctx = _spatial_context(summary)
    crossings = len(ctx.get("poi_points_by_type_xz", {}).get("crossing", []) or [])
    target = max(length_m / 80.0, 1e-3)
    return _clamp(crossings / target)


def _entrance_density(summary: Mapping[str, Any], length_m: float) -> float:
    entrances = float(summary.get("entrance_count", 0))
    per_m = entrances / max(length_m, 1e-6)
    return _clamp(per_m / 0.04)


def _poi_mix(summary: Mapping[str, Any]) -> float:
    poi_counts = {}
    land_use_summary = summary.get("land_use_summary") or {}
    if isinstance(land_use_summary, Mapping):
        for key, value in land_use_summary.items():
            try:
                poi_counts[str(key)] = poi_counts.get(str(key), 0.0) + float(value or 0.0)
            except Exception:
                continue
    ctx = _spatial_context(summary)
    for poi_type, points in (ctx.get("poi_points_by_type_xz") or {}).items():
        poi_counts[poi_type] = poi_counts.get(poi_type, 0.0) + len(points or [])
    values = [max(float(v), 0.0) for v in poi_counts.values() if v]
    total = sum(values)
    if total <= 0:
        return 0.0
    entropy = -sum((v / total) * math.log(max(v / total, 1e-9)) for v in values)
    max_entropy = math.log(len(values)) if values else 1.0
    return _clamp(entropy / max(max_entropy, 1e-9))


def _micro_env(summary: Mapping[str, Any], tree_shade: float) -> float:
    noise = float(summary.get("mean_noise_shielding", 0.0) or 0.0)
    openness = float(summary.get("mean_entrance_openness", 1.0) or 1.0)
    return _clamp(0.5 * tree_shade + 0.3 * noise + 0.2 * openness)


def compute_walkability_indicators(layout_payload: Mapping[str, Any]) -> WalkabilityResult:
    summary = dict(layout_payload.get("summary", {}) or {})
    placements = _placements(layout_payload)
    length_m = _length_from_summary(summary)
    sidewalk_width = _sidewalk_width(summary)
    left_clear = float(summary.get("left_clear_path_width_m", sidewalk_width) or sidewalk_width)
    right_clear = float(summary.get("right_clear_path_width_m", sidewalk_width) or sidewalk_width)
    clear_width = _mean([left_clear, right_clear])
    sid_clr = _clamp((clear_width - 1.8) / (3.2 - 1.8))

    clear_area = max(length_m * (left_clear + right_clear), 0.0)
    sidewalk_area = max(length_m * sidewalk_width * 2.0, 1e-3)
    clear_cont = _clamp(clear_area / sidewalk_area)

    furn_d = _amenity_density(placements, length_m)
    light_uni = _lamp_uniformity(placements)
    tree_shade = _tree_shade_fraction(placements, sidewalk_width, length_m)
    buffer_ratio = _clamp(
        (float(summary.get("left_furnishing_width_m", 0.0)) + float(summary.get("right_furnishing_width_m", 0.0)))
        / max(_road_width(summary), 1e-3)
    )
    transit_prox = _transit_proximity(summary)
    cross_prov = _crossing_provision(summary, length_m)
    entr_dens = _entrance_density(summary, length_m)
    poi_mix = _poi_mix(summary)
    micro_env = _micro_env(summary, tree_shade)

    indicators = {
        "SID_CLR": round(sid_clr, 4),
        "CLEAR_CONT": round(clear_cont, 4),
        "FURN_D": round(furn_d, 4),
        "LIGHT_UNI": round(light_uni, 4),
        "TREE_SHADE": round(tree_shade, 4),
        "BUFFER_RATIO": round(buffer_ratio, 4),
        "TRANSIT_PROX": round(transit_prox, 4),
        "CROSS_PROV": round(cross_prov, 4),
        "ENTR_DENS": round(entr_dens, 4),
        "POI_MIX": round(poi_mix, 4),
        "MICRO_ENV": round(micro_env, 4),
    }

    protection = _mean([indicators["LIGHT_UNI"], indicators["BUFFER_RATIO"], indicators["CROSS_PROV"]])
    comfort = _mean([indicators["SID_CLR"], indicators["CLEAR_CONT"], indicators["TREE_SHADE"], indicators["MICRO_ENV"]])
    delight = _mean([indicators["FURN_D"], indicators["TRANSIT_PROX"], indicators["ENTR_DENS"], indicators["POI_MIX"]])
    walkability_index = round(0.4 * protection + 0.35 * comfort + 0.25 * delight, 4)

    pillar_scores = {
        "Protection": round(protection, 4),
        "Comfort": round(comfort, 4),
        "Delight": round(delight, 4),
    }
    pillar_weights = {"Protection": 0.4, "Comfort": 0.35, "Delight": 0.25}
    top_contributors = _compute_top_contributors(indicators, pillar_weights)

    metadata = {
        "length_m": round(length_m, 3),
        "sidewalk_width_m": round(sidewalk_width, 3),
        "left_clear_path_width_m": round(left_clear, 3),
        "right_clear_path_width_m": round(right_clear, 3),
    }
    return WalkabilityResult(
        indicators=indicators,
        pillar_scores=pillar_scores,
        walkability_index=walkability_index,
        metadata=metadata,
        top_contributors=top_contributors,
    )


def write_walkability_report(result: WalkabilityResult, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=True), encoding="utf-8")


def _compute_top_contributors(indicators: Mapping[str, float], pillar_weights: Mapping[str, float]) -> List[Dict[str, Any]]:
    """Return the top-3 indicators whose +0.1 improvement would most increase the walkability index."""
    protection_keys = {"LIGHT_UNI", "BUFFER_RATIO", "CROSS_PROV"}
    comfort_keys = {"SID_CLR", "CLEAR_CONT", "TREE_SHADE", "MICRO_ENV"}
    delight_keys = {"FURN_D", "TRANSIT_PROX", "ENTR_DENS", "POI_MIX"}

    pillar_map = {}
    for k in protection_keys:
        pillar_map[k] = "Protection"
    for k in comfort_keys:
        pillar_map[k] = "Comfort"
    for k in delight_keys:
        pillar_map[k] = "Delight"

    impacts = []
    for key, value in indicators.items():
        pillar = pillar_map.get(key)
        if pillar is None:
            continue
        # Current pillar mean
        keys_in_pillar = [k for k, p in pillar_map.items() if p == pillar]
        current_mean = _mean([indicators.get(k, 0.0) for k in keys_in_pillar])
        # Pillar mean if this indicator increased by 0.1 (clamped)
        new_value = min(value + 0.1, 1.0)
        new_mean = _mean([new_value if k == key else indicators.get(k, 0.0) for k in keys_in_pillar])
        delta_pillar = new_mean - current_mean
        delta_index = pillar_weights.get(pillar, 0.0) * delta_pillar
        impacts.append({"indicator": key, "delta_index": round(delta_index, 6)})

    impacts.sort(key=lambda x: x["delta_index"], reverse=True)
    return impacts[:3]


def _compute_safety_diagnosis(features: Mapping[str, float], llm_scores: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Identify the weakest safety dimension."""
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


def _compute_beauty_diagnosis(features: Mapping[str, float], llm_scores: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Identify the weakest beauty dimension."""
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


def _feature_value(source: Mapping[str, float], key: str, default: float = 0.0) -> float:
    return float(source.get(key, default))


def compute_structured_safety_report(
    layout_payload: Mapping[str, Any],
    walkability: WalkabilityResult | None = None,
    llm_scores: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    summary = dict(layout_payload.get("summary", {}) or {})
    placements = _placements(layout_payload)
    if walkability is None:
        walkability = compute_walkability_indicators(layout_payload)
    indicators = walkability.indicators
    features = {
        "LIGHT_UNI": indicators.get("LIGHT_UNI", 0.0),
        "CROSS_PROV": indicators.get("CROSS_PROV", 0.0),
        "BUFFER_RATIO": indicators.get("BUFFER_RATIO", 0.0),
    }
    length_m = _length_from_summary(summary)
    bollard_count = sum(1 for placement in placements if str(placement.get("category", "")).strip().lower() == "bollard")
    features["BOLLARD_DENSITY"] = round(_clamp((bollard_count / max(length_m, 1e-6)) / 0.15), 4)
    dropped_slot_rate = float(summary.get("dropped_slot_rate", 0.0) or 0.0)
    mean_openness = float(summary.get("mean_entrance_openness", 1.0) or 1.0)
    features["VISIBILITY_PENALTY"] = round(_clamp((1.0 - mean_openness) * dropped_slot_rate), 4)

    structural = (
        0.15 * features["CROSS_PROV"]
        + 0.15 * features["LIGHT_UNI"]
        + 0.10 * features["BUFFER_RATIO"]
        + 0.1 * features["BOLLARD_DENSITY"]
        + max(0.0, 0.1 - features["VISIBILITY_PENALTY"])
    )
    structural = round(_clamp(structural, 0.0, 1.0), 4)

    final_score = structural
    needs_review = False
    if llm_scores:
        llm_mean = _mean([llm_scores.get(k, 0.0) for k in ("lighting", "visibility", "protection", "activation")])
        final_score = round(
            _clamp(0.6 * llm_mean + 0.15 * features["CROSS_PROV"] + 0.15 * features["LIGHT_UNI"] + 0.10 * features["BUFFER_RATIO"]),
            4,
        )
        # Flag if LLM sub-scores are highly inconsistent
        llm_values = [float(llm_scores.get(k, 0.0)) for k in ("lighting", "visibility", "protection", "activation")]
        if len(llm_values) > 1:
            mean_val = _mean(llm_values)
            variance = _mean([(v - mean_val) ** 2 for v in llm_values])
            stddev = math.sqrt(max(variance, 0.0))
            if stddev > 0.20:  # threshold on 0-1 scale; 0.20 ~= 1.0 on 0-5 scale
                needs_review = True

    diagnosis = _compute_safety_diagnosis(features, llm_scores)
    report = {
        "features": features,
        "structural_score": structural,
        "llm_scores": dict(llm_scores) if llm_scores else None,
        "final_score": final_score,
        "llm_required": True,
        "needs_review": needs_review,
        "diagnosis": diagnosis,
    }
    return report


def _door_based_active_front_ratio(summary: Mapping[str, Any]) -> float:
    door_count = float(summary.get("door_count", summary.get("entrance_count", 0)) or 0.0)
    length_m = _length_from_summary(summary)
    estimated_active_length = door_count * 4.0  # assume each active frontage spans ~4m
    total_frontage = max(length_m * 2.0, 1e-3)
    return _clamp(estimated_active_length / total_frontage / 0.7)


def _anchor_poi_score(summary: Mapping[str, Any], length_m: float) -> float:
    ctx = _spatial_context(summary)
    poi_points = ctx.get("poi_points_by_type_xz", {}) or {}
    weighted = 0.0
    for poi_type, points in poi_points.items():
        canonical = str(poi_type).strip().lower()
        weight = ANCHOR_POI_WEIGHTS.get(canonical)
        if weight is None:
            # approximate using prefixes
            for key, value in ANCHOR_POI_WEIGHTS.items():
                if canonical.startswith(key):
                    weight = value
                    break
        if weight is None:
            continue
        weighted += weight * len(points or [])
    density = weighted / max(length_m, 1e-6)
    return _clamp(density / 0.12)


def compute_structured_beauty_report(
    layout_payload: Mapping[str, Any],
    llm_scores: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    summary = dict(layout_payload.get("summary", {}) or {})
    presentation = summary.get("composition_report", {}) or {}
    style_coherence = float(presentation.get("style_coherence", summary.get("style_coherence", 0.0)) or 0.0)
    visual_clutter = float(presentation.get("visual_clutter", 0.0) or 0.0)
    spacing_rhythm = float(presentation.get("spacing_rhythm", summary.get("spacing_uniformity", 0.0)) or 0.0)
    focal_readability = float(presentation.get("focal_readability", 0.0) or 0.0)
    presentation_score = float(presentation.get("presentation_score", summary.get("presentation_score", 0.0)) or 0.0)

    length_m = _length_from_summary(summary)
    active_front_ratio = _door_based_active_front_ratio(summary)
    anchor_poi = _anchor_poi_score(summary, length_m)

    structural = round(
        _clamp(0.4 * presentation_score + 0.1 * active_front_ratio + 0.1 * anchor_poi + 0.1 * (1.0 - visual_clutter)),
        4,
    )

    features = {
        "style_coherence": _clamp(style_coherence),
        "visual_clutter": _clamp(visual_clutter),
        "spacing_rhythm": _clamp(spacing_rhythm),
        "focal_readability": _clamp(focal_readability),
        "presentation_score": _clamp(presentation_score),
        "active_front_ratio": active_front_ratio,
        "anchor_poi_score": anchor_poi,
    }

    final_score = structural
    needs_review = False
    if llm_scores:
        llm_mean = _mean([llm_scores.get(k, 0.0) for k in ("coherence", "human_scale", "material_contrast", "visual_interest")])
        final_score = round(
            _clamp(
                0.4 * llm_mean
                + 0.4 * presentation_score
                + 0.1 * active_front_ratio
                + 0.1 * anchor_poi
            ),
            4,
        )
        llm_values = [float(llm_scores.get(k, 0.0)) for k in ("coherence", "human_scale", "material_contrast", "visual_interest")]
        if len(llm_values) > 1:
            mean_val = _mean(llm_values)
            variance = _mean([(v - mean_val) ** 2 for v in llm_values])
            stddev = math.sqrt(max(variance, 0.0))
            if stddev > 0.20:
                needs_review = True

    diagnosis = _compute_beauty_diagnosis(features, llm_scores)
    report = {
        "features": features,
        "structural_score": structural,
        "llm_scores": dict(llm_scores) if llm_scores else None,
        "final_score": final_score,
        "llm_required": True,
        "needs_review": needs_review,
        "diagnosis": diagnosis,
    }
    return report


def write_json_report(data: Mapping[str, Any], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")
