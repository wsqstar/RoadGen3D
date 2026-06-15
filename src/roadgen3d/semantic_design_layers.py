"""A/B semantic design layer contract for street generation."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence

SEMANTIC_DESIGN_LAYERS_SCHEMA_VERSION = "roadgen3d_semantic_design_layers_v1"

SKELETON_DESIGN_PROFILES: tuple[str, ...] = (
    "child_friendly_school",
    "walkable_commercial",
    "vehicle_access_commercial",
    "transit_priority",
    "green_walkable",
    "quiet_residential",
)

STREET_FURNITURE_PROFILES: tuple[str, ...] = (
    "none",
    "balanced_complete",
    "pedestrian_friendly",
    "commercial_vitality",
    "transit_priority",
    "park_landscape",
    "quiet_residential",
)

SEMANTIC_RESOLUTION_ORDER: tuple[str, ...] = (
    "manual",
    "llm",
    "osm",
    "recommended",
    "fallback",
)

_SOURCE_PRIORITY = {
    "manual": 0,
    "human": 0,
    "viewer": 0,
    "reference_annotation": 0,
    "user": 0,
    "llm": 1,
    "osm": 2,
    "osm_poi": 2,
    "poi": 2,
    "recommended": 3,
    "inferred": 3,
    "fallback": 4,
}

_SKELETON_ALIASES = {
    "school": "child_friendly_school",
    "kindergarten": "child_friendly_school",
    "child_friendly": "child_friendly_school",
    "commercial": "walkable_commercial",
    "retail": "walkable_commercial",
    "walkable": "walkable_commercial",
    "vehicle_access": "vehicle_access_commercial",
    "car_access": "vehicle_access_commercial",
    "transit": "transit_priority",
    "bus": "transit_priority",
    "green": "green_walkable",
    "park": "green_walkable",
    "residential": "quiet_residential",
}

_FURNITURE_ALIASES = {
    "no_furniture": "none",
    "furniture_free": "none",
    "structure_only": "none",
    "balanced": "balanced_complete",
    "complete": "balanced_complete",
    "pedestrian": "pedestrian_friendly",
    "walkable": "pedestrian_friendly",
    "commercial": "commercial_vitality",
    "commerce": "commercial_vitality",
    "retail": "commercial_vitality",
    "transit": "transit_priority",
    "bus": "transit_priority",
    "green": "park_landscape",
    "park": "park_landscape",
    "residential": "quiet_residential",
}

SKELETON_TO_STREET_FURNITURE_PROFILE: Dict[str, str] = {
    "child_friendly_school": "pedestrian_friendly",
    "walkable_commercial": "commercial_vitality",
    "vehicle_access_commercial": "balanced_complete",
    "transit_priority": "transit_priority",
    "green_walkable": "park_landscape",
    "quiet_residential": "quiet_residential",
}

STREET_FURNITURE_PROFILE_CONFIG_PATCHES: Dict[str, Dict[str, Any]] = {
    "none": {
        "density": 0.1,
        "amenity_coverage_mode": "off",
        "curated_street_assets_profile": "disabled",
        "max_bus_stops_per_scene": 0,
        "allow_demo_bus_stop_when_osm_absent": False,
        "minimum_category_presence": (),
        "optional_category_presence": (),
    },
    "balanced_complete": {
        "design_rule_profile": "balanced_complete_street_v1",
        "objective_profile": "balanced",
        "style_preset": "civic_clean_v1",
        "density": 0.6,
        "ped_demand_level": "medium",
        "bike_demand_level": "medium",
        "transit_demand_level": "medium",
        "vehicle_demand_level": "medium",
        "minimum_category_presence": ("trash", "bench", "lamp"),
        "optional_category_presence": ("mailbox", "hydrant"),
    },
    "pedestrian_friendly": {
        "design_rule_profile": "pedestrian_priority_v1",
        "objective_profile": "balanced",
        "style_preset": "lush_walkable_v1",
        "density": 0.5,
        "ped_demand_level": "high",
        "bike_demand_level": "medium",
        "transit_demand_level": "medium",
        "vehicle_demand_level": "low",
        "minimum_category_presence": ("lamp", "bench", "trash", "bollard"),
        "optional_category_presence": ("tree", "hydrant"),
    },
    "commercial_vitality": {
        "design_rule_profile": "balanced_complete_street_v1",
        "objective_profile": "commerce",
        "style_preset": "civic_clean_v1",
        "density": 0.9,
        "ped_demand_level": "high",
        "bike_demand_level": "medium",
        "transit_demand_level": "high",
        "vehicle_demand_level": "medium",
        "minimum_category_presence": ("lamp", "bench", "trash"),
        "optional_category_presence": ("tree", "mailbox", "bollard"),
    },
    "transit_priority": {
        "design_rule_profile": "transit_priority_v1",
        "objective_profile": "transit",
        "style_preset": "transit_modern_v1",
        "density": 0.85,
        "ped_demand_level": "high",
        "bike_demand_level": "medium",
        "transit_demand_level": "high",
        "vehicle_demand_level": "high",
        "minimum_category_presence": ("bus_stop", "lamp", "bench", "trash"),
        "optional_category_presence": ("tree",),
        "max_bus_stops_per_scene": 2,
        "allow_demo_bus_stop_when_osm_absent": True,
    },
    "park_landscape": {
        "design_rule_profile": "pedestrian_priority_v1",
        "objective_profile": "greening",
        "style_preset": "lush_walkable_v1",
        "density": 0.25,
        "ped_demand_level": "medium",
        "bike_demand_level": "medium",
        "transit_demand_level": "low",
        "vehicle_demand_level": "low",
        "minimum_category_presence": ("tree", "lamp", "bench", "trash"),
        "optional_category_presence": ("bollard",),
    },
    "quiet_residential": {
        "design_rule_profile": "pedestrian_priority_v1",
        "objective_profile": "greening",
        "style_preset": "lush_walkable_v1",
        "density": 0.35,
        "ped_demand_level": "high",
        "bike_demand_level": "medium",
        "transit_demand_level": "low",
        "vehicle_demand_level": "low",
        "minimum_category_presence": ("lamp", "bench", "trash"),
        "optional_category_presence": ("tree", "hydrant"),
    },
}


@dataclass(frozen=True)
class SemanticLayerCandidate:
    profile: str
    source: str
    confidence: float
    reasons: tuple[str, ...] = ()
    weight: float = 1.0

    def priority(self) -> int:
        return _SOURCE_PRIORITY.get(normalize_source(self.source), 9)

    def to_dict(self, *, final: "SemanticLayerCandidate | None" = None) -> Dict[str, Any]:
        payload = {
            "profile": self.profile,
            "source": normalize_source(self.source),
            "confidence": float(max(0.0, min(self.confidence, 1.0))),
            "reasons": list(self.reasons),
            "weight": float(self.weight),
            "overridden_by": "",
        }
        if final is not None and (self.profile != final.profile or normalize_source(self.source) != normalize_source(final.source)):
            payload["overridden_by"] = normalize_source(final.source)
        return payload


def normalize_source(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"human", "human_annotation", "reference", "reference_plan", "reference_annotation", "annotation", "viewer", "user"}:
        return "manual"
    if text in {"osm_poi", "osm/poi", "poi_auto"}:
        return "osm"
    if text in {"inferred", "auto_recommended", "derived"}:
        return "recommended"
    return text or "fallback"


def _normalize_profile(value: object, *, allowed: Sequence[str], aliases: Mapping[str, str]) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not text:
        return ""
    if text in allowed:
        return text
    return aliases.get(text, "")


def normalize_skeleton_design_profile(value: object) -> str:
    return _normalize_profile(value, allowed=SKELETON_DESIGN_PROFILES, aliases=_SKELETON_ALIASES)


def normalize_street_furniture_profile(value: object) -> str:
    return _normalize_profile(value, allowed=STREET_FURNITURE_PROFILES, aliases=_FURNITURE_ALIASES)


def recommend_street_furniture_profile(skeleton_design_profile: str) -> str:
    profile = normalize_skeleton_design_profile(skeleton_design_profile)
    return SKELETON_TO_STREET_FURNITURE_PROFILE.get(profile, "balanced_complete")


def street_furniture_profile_config_patch(profile: str) -> Dict[str, Any]:
    normalized = normalize_street_furniture_profile(profile) or "balanced_complete"
    return {
        "street_furniture_profile": normalized,
        "furniture_balance_policy": "overall_balanced",
        "street_furniture_distribution_policy": "road_uniform_v1",
        **dict(STREET_FURNITURE_PROFILE_CONFIG_PATCHES.get(normalized, STREET_FURNITURE_PROFILE_CONFIG_PATCHES["balanced_complete"])),
    }


def apply_street_furniture_profile_defaults(patch: Mapping[str, Any] | None) -> Dict[str, Any]:
    result = dict(patch or {})
    requested = normalize_street_furniture_profile(result.get("street_furniture_profile"))
    skeleton = normalize_skeleton_design_profile(result.get("skeleton_design_profile"))
    if not requested and skeleton:
        requested = recommend_street_furniture_profile(skeleton)
        result.setdefault("street_furniture_profile_source", "recommended")
        result.setdefault("street_furniture_profile_reasons", ("recommended_from_skeleton_design_profile",))
    if not requested:
        return result
    defaults = street_furniture_profile_config_patch(requested)
    defaults.update(result)
    defaults["street_furniture_profile"] = requested
    defaults.setdefault("street_furniture_profile_source", "manual")
    return defaults


def _coerce_reasons(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        items = value.replace(";", ",").split(",")
    elif isinstance(value, Iterable):
        items = list(value)
    else:
        items = ()
    return tuple(dict.fromkeys(str(item).strip() for item in items if str(item).strip()))


def _config_candidate(config: Any, *, layer: str) -> SemanticLayerCandidate | None:
    if layer == "skeleton":
        profile = normalize_skeleton_design_profile(getattr(config, "skeleton_design_profile", ""))
        if not profile:
            return None
        source = normalize_source(getattr(config, "skeleton_design_profile_source", "") or "llm")
        confidence = float(getattr(config, "skeleton_design_profile_confidence", 1.0) or 1.0)
        reasons = _coerce_reasons(getattr(config, "skeleton_design_profile_reasons", ()) or ())
        return SemanticLayerCandidate(profile, source, confidence, reasons or ("compose_config_profile",), 1.0)
    profile = normalize_street_furniture_profile(getattr(config, "street_furniture_profile", ""))
    if not profile:
        return None
    source = normalize_source(getattr(config, "street_furniture_profile_source", "") or "manual")
    confidence = float(getattr(config, "street_furniture_profile_confidence", 1.0) or 1.0)
    reasons = _coerce_reasons(getattr(config, "street_furniture_profile_reasons", ()) or ())
    return SemanticLayerCandidate(profile, source, confidence, reasons or ("compose_config_profile",), 1.0)


def _graph_skeleton_candidates(road_segment_graph: Any) -> list[SemanticLayerCandidate]:
    nodes = list(getattr(road_segment_graph, "nodes", ()) or ())
    buckets: Dict[tuple[str, str], Dict[str, Any]] = defaultdict(lambda: {"weight": 0.0, "confidence_sum": 0.0, "reasons": Counter()})
    for node in nodes:
        profile = normalize_skeleton_design_profile(
            getattr(node, "skeleton_design_profile", "") or getattr(node, "semantic_profile_id", "")
        )
        if not profile:
            continue
        source = normalize_source(
            getattr(node, "skeleton_design_profile_source", "")
            or ("osm" if getattr(node, "semantic_profile_id", "") else "manual")
        )
        weight = max(float(getattr(node, "length_m", 0.0) or 0.0), 1.0)
        confidence = float(
            getattr(node, "skeleton_design_profile_confidence", None)
            if getattr(node, "skeleton_design_profile_confidence", None) is not None
            else getattr(node, "semantic_confidence", 0.75)
        )
        bucket = buckets[(profile, source)]
        bucket["weight"] += weight
        bucket["confidence_sum"] += confidence * weight
        for reason in tuple(getattr(node, "skeleton_design_profile_reasons", ()) or getattr(node, "semantic_reasons", ()) or ()):
            reason_text = str(reason).strip()
            if reason_text:
                bucket["reasons"][reason_text] += 1
    candidates: list[SemanticLayerCandidate] = []
    for (profile, source), bucket in buckets.items():
        weight = float(bucket["weight"] or 1.0)
        reasons = tuple(reason for reason, _ in bucket["reasons"].most_common(4))
        candidates.append(
            SemanticLayerCandidate(
                profile=profile,
                source=source,
                confidence=float(bucket["confidence_sum"]) / weight,
                reasons=reasons or ("road_segment_graph_profile",),
                weight=weight,
            )
        )
    return candidates


def _annotation_skeleton_candidates(annotation_records: Iterable[Any] | None) -> list[SemanticLayerCandidate]:
    if annotation_records is None:
        return []
    candidates: list[SemanticLayerCandidate] = []
    for index, record in enumerate(annotation_records):
        if isinstance(record, Mapping):
            get_value = record.get
        else:
            get_value = lambda key, default=None, _record=record: getattr(_record, key, default)
        profile = normalize_skeleton_design_profile(
            get_value("skeleton_design_profile", "") or get_value("semantic_profile_id", "")
        )
        if not profile:
            continue
        source = normalize_source(get_value("skeleton_design_profile_source", "") or "manual")
        confidence = float(get_value("skeleton_design_profile_confidence", 1.0) or 1.0)
        reasons = _coerce_reasons(
            get_value("skeleton_design_profile_reasons", ())
            or get_value("semantic_reasons", ())
            or ("reference_annotation_profile",)
        )
        weight = float(
            get_value("area_m2", 0.0)
            or get_value("length_m", 0.0)
            or 1.0
        )
        candidates.append(
            SemanticLayerCandidate(
                profile=profile,
                source=source,
                confidence=confidence,
                reasons=reasons or (f"annotation_record:{index}",),
                weight=max(weight, 1.0),
            )
        )
    return candidates


def _choose_candidate(candidates: Sequence[SemanticLayerCandidate]) -> SemanticLayerCandidate | None:
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: (item.priority(), -float(item.weight), -float(item.confidence), item.profile),
    )[0]


def resolve_semantic_design_layers(
    *,
    config: Any,
    road_segment_graph: Any | None = None,
    annotation_records: Iterable[Any] | None = None,
) -> Dict[str, Any]:
    skeleton_candidates = [item for item in [_config_candidate(config, layer="skeleton")] if item is not None]
    skeleton_candidates.extend(_annotation_skeleton_candidates(annotation_records))
    if road_segment_graph is not None:
        skeleton_candidates.extend(_graph_skeleton_candidates(road_segment_graph))
    final_skeleton = _choose_candidate(skeleton_candidates)
    if final_skeleton is None:
        final_skeleton = SemanticLayerCandidate("quiet_residential", "fallback", 0.0, ("no_skeleton_profile",), 1.0)
        skeleton_candidates.append(final_skeleton)

    furniture_candidates = [item for item in [_config_candidate(config, layer="furniture")] if item is not None]
    if not furniture_candidates:
        if normalize_source(final_skeleton.source) == "fallback" and "no_skeleton_profile" in set(final_skeleton.reasons):
            recommended = "balanced_complete"
        else:
            recommended = recommend_street_furniture_profile(final_skeleton.profile)
        furniture_candidates.append(
            SemanticLayerCandidate(
                recommended,
                "recommended",
                max(final_skeleton.confidence * 0.85, 0.3),
                (f"recommended_from:{final_skeleton.profile}",),
                final_skeleton.weight,
            )
        )
    final_furniture = _choose_candidate(furniture_candidates)
    if final_furniture is None:
        final_furniture = SemanticLayerCandidate("balanced_complete", "fallback", 0.0, ("no_furniture_profile",), 1.0)
        furniture_candidates.append(final_furniture)

    profile_pair = f"{final_skeleton.profile}+{final_furniture.profile}"
    return {
        "schema_version": SEMANTIC_DESIGN_LAYERS_SCHEMA_VERSION,
        "resolution_order": list(SEMANTIC_RESOLUTION_ORDER),
        "profile_pair": profile_pair,
        "skeleton_design_profile": final_skeleton.profile,
        "skeleton_design_profile_source": normalize_source(final_skeleton.source),
        "skeleton_design_profile_confidence": round(max(0.0, min(final_skeleton.confidence, 1.0)), 4),
        "skeleton_design_profile_reasons": list(final_skeleton.reasons),
        "street_furniture_profile": final_furniture.profile,
        "street_furniture_profile_source": normalize_source(final_furniture.source),
        "street_furniture_profile_confidence": round(max(0.0, min(final_furniture.confidence, 1.0)), 4),
        "street_furniture_profile_reasons": list(final_furniture.reasons),
        "layers": {
            "skeleton_design": final_skeleton.to_dict(),
            "street_furniture": final_furniture.to_dict(),
        },
        "candidates": {
            "skeleton_design": [item.to_dict(final=final_skeleton) for item in skeleton_candidates],
            "street_furniture": [item.to_dict(final=final_furniture) for item in furniture_candidates],
        },
    }
