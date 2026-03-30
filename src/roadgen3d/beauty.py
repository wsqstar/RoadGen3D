"""Presentation-oriented style presets, curation, composition, and views."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .eval_metrics import compute_balance_score, compute_spacing_uniformity, compute_style_consistency
from .placement_field import pair_interaction_scores, poi_attraction_score
from .poi_taxonomy import asset_category_for_poi, canonicalize_poi_type, nonempty_poi_points, poi_plot_config
from .street_priors import DEFAULT_CATEGORIES, DEFAULT_SPACING_M
from .types import LayoutSlotPlan, StreetComposeConfig, StreetPlacement, StreetProgram


@dataclass(frozen=True)
class StylePresetSpec:
    name: str
    display_name: str
    category_multipliers: Dict[str, float]
    category_min_counts: Dict[str, int]
    category_max_counts: Dict[str, int]
    hero_categories: Tuple[str, ...]
    category_priority: Tuple[str, ...]
    global_tags: Tuple[str, ...]
    category_tags: Dict[str, Tuple[str, ...]]
    preferred_materials: Tuple[str, ...]
    category_materials: Dict[str, Tuple[str, ...]]
    local_density_limit: int
    scene_colors: Dict[str, Tuple[int, int, int, int]]
    surface_roughness: Dict[str, float] = field(default_factory=lambda: {
        "carriageway": 0.95, "sidewalk": 0.70, "curb": 0.40,
        "context_ground": 0.85, "furnishing": 0.70, "clear_path": 0.65,
        "lane_mark": 0.30, "crossing": 0.35, "transit_pad": 0.50, "tree_pit": 0.90,
    })


STYLE_PRESETS: Dict[str, StylePresetSpec] = {
    "civic_clean_v1": StylePresetSpec(
        name="civic_clean_v1",
        display_name="Civic Clean",
        category_multipliers={
            "bench": 0.85,
            "lamp": 1.05,
            "trash": 0.75,
            "tree": 0.85,
            "bus_stop": 1.0,
            "mailbox": 0.9,
            "hydrant": 1.0,
            "bollard": 0.9,
        },
        category_min_counts={"lamp": 2, "tree": 2, "bench": 1},
        category_max_counts={"bench": 3, "trash": 2, "tree": 4, "bollard": 10},
        hero_categories=("bus_stop", "bench", "tree"),
        category_priority=("bus_stop", "tree", "lamp", "bench", "bollard", "trash", "mailbox", "hydrant"),
        global_tags=("civic", "clean", "minimal", "formal"),
        category_tags={
            "bench": ("clean", "formal", "civic"),
            "lamp": ("clean", "minimal", "metal"),
            "tree": ("formal", "canopy", "civic"),
            "bollard": ("clean", "metal"),
        },
        preferred_materials=("stone", "concrete", "metal", "wood_metal"),
        category_materials={
            "bench": ("wood_metal", "stone"),
            "lamp": ("metal",),
            "tree": ("foliage",),
        },
        local_density_limit=3,
        scene_colors={
            "context_ground": (174, 169, 156, 255),
            "carriageway": (71, 76, 84, 255),
            "sidewalk": (195, 194, 186, 255),
            "furnishing": (176, 174, 164, 255),
            "clear_path": (212, 210, 200, 255),
            "lane_mark": (242, 238, 221, 255),
            "curb": (145, 145, 145, 255),
            "transit_pad": (118, 129, 145, 255),
            "crossing": (236, 228, 208, 255),
            "tree_pit": (98, 93, 76, 255),
        },
        surface_roughness={
            "carriageway": 0.95, "sidewalk": 0.65, "curb": 0.40,
            "context_ground": 0.85, "furnishing": 0.65, "clear_path": 0.60,
            "lane_mark": 0.30, "crossing": 0.35, "transit_pad": 0.50, "tree_pit": 0.90,
        },
    ),
    "transit_modern_v1": StylePresetSpec(
        name="transit_modern_v1",
        display_name="Transit Modern",
        category_multipliers={
            "bench": 0.7,
            "lamp": 1.15,
            "trash": 0.8,
            "tree": 0.7,
            "bus_stop": 1.2,
            "mailbox": 0.7,
            "hydrant": 1.0,
            "bollard": 1.0,
        },
        category_min_counts={"lamp": 2, "bus_stop": 1, "bollard": 4},
        category_max_counts={"bench": 2, "tree": 3, "trash": 2, "bollard": 12},
        hero_categories=("bus_stop", "lamp", "bollard"),
        category_priority=("bus_stop", "lamp", "bollard", "bench", "trash", "tree", "mailbox", "hydrant"),
        global_tags=("transit", "modern", "sleek", "metal"),
        category_tags={
            "bus_stop": ("transit", "modern", "metal"),
            "lamp": ("modern", "metal"),
            "bollard": ("modern", "metal"),
            "bench": ("modern", "clean"),
        },
        preferred_materials=("metal", "glass", "concrete"),
        category_materials={
            "bus_stop": ("metal", "glass"),
            "lamp": ("metal",),
            "bollard": ("metal",),
        },
        local_density_limit=3,
        scene_colors={
            "context_ground": (166, 171, 178, 255),
            "carriageway": (60, 65, 76, 255),
            "sidewalk": (201, 205, 210, 255),
            "furnishing": (169, 175, 182, 255),
            "clear_path": (224, 228, 232, 255),
            "lane_mark": (246, 242, 227, 255),
            "curb": (132, 138, 145, 255),
            "transit_pad": (88, 112, 137, 255),
            "crossing": (233, 236, 240, 255),
            "tree_pit": (92, 96, 84, 255),
        },
        surface_roughness={
            "carriageway": 0.93, "sidewalk": 0.60, "curb": 0.35,
            "context_ground": 0.80, "furnishing": 0.60, "clear_path": 0.55,
            "lane_mark": 0.25, "crossing": 0.30, "transit_pad": 0.45, "tree_pit": 0.85,
        },
    ),
    "lush_walkable_v1": StylePresetSpec(
        name="lush_walkable_v1",
        display_name="Lush Walkable",
        category_multipliers={
            "bench": 1.15,
            "lamp": 0.95,
            "trash": 0.85,
            "tree": 1.3,
            "bus_stop": 0.85,
            "mailbox": 0.8,
            "hydrant": 1.0,
            "bollard": 0.75,
        },
        category_min_counts={"bench": 2, "tree": 3, "lamp": 1},
        category_max_counts={"bench": 4, "trash": 2, "tree": 6, "bollard": 8},
        hero_categories=("tree", "bench", "bus_stop"),
        category_priority=("tree", "bench", "lamp", "bus_stop", "trash", "mailbox", "bollard", "hydrant"),
        global_tags=("lush", "walkable", "green", "warm"),
        category_tags={
            "tree": ("lush", "green", "canopy"),
            "bench": ("walkable", "warm", "wood"),
            "lamp": ("warm", "walkable"),
            "bus_stop": ("walkable", "transit"),
        },
        preferred_materials=("wood", "wood_metal", "foliage", "stone"),
        category_materials={
            "bench": ("wood", "wood_metal"),
            "tree": ("foliage",),
            "lamp": ("metal", "wood_metal"),
        },
        local_density_limit=4,
        scene_colors={
            "context_ground": (160, 152, 126, 255),
            "carriageway": (73, 76, 70, 255),
            "sidewalk": (205, 198, 178, 255),
            "furnishing": (180, 166, 139, 255),
            "clear_path": (223, 215, 195, 255),
            "lane_mark": (239, 231, 204, 255),
            "curb": (143, 138, 124, 255),
            "transit_pad": (122, 132, 110, 255),
            "crossing": (233, 224, 196, 255),
            "tree_pit": (89, 78, 56, 255),
        },
        surface_roughness={
            "carriageway": 0.95, "sidewalk": 0.75, "curb": 0.45,
            "context_ground": 0.90, "furnishing": 0.75, "clear_path": 0.70,
            "lane_mark": 0.35, "crossing": 0.40, "transit_pad": 0.55, "tree_pit": 0.92,
        },
    ),
}

_PARAMETRIC_PRIORITY_CATEGORIES = {"bench", "lamp"}


def asset_generator_type(row: Mapping[str, Any]) -> str:
    """Collapse generator/source metadata into a small provenance vocabulary."""
    generator = str(row.get("generator_type", "") or "").strip().lower()
    source = str(row.get("source", "") or "").strip().lower()
    if generator.startswith("parametric") or source == "parametric_generated":
        return "parametric"
    if source == "procedural_fallback":
        return "procedural_fallback"
    if generator:
        return generator
    if source == "procedural_generated":
        return "legacy"
    if source:
        return source
    return "legacy"


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _mesh_face_count(row: Mapping[str, Any]) -> int:
    count = _safe_int(row.get("mesh_face_count"), -1)
    if count >= 0:
        return count
    metrics = row.get("quality_metrics")
    if isinstance(metrics, Mapping):
        return max(0, _safe_int(metrics.get("face_count"), 0))
    return 0


_BLOCKED_ASSET_IDS = {
    "objaverse_tree_7c97aea203b34df6bb615d0d3567d984",
    "objaverse_tree_352c29c013434d6585e74332699310e2",
    "objaverse_tree_7a689370f9ec46cea2cbc94641c225e6",
    "objaverse_tree_a90b8cca57b44f5492e796cf94d64e80",
    "objaverse_tree_209a0ca9d736401da034fe1d29df010e",
}


def _scene_eligible(row: Mapping[str, Any]) -> bool:
    asset_id = str(row.get("asset_id", "") or "").strip()
    if asset_id in _BLOCKED_ASSET_IDS:
        return False
    if "scene_eligible" in row:
        return _coerce_bool(row.get("scene_eligible"), default=True)
    return _safe_int(row.get("quality_tier"), 0) >= 1


def _is_preview_asset(row: Mapping[str, Any]) -> bool:
    return str(row.get("runtime_profile", "") or "").strip().lower() == "preview"


def _parametric_scene_ready(row: Mapping[str, Any]) -> bool:
    return (
        asset_generator_type(row) == "parametric"
        and _scene_eligible(row)
        and not _is_preview_asset(row)
        and _safe_int(row.get("quality_tier"), 0) >= 2
    )


def _tree_upright_validated(row: Mapping[str, Any]) -> bool:
    notes = row.get("quality_notes")
    if isinstance(notes, str):
        note_values = (notes.strip(),)
    elif notes is None:
        note_values = ()
    else:
        note_values = tuple(str(item).strip() for item in notes if str(item).strip())
    if "tree_upright_validated" in note_values:
        return True
    metrics = row.get("quality_metrics")
    if isinstance(metrics, Mapping):
        validation = metrics.get("tree_upright_validation")
        if isinstance(validation, Mapping):
            return not bool(str(validation.get("failure_reason", "")).strip())
    return False


def _is_external_tree_asset(row: Mapping[str, Any]) -> bool:
    """Check if a tree asset is a validated non-procedural scene tree."""
    if str(row.get("category", "")).strip().lower() != "tree":
        return False
    provenance = asset_generator_type(row)
    if provenance in {"parametric", "legacy", "procedural_fallback"}:
        return False
    source = str(row.get("source", "") or "").strip().lower()
    is_external = source not in {"procedural_generated", "parametric_generated", "procedural_fallback"}
    if is_external and _tree_upright_validated(row):
        return True
    return False


def _filter_candidates_for_curation_mode(
    scored: Sequence[Tuple[Dict[str, Any], float, float]],
    *,
    category: str,
    config: StreetComposeConfig,
) -> Tuple[List[Tuple[Dict[str, Any], float, float]], Dict[str, Any]]:
    mode = str(getattr(config, "asset_curation_mode", "scene_ready_first")).strip().lower()
    parametric_count = sum(1 for row, _score, _curation in scored if asset_generator_type(row) == "parametric")
    legacy_count = int(len(scored) - parametric_count)
    scene_eligible = [item for item in scored if _scene_eligible(item[0])]
    info = {
        "asset_curation_mode": mode,
        "parametric_candidate_count": int(parametric_count),
        "legacy_candidate_count": int(legacy_count),
        "scene_eligible_candidate_count": int(len(scene_eligible)),
        "scene_ineligible_candidate_count": int(len(scored) - len(scene_eligible)),
        "scene_eligibility_filter": "all",
        "provenance_filter": "all",
        "provenance_fallback": False,
    }
    filtered = list(scored)
    if scene_eligible:
        filtered = scene_eligible
        info["scene_eligibility_filter"] = "scene_eligible_only"
    else:
        info["provenance_fallback"] = True

    if category == "tree":
        external_tree_only = [item for item in filtered if _is_external_tree_asset(item[0])]
        if external_tree_only:
            info["provenance_filter"] = "external_tree_only"
            return external_tree_only, info

    if category == "bench" and mode in {"scene_ready_first", "curated_first", "parametric_first"}:
        parametric_only = [item for item in filtered if _parametric_scene_ready(item[0])]
        if parametric_only:
            info["provenance_filter"] = "parametric_only"
            return parametric_only, info

    if mode == "scene_ready_first":
        return filtered, info

    if category not in _PARAMETRIC_PRIORITY_CATEGORIES:
        return filtered, info

    if mode == "parametric_first":
        parametric_only = [item for item in filtered if _parametric_scene_ready(item[0])]
        if parametric_only:
            info["provenance_filter"] = "parametric_only"
            return parametric_only, info
        info["provenance_fallback"] = True
        return filtered, info

    if mode == "legacy":
        legacy_only = [item for item in filtered if asset_generator_type(item[0]) != "parametric"]
        if legacy_only:
            info["provenance_filter"] = "legacy_only"
            return legacy_only, info
        info["provenance_fallback"] = True
        return filtered, info

    return filtered, info

_PRESENTATION_HEIGHTS: Dict[str, float] = {
    "bench": 0.8,
    "lamp": 3.2,
    "trash": 1.0,
    "tree": 4.0,
    "bus_stop": 2.7,
    "mailbox": 1.3,
    "hydrant": 0.9,
    "bollard": 1.1,
}


def infer_style_preset(query: str, target_street_type: str = "") -> str:
    text = f"{query} {target_street_type}".strip().lower()
    if any(token in text for token in ("transit", "bus", "station", "platform")):
        return "transit_modern_v1"
    if any(token in text for token in ("green", "park", "walkable", "pedestrian", "tree", "lush")):
        return "lush_walkable_v1"
    return "civic_clean_v1"


def load_style_preset(name: str | None) -> StylePresetSpec:
    key = str(name or "").strip().lower()
    if not key:
        key = "civic_clean_v1"
    if key == "auto":
        key = "civic_clean_v1"
    if key not in STYLE_PRESETS:
        key = "civic_clean_v1"
    return STYLE_PRESETS[key]


def _normalize_tags(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw = [item.strip().lower() for item in value.split(",")]
    else:
        raw = [str(item).strip().lower() for item in value]
    return tuple(sorted({item for item in raw if item}))


def _default_style_tags(category: str, text_desc: str) -> Tuple[str, ...]:
    tags = {"civic", "clean"}
    text = f"{category} {text_desc}".lower()
    if category in {"lamp", "bus_stop", "bollard"} or any(token in text for token in ("modern", "metal", "transit")):
        tags.update({"transit", "modern", "metal"})
    if category in {"tree", "bench"} or any(token in text for token in ("tree", "wood", "green", "park")):
        tags.update({"walkable", "green", "warm"})
    if category == "tree":
        tags.add("canopy")
    if category == "bench":
        tags.add("wood")
    return tuple(sorted(tags))


def _default_material_family(category: str) -> str:
    return {
        "bench": "wood_metal",
        "lamp": "metal",
        "trash": "metal",
        "tree": "foliage",
        "bus_stop": "metal",
        "mailbox": "metal",
        "hydrant": "metal",
        "bollard": "metal",
    }.get(str(category), "generic")


def _normalize_notes(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def enrich_asset_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    category = str(row.get("category", "")).strip().lower()
    text_desc = str(row.get("text_desc", "")).strip()
    style_tags = _normalize_tags(row.get("style_tags")) or _default_style_tags(category, text_desc)
    quality_tier = int(row.get("quality_tier", 3 if bool(row.get("hero_asset", False)) else 2 if category in {"bench", "lamp", "tree", "bus_stop", "bollard"} else 1))
    mesh_face_count = _mesh_face_count(row)
    if "scene_eligible" in row:
        scene_eligible = _coerce_bool(row.get("scene_eligible"), default=True)
    else:
        scene_eligible = bool(quality_tier >= 1)
    return {
        **dict(row),
        "style_tags": style_tags,
        "quality_tier": max(0, min(int(quality_tier), 3)),
        "material_family": str(row.get("material_family", "")).strip().lower() or _default_material_family(category),
        "hero_asset": bool(row.get("hero_asset", False) or category in {"bus_stop"} and "modern" in style_tags),
        "avoid_with_presets": _normalize_tags(row.get("avoid_with_presets")),
        "scene_eligible": bool(scene_eligible),
        "mesh_face_count": mesh_face_count,
        "quality_notes": _normalize_notes(row.get("quality_notes")),
    }


def _asset_curation_score(row: Mapping[str, Any], category: str, preset: StylePresetSpec) -> float:
    avoid = set(_normalize_tags(row.get("avoid_with_presets")))
    if preset.name in avoid:
        return -10.0
    style_tags = set(_normalize_tags(row.get("style_tags")))
    material = str(row.get("material_family", "")).strip().lower()
    score = 0.0
    score += 0.35 * float(int(row.get("quality_tier", 1)))
    score += 1.25 if _scene_eligible(row) else -2.75
    score += min(float(_mesh_face_count(row)), 1800.0) / 1800.0
    if bool(row.get("hero_asset", False)) and category in preset.hero_categories:
        score += 0.6
    if style_tags & set(preset.global_tags):
        score += 0.8
    if style_tags & set(preset.category_tags.get(category, ())):
        score += 1.2
    if material and material in set(preset.preferred_materials):
        score += 0.6
    if material and material in set(preset.category_materials.get(category, ())):
        score += 0.8
    if asset_generator_type(row) == "parametric" and category in _PARAMETRIC_PRIORITY_CATEGORIES:
        score += 0.25 if _parametric_scene_ready(row) else -0.35 if _is_preview_asset(row) else 0.0
    return float(score)


def curate_candidates(
    candidates: Sequence[Tuple[Dict[str, Any], float]],
    *,
    category: str,
    config: StreetComposeConfig,
) -> Tuple[List[Tuple[Dict[str, Any], float]], Dict[str, Any]]:
    if not candidates:
        return [], {"curated_used": False, "curated_candidate_count": 0}
    preset = load_style_preset(getattr(config, "style_preset", None))
    scored: List[Tuple[Dict[str, Any], float, float]] = []
    for row, base_score in candidates:
        enriched = enrich_asset_row(row)
        curation_score = _asset_curation_score(enriched, category, preset)
        adjusted = float(base_score) + 0.12 * float(curation_score)
        scored.append((enriched, adjusted, curation_score))
    filtered_scored, provenance_info = _filter_candidates_for_curation_mode(
        scored,
        category=category,
        config=config,
    )
    curated = [item for item in filtered_scored if item[2] >= 1.1]
    mode = str(getattr(config, "asset_curation_mode", "scene_ready_first")).strip().lower()
    use_curated = mode in {"curated_first", "parametric_first", "scene_ready_first"} and bool(curated)
    chosen = curated if use_curated else filtered_scored
    chosen.sort(key=lambda item: (float(item[1]), float(item[2]), bool(item[0].get("hero_asset", False))), reverse=True)
    return [(row, float(score)) for row, score, _ in chosen], {
        "curated_used": bool(use_curated),
        "curated_candidate_count": int(len(curated)),
        "candidate_count": int(len(filtered_scored)),
        "candidate_count_raw": int(len(scored)),
        "top_curation_score": float(max(item[2] for item in chosen)),
        **provenance_info,
    }


def shape_program_for_style(program: StreetProgram, config: StreetComposeConfig) -> StreetProgram:
    preset_name = str(getattr(config, "style_preset", "")).strip() or infer_style_preset(config.query, config.target_street_type)
    preset = load_style_preset(preset_name)
    requirements = dict(program.furniture_requirements)
    enforced_min: Dict[str, int] = {}
    for poi_type, count in (program.observed_poi_counts or {}).items():
        category = asset_category_for_poi(poi_type)
        if category:
            enforced_min[category] = enforced_min.get(category, 0) + int(count)

    for category, base_count in list(requirements.items()):
        scaled = int(round(float(base_count) * float(preset.category_multipliers.get(category, 1.0))))
        scaled = max(int(preset.category_min_counts.get(category, 0)), scaled)
        max_cap = preset.category_max_counts.get(category)
        if max_cap is not None:
            length_scale = max(float(config.length_m) / 80.0, 0.75)
            scaled = min(int(round(float(max_cap) * length_scale)), scaled)
        if category in enforced_min:
            scaled = max(int(enforced_min[category]), scaled)
        requirements[category] = max(0, int(scaled))

    goal_list = list(program.design_goals)
    if preset.name == "transit_modern_v1" and "legibility" not in goal_list:
        goal_list.append("legibility")
    if preset.name == "lush_walkable_v1":
        for goal in ("comfort", "greening"):
            if goal not in goal_list:
                goal_list.append(goal)
    if preset.name == "civic_clean_v1" and "clarity" not in goal_list:
        goal_list.append("clarity")
    if (
        int(program.observed_poi_counts.get("bus_stop", 0)) > 0
        or int(program.observed_poi_counts.get("subway_entrance", 0)) > 0
    ) and "transit_access" not in goal_list:
        goal_list.append("transit_access")
    design_goals = tuple(goal_list)
    if design_goals:
        design_goal_weights = {goal: float(1.0 / len(design_goals)) for goal in design_goals}
    else:
        design_goal_weights = {}

    control_points = list(program.control_points)
    if (
        int(program.observed_poi_counts.get("bus_stop", 0)) > 0
        or int(program.observed_poi_counts.get("subway_entrance", 0)) > 0
    ) and "transit_stop" not in control_points:
        control_points.append("transit_stop")

    updated_context = dict(program.context_conditions)
    updated_context["style_preset"] = preset.name
    updated_notes = tuple(list(program.notes) + [f"style_preset={preset.name}", f"beauty_mode={getattr(config, 'beauty_mode', 'presentation_v1')}"])
    return replace(
        program,
        furniture_requirements=requirements,
        control_points=tuple(control_points),
        design_goals=design_goals,
        design_goal_weights=design_goal_weights,
        context_conditions=updated_context,
        notes=updated_notes,
    )


def _poi_points(poi_context: object | None) -> Dict[str, Tuple[Tuple[float, float], ...]]:
    if poi_context is None:
        return {}
    raw = getattr(poi_context, "poi_points_by_type_xz", {}) or {}
    return nonempty_poi_points(raw)


def _attraction_field(category: str, position_xz: Tuple[float, float], poi_context: object | None) -> float:
    return float(
        poi_attraction_score(
            str(category),
            position_xz,
            _poi_points(poi_context),
        )
    )


def _slot_priority(category: str, preset: StylePresetSpec) -> float:
    if category not in preset.category_priority:
        return 0.0
    return float(len(preset.category_priority) - preset.category_priority.index(category)) / float(len(preset.category_priority))


def _slot_composition_score(slot: LayoutSlotPlan, preset: StylePresetSpec, poi_context: object | None) -> float:
    score = _slot_priority(slot.category, preset)
    if bool(slot.required):
        score += 5.0
    if str(slot.anchor_poi_type or "").strip():
        score += 4.0
    score += 2.0 * _attraction_field(slot.category, (float(slot.x_center_m), float(slot.z_center_m)), poi_context)
    if slot.category in preset.hero_categories:
        score += 0.5
    return float(score)


def apply_composition_pass(
    slot_plans: Sequence[LayoutSlotPlan],
    *,
    config: StreetComposeConfig,
    poi_context: object | None = None,
) -> Tuple[Tuple[LayoutSlotPlan, ...], Dict[str, Any]]:
    if str(getattr(config, "beauty_mode", "presentation_v1")).strip().lower() != "presentation_v1":
        return tuple(slot_plans), {"trimmed_optional_slots": 0, "required_slots_preserved": len([slot for slot in slot_plans if slot.required])}
    preset = load_style_preset(getattr(config, "style_preset", None))
    required_slots = [slot for slot in slot_plans if bool(slot.required) or str(slot.anchor_poi_type or "").strip()]
    optional_slots = [slot for slot in slot_plans if slot not in required_slots]
    scored_optional = sorted(optional_slots, key=lambda slot: _slot_composition_score(slot, preset, poi_context), reverse=True)
    kept: List[LayoutSlotPlan] = list(required_slots)
    kept_by_category: Dict[str, int] = {}
    for slot in required_slots:
        kept_by_category[slot.category] = kept_by_category.get(slot.category, 0) + 1

    for slot in scored_optional:
        max_cap = preset.category_max_counts.get(slot.category)
        if max_cap is not None:
            scaled_cap = max(int(preset.category_min_counts.get(slot.category, 0)), int(round(float(max_cap) * max(float(config.length_m) / 80.0, 0.75))))
            if kept_by_category.get(slot.category, 0) >= scaled_cap:
                continue
        nearby = 0
        too_close = False
        same_cat_spacing = max(float(DEFAULT_SPACING_M.get(slot.category, 12.0)) * 0.55, 2.2)
        for existing in kept:
            dist = math.hypot(float(slot.x_center_m) - float(existing.x_center_m), float(slot.z_center_m) - float(existing.z_center_m))
            if dist <= 5.0:
                nearby += 1
            if slot.category == existing.category and dist <= same_cat_spacing:
                too_close = True
                break
        if too_close or nearby > int(preset.local_density_limit):
            continue
        kept.append(slot)
        kept_by_category[slot.category] = kept_by_category.get(slot.category, 0) + 1

    kept.sort(key=lambda slot: (0 if slot.required or slot.anchor_poi_type else 1, -_slot_composition_score(slot, preset, poi_context), float(slot.x_center_m)))
    return tuple(kept), {
        "trimmed_optional_slots": int(len(optional_slots) - sum(1 for slot in kept if slot in optional_slots)),
        "required_slots_preserved": int(len(required_slots)),
        "composition_slot_count": int(len(kept)),
        "composition_optional_count": int(sum(1 for slot in kept if slot in optional_slots)),
        "style_preset": preset.name,
    }


def score_pose_composition(
    *,
    category: str,
    position_xz: Tuple[float, float],
    existing_placements: Sequence[StreetPlacement],
    poi_context: object | None,
    config: StreetComposeConfig,
) -> float:
    preset = load_style_preset(getattr(config, "style_preset", None))
    px, pz = float(position_xz[0]), float(position_xz[1])
    attraction = _attraction_field(category, (px, pz), poi_context)
    clutter_penalty = 0.0
    rhythm_bonus = 0.0
    pairing_bonus = 0.0
    for placement in existing_placements:
        qx = float(placement.position_xyz[0])
        qz = float(placement.position_xyz[2])
        dist = math.hypot(px - qx, pz - qz)
        if dist <= 3.25:
            clutter_penalty += 0.6
        if placement.category == category:
            target_spacing = max(float(DEFAULT_SPACING_M.get(category, 12.0)) * 0.65, 2.0)
            rhythm_bonus += max(0.0, 1.0 - abs(dist - target_spacing) / max(target_spacing, 1.0)) * 0.25
            if dist < target_spacing * 0.35:
                clutter_penalty += 1.4
        pair_attraction, _pair_repulsion = pair_interaction_scores(
            str(category),
            (px, pz),
            str(placement.category),
            (qx, qz),
        )
        pairing_bonus += min(float(pair_attraction), 0.4)
    hero_bonus = 0.25 if category in preset.hero_categories else 0.0
    return float(attraction + rhythm_bonus + pairing_bonus + hero_bonus - clutter_penalty)


def compute_presentation_report(
    placements: Sequence[StreetPlacement],
    *,
    asset_by_id: Mapping[str, Mapping[str, Any]],
    config: StreetComposeConfig,
    poi_context: object | None = None,
    composition_report: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    placement_dicts = [placement.to_dict() for placement in placements]
    spacing_rhythm = float(max(0.0, min(float(compute_spacing_uniformity(placement_dicts)), 1.0)))
    balance = float(max(0.0, min(float(compute_balance_score(placement_dicts)), 1.0)))
    semantic_style = float(max(0.0, min(float(compute_style_consistency(placement_dicts)), 1.0)))
    preset = load_style_preset(getattr(config, "style_preset", None))
    selected_curation_scores: List[float] = []
    local_pairs = 0
    for idx, placement in enumerate(placements):
        row = enrich_asset_row(asset_by_id.get(placement.asset_id, {"category": placement.category, "text_desc": placement.category}))
        selected_curation_scores.append(max(0.0, _asset_curation_score(row, placement.category, preset)))
        for other in placements[idx + 1:]:
            dist = math.hypot(float(placement.position_xyz[0]) - float(other.position_xyz[0]), float(placement.position_xyz[2]) - float(other.position_xyz[2]))
            if dist <= 3.25:
                local_pairs += 1
    curation_norm = min(1.0, (sum(selected_curation_scores) / max(len(selected_curation_scores), 1)) / 2.5)
    style_coherence = float(max(semantic_style, curation_norm))
    pair_count = max(len(placements) * (len(placements) - 1) / 2.0, 1.0)
    visual_clutter = float(local_pairs / pair_count)

    poi_points = _poi_points(poi_context)
    violated = sum(1 for placement in placements if placement.violated_rules)
    key_poi_total = sum(len(points) for poi_type, points in poi_points.items() if canonicalize_poi_type(poi_type) in {"entrance", "bus_stop", "subway_entrance", "crossing"})
    focal_readability = float(max(0.0, 1.0 - (violated / max(len(placements), 1)) * 0.6))
    if key_poi_total > 0:
        focal_readability = max(0.0, min(1.0, focal_readability + 0.1))

    pairing_bonus = 0.0
    for placement in placements:
        pairing_bonus += max(0.0, score_pose_composition(
            category=placement.category,
            position_xz=(float(placement.position_xyz[0]), float(placement.position_xyz[2])),
            existing_placements=[other for other in placements if other.instance_id != placement.instance_id],
            poi_context=poi_context,
            config=config,
        ))
    pairing_bonus = float(min(pairing_bonus / max(len(placements), 1), 1.0))
    presentation_score = float(
        0.28 * style_coherence
        + 0.22 * (1.0 - visual_clutter)
        + 0.2 * spacing_rhythm
        + 0.18 * focal_readability
        + 0.12 * balance
    )
    style_coherence = float(max(0.0, min(style_coherence, 1.0)))
    visual_clutter = float(max(0.0, min(visual_clutter, 1.0)))
    focal_readability = float(max(0.0, min(focal_readability, 1.0)))
    presentation_score = float(max(0.0, min(presentation_score, 1.0)))
    result = {
        "style_preset": preset.name,
        "beauty_mode": str(getattr(config, "beauty_mode", "presentation_v1")),
        "style_coherence": round(style_coherence, 4),
        "visual_clutter": round(visual_clutter, 4),
        "spacing_rhythm": round(spacing_rhythm, 4),
        "focal_readability": round(focal_readability, 4),
        "focal_balance_score": round(balance, 4),
        "pairing_bonus": round(pairing_bonus, 4),
        "presentation_score": round(presentation_score, 4),
    }
    if composition_report:
        result.update({
            "trimmed_optional_slots": int(composition_report.get("trimmed_optional_slots", 0)),
            "required_slots_preserved": int(composition_report.get("required_slots_preserved", 0)),
            "composition_slot_count": int(composition_report.get("composition_slot_count", len(placements))),
        })
    return result


def style_palette(name: str | None) -> Dict[str, Tuple[int, int, int, int]]:
    return dict(load_style_preset(name).scene_colors)


def surface_roughness(name: str | None) -> Dict[str, float]:
    return dict(load_style_preset(name).surface_roughness)


def _require_matplotlib():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    return plt


def _require_pillow():
    try:
        from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps
    except Exception:
        return None
    return Image, ImageChops, ImageEnhance, ImageFilter, ImageOps


def _ensure_homebrew_cairo_path() -> None:
    homebrew_lib = Path("/opt/homebrew/lib")
    if not homebrew_lib.exists():
        return
    existing = str(os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "") or "").strip()
    if existing:
        parts = [part for part in existing.split(":") if part]
        if str(homebrew_lib) not in parts:
            os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = f"{homebrew_lib}:{existing}"
    else:
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = str(homebrew_lib)


def _require_cairosvg():
    try:
        _ensure_homebrew_cairo_path()
        import cairosvg
    except Exception:
        return None
    return cairosvg


@dataclass(frozen=True)
class AxonometricBoardStyle:
    background: str
    board_fill: str
    board_shadow: Tuple[float, float, float, float]
    carriageway_fill: str
    lane_mark_fill: str
    sidewalk_fill: str
    furnishing_fill: str
    activity_fill: str
    green_fill: str
    roof_fill: str
    facade_fill: str
    facade_shadow_fill: str
    outline: str
    accent_edge: str
    window_fill: str
    tree_fill: str
    tree_shadow_fill: str
    tree_trunk_fill: str
    person_fill: str
    vehicle_fill: str
    bus_fill: str
    furniture_fill: str
    outline_lw: float = 1.0
    detail_lw: float = 0.7
    glyph_lw: float = 0.8
    shadow_offset_x: float = 0.75
    shadow_offset_y: float = -0.5
    shadow_alpha: float = 0.18


_AXONOMETRIC_BOARD_STYLE = AxonometricBoardStyle(
    background="#fbfaf7",
    board_fill="#efede8",
    board_shadow=(0.84, 0.82, 0.79, 0.42),
    carriageway_fill="#4c5660",
    lane_mark_fill="#ecefdf",
    sidewalk_fill="#fbefe8",
    furnishing_fill="#f9dfe4",
    activity_fill="#ef9cb0",
    green_fill="#dfeee1",
    roof_fill="#c7dfc2",
    facade_fill="#fbfaf4",
    facade_shadow_fill="#ece6dd",
    outline="#758186",
    accent_edge="#de738f",
    window_fill="#d3e8d7",
    tree_fill="#c9dde1",
    tree_shadow_fill="#b4cdd3",
    tree_trunk_fill="#85756a",
    person_fill="#355468",
    vehicle_fill="#efc455",
    bus_fill="#8ba8d4",
    furniture_fill="#856773",
)

_AXONOMETRIC_SPRITE_FILES: Dict[str, str] = {
    "bench": "bench_axonometric_01.svg",
    "bus_stop": "bus_stop_axonometric_01.svg",
    "lamp": "lamp_axonometric_01.svg",
    "mailbox": "mailbox_axonometric_01.svg",
    "trash": "trash_axonometric_01.svg",
    "tree": "tree_axonometric_01.svg",
}

_AXONOMETRIC_SPRITE_HEIGHT_UNITS: Dict[str, float] = {
    "bench": 2.1,
    "bus_stop": 4.3,
    "lamp": 5.4,
    "mailbox": 2.2,
    "trash": 1.9,
    "tree": 8.0,
}


def _axonometric_sprite_dir() -> Path:
    return (Path(__file__).resolve().parents[2] / "assets" / "axonometric" / "sprites").resolve()


def _axonometric_sprite_path(category: str) -> Optional[Path]:
    sprite_name = _AXONOMETRIC_SPRITE_FILES.get(str(category).strip().lower())
    if not sprite_name:
        return None
    sprite_path = _axonometric_sprite_dir() / sprite_name
    if not sprite_path.exists():
        return None
    return sprite_path


@lru_cache(maxsize=64)
def _load_axonometric_sprite_rgba(category: str) -> Optional[Tuple[Any, float]]:
    cairosvg = _require_cairosvg()
    pillow = _require_pillow()
    sprite_path = _axonometric_sprite_path(category)
    if cairosvg is None or pillow is None or sprite_path is None:
        return None
    Image, *_rest = pillow
    try:
        import io
        import numpy as np

        png_bytes = cairosvg.svg2png(url=str(sprite_path))
        image = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        width_px, height_px = image.size
        if width_px <= 0 or height_px <= 0:
            return None
        return np.asarray(image), float(width_px) / float(height_px)
    except Exception:
        return None


def _draw_oblique_sprite(
    ax: Any,
    u: float,
    v: float,
    *,
    category: str,
    zorder: float,
) -> bool:
    loaded = _load_axonometric_sprite_rgba(str(category).strip().lower())
    if loaded is None:
        return False
    image_rgba, aspect_ratio = loaded
    height_units = float(_AXONOMETRIC_SPRITE_HEIGHT_UNITS.get(str(category).strip().lower(), 1.2))
    width_units = height_units * float(aspect_ratio)
    anchor_x, anchor_y = _project_oblique_point(u, v, 0.0)
    ax.imshow(
        image_rgba,
        extent=(
            anchor_x - width_units / 2.0,
            anchor_x + width_units / 2.0,
            anchor_y,
            anchor_y + height_units,
        ),
        interpolation="bilinear",
        zorder=zorder,
    )
    return True


def _world_surface_polygons(layout_payload: Mapping[str, Any]) -> Dict[str, List[List[Tuple[float, float]]]]:
    summary = dict(layout_payload.get("summary", {}) or {})
    osm_geometry = dict(summary.get("osm_geometry", {}) or {})
    result: Dict[str, List[List[Tuple[float, float]]]] = {
        "carriageway": [],
        "sidewalk": [],
        "left_sidewalk": [],
        "right_sidewalk": [],
    }
    if osm_geometry.get("carriageway_rings"):
        for ring in osm_geometry.get("carriageway_rings", []) or []:
            polygon = [(float(point[0]), float(point[1])) for point in ring if len(point) >= 2]
            if len(polygon) >= 3:
                result["carriageway"].append(polygon)
        for ring in osm_geometry.get("sidewalk_rings", []) or []:
            polygon = [(float(point[0]), float(point[1])) for point in ring if len(point) >= 2]
            if len(polygon) >= 3:
                result["sidewalk"].append(polygon)
        for ring in osm_geometry.get("left_sidewalk_rings", []) or []:
            polygon = [(float(point[0]), float(point[1])) for point in ring if len(point) >= 2]
            if len(polygon) >= 3:
                result["left_sidewalk"].append(polygon)
        for ring in osm_geometry.get("right_sidewalk_rings", []) or []:
            polygon = [(float(point[0]), float(point[1])) for point in ring if len(point) >= 2]
            if len(polygon) >= 3:
                result["right_sidewalk"].append(polygon)
        return result

    bounds = _layout_bounds(layout_payload)
    road_width = float(summary.get("road_width_m", 8.0))
    sidewalk_width = float(summary.get("sidewalk_width_m", 2.5))
    result["carriageway"].append(
        [
            (bounds[0], -road_width / 2.0),
            (bounds[2], -road_width / 2.0),
            (bounds[2], road_width / 2.0),
            (bounds[0], road_width / 2.0),
        ]
    )
    left_polygon = [
        (bounds[0], road_width / 2.0),
        (bounds[2], road_width / 2.0),
        (bounds[2], road_width / 2.0 + sidewalk_width),
        (bounds[0], road_width / 2.0 + sidewalk_width),
    ]
    right_polygon = [
        (bounds[0], -road_width / 2.0 - sidewalk_width),
        (bounds[2], -road_width / 2.0 - sidewalk_width),
        (bounds[2], -road_width / 2.0),
        (bounds[0], -road_width / 2.0),
    ]
    result["sidewalk"].append(left_polygon)
    result["sidewalk"].append(right_polygon)
    result["left_sidewalk"].append(left_polygon)
    result["right_sidewalk"].append(right_polygon)
    return result


def _world_zone_polygons(layout_payload: Mapping[str, Any]) -> Dict[str, List[List[Tuple[float, float]]]]:
    polygons_by_role: Dict[str, List[List[Tuple[float, float]]]] = {}
    for cell in layout_payload.get("zoning_grid", []) or []:
        polygon = [
            (float(point[0]), float(point[1]))
            for point in (cell.get("polygon_xz", []) or [])
            if len(point) >= 2
        ]
        if len(polygon) < 3:
            continue
        lane_role = str(cell.get("lane_role", "") or "")
        land_use_type = str(cell.get("land_use_type", "") or "")
        if lane_role:
            polygons_by_role.setdefault(lane_role, []).append(polygon)
        if land_use_type == "green":
            polygons_by_role.setdefault("green_land_use", []).append(polygon)
    return polygons_by_role


def _scene_origin(layout_payload: Mapping[str, Any]) -> Tuple[float, float]:
    min_x, min_z, max_x, max_z = _layout_bounds(layout_payload)
    return ((min_x + max_x) / 2.0, (min_z + max_z) / 2.0)


def _principal_axis_angle(points: Sequence[Tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    mean_x = sum(float(point[0]) for point in points) / float(len(points))
    mean_z = sum(float(point[1]) for point in points) / float(len(points))
    cov_xx = sum((float(point[0]) - mean_x) ** 2 for point in points)
    cov_zz = sum((float(point[1]) - mean_z) ** 2 for point in points)
    cov_xz = sum((float(point[0]) - mean_x) * (float(point[1]) - mean_z) for point in points)
    if abs(cov_xx - cov_zz) <= 1e-9 and abs(cov_xz) <= 1e-9:
        return 0.0
    return 0.5 * math.atan2(2.0 * cov_xz, cov_xx - cov_zz)


def _street_axis_angle(layout_payload: Mapping[str, Any]) -> float:
    surface_polygons = _world_surface_polygons(layout_payload)
    road_points = [point for polygon in surface_polygons.get("carriageway", []) for point in polygon]
    if len(road_points) >= 2:
        return _principal_axis_angle(road_points)
    building_points = [
        point
        for polygon, _height in _building_polygons_with_height(layout_payload)
        for point in polygon
    ]
    if len(building_points) >= 2:
        return _principal_axis_angle(building_points)
    placement_points = []
    for placement in layout_payload.get("placements", []) or []:
        pos = placement.get("position_xyz", []) or []
        if len(pos) >= 3:
            placement_points.append((float(pos[0]), float(pos[2])))
    if len(placement_points) >= 2:
        return _principal_axis_angle(placement_points)
    return 0.0


def _localize_point(
    point_xz: Tuple[float, float],
    *,
    origin_xz: Tuple[float, float],
    axis_angle_rad: float,
) -> Tuple[float, float]:
    dx = float(point_xz[0]) - float(origin_xz[0])
    dz = float(point_xz[1]) - float(origin_xz[1])
    cos_a = math.cos(-float(axis_angle_rad))
    sin_a = math.sin(-float(axis_angle_rad))
    return (
        dx * cos_a - dz * sin_a,
        dx * sin_a + dz * cos_a,
    )


def _localize_polygon(
    polygon_xz: Sequence[Tuple[float, float]],
    *,
    origin_xz: Tuple[float, float],
    axis_angle_rad: float,
) -> List[Tuple[float, float]]:
    return [
        _localize_point((float(x), float(z)), origin_xz=origin_xz, axis_angle_rad=axis_angle_rad)
        for x, z in polygon_xz
    ]


def _project_plan_point(u: float, v: float, *, angle_rad: float) -> Tuple[float, float]:
    cos_a = math.cos(float(angle_rad))
    sin_a = math.sin(float(angle_rad))
    return (
        float(u) * cos_a - float(v) * sin_a,
        float(u) * sin_a + float(v) * cos_a,
    )


def _project_plan_polygon(
    polygon_uv: Sequence[Tuple[float, float]],
    *,
    angle_rad: float,
) -> List[Tuple[float, float]]:
    return [_project_plan_point(float(u), float(v), angle_rad=angle_rad) for u, v in polygon_uv]


def _project_oblique_point(u: float, v: float, h: float) -> Tuple[float, float]:
    return (
        float(u) - 0.88 * float(v),
        -0.56 * float(u) - 0.24 * float(v) + float(h),
    )


def _project_oblique_polygon(
    polygon_uv: Sequence[Tuple[float, float]],
    *,
    h: float,
) -> List[Tuple[float, float]]:
    return [_project_oblique_point(float(u), float(v), float(h)) for u, v in polygon_uv]


def _bbox_from_points(points: Sequence[Tuple[float, float]]) -> Tuple[float, float, float, float]:
    if not points:
        return (-1.0, -1.0, 1.0, 1.0)
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _bounds_from_polygons(polygons: Sequence[Sequence[Tuple[float, float]]]) -> Tuple[float, float, float, float]:
    points = [point for polygon in polygons for point in polygon]
    return _bbox_from_points(points)


def _local_building_boxes(
    layout_payload: Mapping[str, Any],
    *,
    origin_xz: Tuple[float, float],
    axis_angle_rad: float,
) -> List[Dict[str, float]]:
    boxes: List[Dict[str, float]] = []
    for polygon_xz, target_height_m in _building_polygons_with_height(layout_payload):
        localized = _localize_polygon(polygon_xz, origin_xz=origin_xz, axis_angle_rad=axis_angle_rad)
        if len(localized) < 3:
            continue
        min_u, min_v, max_u, max_v = _bbox_from_points(localized)
        if max_u - min_u < 1e-3 or max_v - min_v < 1e-3:
            continue
        boxes.append(
            {
                "min_u": float(min_u),
                "max_u": float(max_u),
                "min_v": float(min_v),
                "max_v": float(max_v),
                "center_u": float((min_u + max_u) / 2.0),
                "center_v": float((min_v + max_v) / 2.0),
                "height_m": float(target_height_m),
            }
        )
    return boxes


def _local_building_doors(
    layout_payload: Mapping[str, Any],
    *,
    origin_xz: Tuple[float, float],
    axis_angle_rad: float,
) -> List[Dict[str, float]]:
    doors: List[Dict[str, float]] = []
    axis_angle_deg = math.degrees(float(axis_angle_rad))
    for plan in layout_payload.get("building_placements", []) or []:
        if not bool(plan.get("door_added", False)):
            continue
        door_center_world = plan.get("door_center_world_xyz", []) or []
        if len(door_center_world) < 3:
            continue
        door_dims = dict(plan.get("door_dims_m", {}) or {})
        door_width_m = float(door_dims.get("width_m", plan.get("door_width_m", 0.0)) or 0.0)
        door_height_m = float(door_dims.get("height_m", plan.get("door_height_m", 0.0)) or 0.0)
        if door_width_m <= 0.0 or door_height_m <= 0.0:
            continue
        facing = str(plan.get("door_facing", "") or "").strip().lower()
        yaw_local_rad = math.radians(float(plan.get("yaw_deg", 0.0) or 0.0) - axis_angle_deg)
        if facing in {"front", "back"}:
            width_dir = (math.cos(yaw_local_rad), math.sin(yaw_local_rad))
        else:
            width_dir = (-math.sin(yaw_local_rad), math.cos(yaw_local_rad))
        center_u, center_v = _localize_point(
            (float(door_center_world[0]), float(door_center_world[2])),
            origin_xz=origin_xz,
            axis_angle_rad=axis_angle_rad,
        )
        half_width = door_width_m / 2.0
        doors.append(
            {
                "left_u": float(center_u - width_dir[0] * half_width),
                "left_v": float(center_v - width_dir[1] * half_width),
                "right_u": float(center_u + width_dir[0] * half_width),
                "right_v": float(center_v + width_dir[1] * half_width),
                "center_v": float(center_v),
                "height_m": float(door_height_m),
            }
        )
    return doors


def _visible_oblique_building_boxes(
    boxes: Sequence[Mapping[str, float]],
) -> List[Dict[str, float]]:
    normalized = [dict(item) for item in boxes]
    if not normalized:
        return []
    near_side: List[Dict[str, float]] = []
    far_side: List[Dict[str, float]] = []
    for item in normalized:
        center_v = float(item.get("center_v", (float(item.get("min_v", 0.0)) + float(item.get("max_v", 0.0))) / 2.0))
        projected_y = _project_oblique_point(
            float(item.get("center_u", 0.0)),
            center_v,
            0.0,
        )[1]
        enriched = {**item, "projected_ground_y": float(projected_y)}
        if center_v < 0.0:
            far_side.append(enriched)
        else:
            near_side.append(enriched)
    if not near_side or not far_side:
        return normalized
    # In our orthographic oblique projection the near-screen side sits at
    # positive local-v values. We suppress that edge so the road and furniture
    # remain visible, and only keep the far-side building massing.
    return far_side


def _crossing_world_polygons(layout_payload: Mapping[str, Any]) -> List[List[Tuple[float, float]]]:
    summary = dict(layout_payload.get("summary", {}) or {})
    spatial_context = dict(summary.get("spatial_context", {}) or {})
    crossing_points = nonempty_poi_points(spatial_context.get("poi_points_by_type_xz", {}) or {}).get("crossing", ())
    if not crossing_points:
        return []
    road_width = float(summary.get("road_width_m", 8.0))
    sidewalk_width = float(summary.get("sidewalk_width_m", 2.5))
    axis_angle = _street_axis_angle(layout_payload)
    dir_x = math.cos(axis_angle)
    dir_z = math.sin(axis_angle)
    perp_x = -dir_z
    perp_z = dir_x
    half_length = max(1.8, min(3.0, road_width * 0.34))
    half_width = road_width / 2.0 + sidewalk_width * 1.08
    polygons: List[List[Tuple[float, float]]] = []
    for point in crossing_points:
        cx = float(point[0])
        cz = float(point[1])
        polygon = [
            (cx - dir_x * half_length - perp_x * half_width, cz - dir_z * half_length - perp_z * half_width),
            (cx + dir_x * half_length - perp_x * half_width, cz + dir_z * half_length - perp_z * half_width),
            (cx + dir_x * half_length + perp_x * half_width, cz + dir_z * half_length + perp_z * half_width),
            (cx - dir_x * half_length + perp_x * half_width, cz - dir_z * half_length + perp_z * half_width),
        ]
        polygons.append(polygon)
    return polygons


def _centerline_bounds(local_carriageway_polygons: Sequence[Sequence[Tuple[float, float]]]) -> Tuple[float, float]:
    if not local_carriageway_polygons:
        return (-10.0, 10.0)
    min_u, _min_v, max_u, _max_v = _bounds_from_polygons(local_carriageway_polygons)
    return (float(min_u), float(max_u))


def _ambient_vehicle_specs(
    *,
    u_min: float,
    u_max: float,
    road_width_m: float,
    lane_count: int,
    include_bus: bool,
) -> List[Dict[str, float | str]]:
    if u_max - u_min <= 6.0:
        return []
    usable_min = u_min + 0.12 * (u_max - u_min)
    usable_max = u_max - 0.12 * (u_max - u_min)
    samples = [0.18, 0.42, 0.68, 0.84]
    lane_offsets: List[float] = [0.0]
    if int(lane_count) >= 2:
        lane_offsets = [-road_width_m * 0.18, road_width_m * 0.18]
    specs: List[Dict[str, float | str]] = []
    for idx, t in enumerate(samples):
        u = usable_min + (usable_max - usable_min) * t
        lane_offset = lane_offsets[idx % len(lane_offsets)]
        specs.append(
            {
                "u": float(u),
                "v": float(lane_offset),
                "length_m": 3.6 if idx % 3 else 4.2,
                "width_m": 1.7,
                "kind": "car",
            }
        )
    if include_bus:
        specs.append(
            {
                "u": float(usable_min + 0.55 * (usable_max - usable_min)),
                "v": float(-road_width_m * 0.22 if lane_offsets else 0.0),
                "length_m": 8.4,
                "width_m": 2.3,
                "kind": "bus",
            }
        )
    return specs


def _ambient_people_points(
    *,
    poi_points_local: Mapping[str, Sequence[Tuple[float, float]]],
    u_min: float,
    u_max: float,
    road_width_m: float,
    sidewalk_width_m: float,
) -> List[Tuple[float, float]]:
    points: List[Tuple[float, float]] = []
    offsets_by_type: Dict[str, Tuple[Tuple[float, float], ...]] = {
        "entrance": ((-0.55, 0.95), (0.55, 1.15)),
        "bus_stop": ((-0.75, 1.15), (0.0, 1.45), (0.75, 1.05)),
        "crossing": ((-0.45, 0.95), (0.45, -0.95)),
    }
    for poi_type, offsets in offsets_by_type.items():
        for base_u, base_v in poi_points_local.get(poi_type, ()):
            for du, dv in offsets:
                points.append((float(base_u) + float(du), float(base_v) + float(dv)))
    if points:
        return points[:18]
    sidewalk_v = road_width_m / 2.0 + max(0.7, sidewalk_width_m * 0.45)
    fractions = [0.14, 0.29, 0.48, 0.66, 0.82]
    for t in fractions:
        u = float(u_min + (u_max - u_min) * t)
        points.append((u, sidewalk_v))
        points.append((u + 0.4, -sidewalk_v))
    return points[:12]


def _draw_polygon_patch(ax: Any, polygon_xy: Sequence[Tuple[float, float]], *, facecolor: Any, edgecolor: Any = "none", linewidth: float = 0.0, alpha: float = 1.0, zorder: float = 0.0) -> None:
    if len(polygon_xy) < 3:
        return
    from matplotlib.patches import Polygon as MplPolygon

    ax.add_patch(
        MplPolygon(
            polygon_xy,
            closed=True,
            facecolor=facecolor,
            edgecolor=edgecolor,
            linewidth=linewidth,
            alpha=alpha,
            joinstyle="round",
            zorder=zorder,
        )
    )


def _draw_plan_tree(ax: Any, x: float, y: float, *, style: AxonometricBoardStyle) -> None:
    from matplotlib.patches import Circle

    ax.add_patch(Circle((x + 0.24, y - 0.18), radius=0.62, facecolor=style.tree_shadow_fill, edgecolor="none", alpha=0.38, zorder=7.2))
    ax.add_patch(Circle((x, y + 0.02), radius=0.48, facecolor=style.tree_fill, edgecolor=style.outline, linewidth=style.detail_lw, zorder=7.5))
    ax.add_patch(Circle((x, y - 0.38), radius=0.09, facecolor=style.tree_trunk_fill, edgecolor="none", alpha=0.95, zorder=7.6))


def _draw_plan_person(ax: Any, x: float, y: float, *, style: AxonometricBoardStyle) -> None:
    from matplotlib.patches import Circle

    ax.plot([x, x], [y - 0.2, y + 0.14], color=style.person_fill, linewidth=style.glyph_lw, solid_capstyle="round", zorder=8.5)
    ax.plot([x, x - 0.1], [y - 0.2, y - 0.34], color=style.person_fill, linewidth=style.glyph_lw * 0.85, solid_capstyle="round", zorder=8.5)
    ax.plot([x, x + 0.1], [y - 0.2, y - 0.34], color=style.person_fill, linewidth=style.glyph_lw * 0.85, solid_capstyle="round", zorder=8.5)
    ax.add_patch(Circle((x, y + 0.22), radius=0.06, facecolor=style.person_fill, edgecolor="none", zorder=8.6))


def _draw_plan_vehicle(ax: Any, u: float, v: float, *, length_m: float, width_m: float, plan_angle_rad: float, facecolor: str, style: AxonometricBoardStyle) -> None:
    half_l = float(length_m) / 2.0
    half_w = float(width_m) / 2.0
    polygon = [
        _project_plan_point(u - half_l, v - half_w, angle_rad=plan_angle_rad),
        _project_plan_point(u + half_l, v - half_w, angle_rad=plan_angle_rad),
        _project_plan_point(u + half_l, v + half_w, angle_rad=plan_angle_rad),
        _project_plan_point(u - half_l, v + half_w, angle_rad=plan_angle_rad),
    ]
    shadow = [(x + 0.18, y - 0.12) for x, y in polygon]
    _draw_polygon_patch(ax, shadow, facecolor=(0.0, 0.0, 0.0, 0.10), zorder=7.8)
    _draw_polygon_patch(ax, polygon, facecolor=facecolor, edgecolor="white", linewidth=style.detail_lw, zorder=8.0)


def _draw_plan_furniture(ax: Any, u: float, v: float, *, category: str, plan_angle_rad: float, style: AxonometricBoardStyle) -> None:
    from matplotlib.patches import Circle, Rectangle

    x, y = _project_plan_point(u, v, angle_rad=plan_angle_rad)
    if category == "tree":
        _draw_plan_tree(ax, x, y, style=style)
        return
    if category == "lamp":
        ax.plot([x, x], [y - 0.28, y + 0.36], color=style.furniture_fill, linewidth=style.glyph_lw, zorder=8.1)
        ax.add_patch(Circle((x, y + 0.38), radius=0.11, facecolor=style.activity_fill, edgecolor="white", linewidth=0.45, zorder=8.2))
        ax.add_patch(Circle((x, y + 0.38), radius=0.2, facecolor=style.activity_fill, edgecolor="none", alpha=0.18, zorder=8.16))
        return
    if category == "bench":
        ax.add_patch(Rectangle((x - 0.26, y - 0.08), 0.52, 0.16, angle=32.0, facecolor=style.furniture_fill, edgecolor="white", linewidth=0.5, zorder=8.1))
        return
    if category == "bus_stop":
        ax.add_patch(Rectangle((x - 0.36, y - 0.12), 0.72, 0.24, angle=32.0, facecolor=style.bus_fill, edgecolor="white", linewidth=0.5, zorder=8.1))
        return
    if category == "bollard":
        left_base = _project_plan_point(u - 1.0, v, angle_rad=plan_angle_rad)
        right_base = _project_plan_point(u + 1.0, v, angle_rad=plan_angle_rad)
        post_dx = 0.08
        post_dy = 0.24
        for px, py in (left_base, right_base):
            ax.add_patch(
                Rectangle(
                    (px - post_dx / 2.0, py - post_dy / 2.0),
                    post_dx,
                    post_dy,
                    angle=32.0,
                    facecolor=style.furniture_fill,
                    edgecolor="white",
                    linewidth=0.45,
                    zorder=8.12,
                )
            )
        ax.plot(
            [left_base[0], right_base[0]],
            [left_base[1] + 0.03, right_base[1] + 0.03],
            color=style.furniture_fill,
            linewidth=max(style.glyph_lw * 0.95, 1.0),
            zorder=8.13,
        )
        ax.plot(
            [left_base[0], right_base[0]],
            [left_base[1] - 0.11, right_base[1] - 0.11],
            color=style.furniture_fill,
            linewidth=max(style.glyph_lw * 0.8, 0.9),
            zorder=8.12,
        )
        return
    if category == "trash":
        ax.add_patch(Rectangle((x - 0.09, y - 0.11), 0.18, 0.22, angle=32.0, facecolor=style.furniture_fill, edgecolor="white", linewidth=0.45, zorder=8.1))
        return
    if category == "mailbox":
        ax.add_patch(Rectangle((x - 0.1, y - 0.12), 0.2, 0.24, angle=32.0, facecolor=style.furniture_fill, edgecolor="white", linewidth=0.45, zorder=8.1))
        return
    if category == "hydrant":
        ax.add_patch(Circle((x, y), radius=0.11, facecolor=style.activity_fill, edgecolor="white", linewidth=0.45, zorder=8.1))
        return
    ax.add_patch(Circle((x, y), radius=0.11, facecolor=style.furniture_fill, edgecolor="white", linewidth=0.45, zorder=8.1))


def _draw_oblique_tree(ax: Any, u: float, v: float, *, style: AxonometricBoardStyle) -> None:
    ground = _project_oblique_point(u, v, 0.0)
    canopy = _project_oblique_point(u, v, 2.25)
    ax.plot([ground[0], canopy[0]], [ground[1], canopy[1] - 0.24], color=style.tree_trunk_fill, linewidth=1.2, zorder=8.2)
    ax.scatter([canopy[0] + 0.18], [canopy[1] - 0.16], s=460, color=style.tree_shadow_fill, alpha=0.28, zorder=8.23)
    ax.scatter([canopy[0]], [canopy[1]], s=520, color=style.tree_fill, edgecolors=style.outline, linewidths=0.62, zorder=8.3)


def _draw_oblique_person(ax: Any, u: float, v: float, *, style: AxonometricBoardStyle) -> None:
    foot = _project_oblique_point(u, v, 0.0)
    torso = _project_oblique_point(u, v, 0.55)
    head = _project_oblique_point(u, v, 0.78)
    ax.plot([foot[0], torso[0]], [foot[1], torso[1]], color=style.person_fill, linewidth=0.95, solid_capstyle="round", zorder=8.9)
    ax.plot([foot[0], foot[0] - 0.06], [foot[1], foot[1] - 0.12], color=style.person_fill, linewidth=0.75, solid_capstyle="round", zorder=8.9)
    ax.plot([foot[0], foot[0] + 0.06], [foot[1], foot[1] - 0.12], color=style.person_fill, linewidth=0.75, solid_capstyle="round", zorder=8.9)
    ax.scatter([head[0]], [head[1]], s=14, color=style.person_fill, zorder=9.0)


def _draw_oblique_vehicle(ax: Any, u: float, v: float, *, length_m: float, width_m: float, facecolor: str, style: AxonometricBoardStyle) -> None:
    half_l = float(length_m) / 2.0
    half_w = float(width_m) / 2.0
    top = _project_oblique_polygon(
        [
            (u - half_l, v - half_w),
            (u + half_l, v - half_w),
            (u + half_l, v + half_w),
            (u - half_l, v + half_w),
        ],
        h=0.26,
    )
    shadow = [(x + 0.18, y - 0.12) for x, y in top]
    _draw_polygon_patch(ax, shadow, facecolor=(0.0, 0.0, 0.0, 0.10), zorder=7.7)
    _draw_polygon_patch(ax, top, facecolor=facecolor, edgecolor="white", linewidth=style.detail_lw, zorder=8.0)


def _draw_oblique_furniture(ax: Any, u: float, v: float, *, category: str, style: AxonometricBoardStyle) -> None:
    if _draw_oblique_sprite(ax, u, v, category=category, zorder=8.34 if str(category).strip().lower() == "tree" else 8.32):
        return
    if category == "tree":
        _draw_oblique_tree(ax, u, v, style=style)
        return
    ground = _project_oblique_point(u, v, 0.0)
    if category == "lamp":
        top = _project_oblique_point(u, v, 2.7)
        lamp_head = _project_oblique_point(u + 0.14, v, 2.58)
        ax.plot([ground[0], top[0]], [ground[1], top[1]], color=style.furniture_fill, linewidth=1.15, zorder=8.3)
        ax.plot([top[0], lamp_head[0]], [top[1], lamp_head[1]], color=style.furniture_fill, linewidth=0.92, zorder=8.31)
        ax.scatter([lamp_head[0]], [lamp_head[1]], s=26, color=style.activity_fill, edgecolors="white", linewidths=0.5, zorder=8.4)
        ax.scatter([lamp_head[0]], [lamp_head[1]], s=86, color=style.activity_fill, alpha=0.16, zorder=8.35)
        return
    if category == "bench":
        seat = _project_oblique_polygon(
            [(u - 0.58, v - 0.12), (u + 0.58, v - 0.12), (u + 0.58, v + 0.12), (u - 0.58, v + 0.12)],
            h=0.42,
        )
        _draw_polygon_patch(ax, seat, facecolor=style.furniture_fill, edgecolor="white", linewidth=0.5, zorder=8.35)
        back = _project_oblique_polygon(
            [(u - 0.58, v + 0.06), (u + 0.58, v + 0.06), (u + 0.58, v + 0.12), (u - 0.58, v + 0.12)],
            h=0.92,
        )
        _draw_polygon_patch(ax, back, facecolor=style.furniture_fill, edgecolor="white", linewidth=0.45, zorder=8.36)
        return
    if category == "bus_stop":
        canopy = _project_oblique_polygon(
            [(u - 1.2, v - 0.28), (u + 1.2, v - 0.28), (u + 1.2, v + 0.28), (u - 1.2, v + 0.28)],
            h=1.15,
        )
        _draw_polygon_patch(ax, canopy, facecolor=style.bus_fill, edgecolor="white", linewidth=0.55, zorder=8.4)
        return
    if category == "trash":
        bin_top = _project_oblique_polygon(
            [(u - 0.12, v - 0.12), (u + 0.12, v - 0.12), (u + 0.12, v + 0.12), (u - 0.12, v + 0.12)],
            h=0.56,
        )
        _draw_polygon_patch(ax, bin_top, facecolor=style.furniture_fill, edgecolor="white", linewidth=0.45, zorder=8.25)
        return
    if category == "mailbox":
        box_top = _project_oblique_polygon(
            [(u - 0.13, v - 0.1), (u + 0.13, v - 0.1), (u + 0.13, v + 0.1), (u - 0.13, v + 0.1)],
            h=0.82,
        )
        _draw_polygon_patch(ax, box_top, facecolor=style.furniture_fill, edgecolor="white", linewidth=0.45, zorder=8.25)
        return
    if category == "hydrant":
        top = _project_oblique_point(u, v, 0.66)
        ax.plot([ground[0], top[0]], [ground[1], top[1]], color=style.activity_fill, linewidth=1.2, zorder=8.28)
        ax.scatter([top[0]], [top[1]], s=18, color=style.activity_fill, edgecolors="white", linewidths=0.4, zorder=8.29)
        return
    if category == "bollard":
        post_left_ground = _project_oblique_point(u - 1.0, v, 0.0)
        post_right_ground = _project_oblique_point(u + 1.0, v, 0.0)
        post_left_top = _project_oblique_point(u - 1.0, v, 1.02)
        post_right_top = _project_oblique_point(u + 1.0, v, 1.02)
        rail_top = _project_oblique_polygon(
            [(u - 1.0, v - 0.04), (u + 1.0, v - 0.04), (u + 1.0, v + 0.04), (u - 1.0, v + 0.04)],
            h=0.88,
        )
        rail_mid = _project_oblique_polygon(
            [(u - 1.0, v - 0.035), (u + 1.0, v - 0.035), (u + 1.0, v + 0.035), (u - 1.0, v + 0.035)],
            h=0.56,
        )
        for start, end in ((post_left_ground, post_left_top), (post_right_ground, post_right_top)):
            ax.plot([start[0], end[0]], [start[1], end[1]], color=style.furniture_fill, linewidth=1.05, zorder=8.23)
        _draw_polygon_patch(ax, rail_mid, facecolor=style.furniture_fill, edgecolor="white", linewidth=0.4, zorder=8.24)
        _draw_polygon_patch(ax, rail_top, facecolor=style.furniture_fill, edgecolor="white", linewidth=0.42, zorder=8.25)
        return
    ax.scatter([ground[0]], [ground[1]], s=18, color=style.furniture_fill, edgecolors="white", linewidths=0.45, zorder=8.2)


def _draw_building_windows(
    ax: Any,
    left_bottom: Tuple[float, float],
    right_bottom: Tuple[float, float],
    left_top: Tuple[float, float],
    right_top: Tuple[float, float],
    *,
    style: AxonometricBoardStyle,
    floor_count: int,
    column_count: int,
) -> None:
    for row_idx in range(1, max(1, int(floor_count))):
        t = float(row_idx) / float(max(1, int(floor_count)))
        start = (
            left_bottom[0] + (left_top[0] - left_bottom[0]) * t,
            left_bottom[1] + (left_top[1] - left_bottom[1]) * t,
        )
        end = (
            right_bottom[0] + (right_top[0] - right_bottom[0]) * t,
            right_bottom[1] + (right_top[1] - right_bottom[1]) * t,
        )
        ax.plot([start[0], end[0]], [start[1], end[1]], color=style.window_fill, linewidth=0.5, alpha=0.9, zorder=8.75)
    for col_idx in range(1, max(1, int(column_count))):
        t = float(col_idx) / float(max(1, int(column_count)))
        bottom = (
            left_bottom[0] + (right_bottom[0] - left_bottom[0]) * t,
            left_bottom[1] + (right_bottom[1] - left_bottom[1]) * t,
        )
        top = (
            left_top[0] + (right_top[0] - left_top[0]) * t,
            left_top[1] + (right_top[1] - left_top[1]) * t,
        )
        ax.plot([bottom[0], top[0]], [bottom[1], top[1]], color=style.window_fill, linewidth=0.42, alpha=0.72, zorder=8.75)


def _render_axonometric_plan_view(
    layout_payload: Mapping[str, Any],
    *,
    out_path: Path,
    config: StreetComposeConfig,
) -> Dict[str, str]:
    plt = _require_matplotlib()
    if plt is None:
        return {}
    style = _AXONOMETRIC_BOARD_STYLE
    origin_xz = _scene_origin(layout_payload)
    axis_angle = _street_axis_angle(layout_payload)
    plan_angle = math.radians(32.0)
    surface_world = _world_surface_polygons(layout_payload)
    zone_world = _world_zone_polygons(layout_payload)
    local_surface = {
        role: [_localize_polygon(polygon, origin_xz=origin_xz, axis_angle_rad=axis_angle) for polygon in polygons]
        for role, polygons in surface_world.items()
    }
    local_zone = {
        role: [_localize_polygon(polygon, origin_xz=origin_xz, axis_angle_rad=axis_angle) for polygon in polygons]
        for role, polygons in zone_world.items()
    }
    local_crossings = [
        _localize_polygon(polygon, origin_xz=origin_xz, axis_angle_rad=axis_angle)
        for polygon in _crossing_world_polygons(layout_payload)
    ]
    building_boxes = _local_building_boxes(layout_payload, origin_xz=origin_xz, axis_angle_rad=axis_angle)
    visible_building_boxes = _visible_oblique_building_boxes(building_boxes)
    local_poi_points = {
        poi_type: [
            _localize_point((float(point[0]), float(point[1])), origin_xz=origin_xz, axis_angle_rad=axis_angle)
            for point in points
        ]
        for poi_type, points in nonempty_poi_points(
            ((layout_payload.get("summary", {}) or {}).get("spatial_context", {}) or {}).get("poi_points_by_type_xz", {}) or {}
        ).items()
    }
    localized_placements = []
    for placement in layout_payload.get("placements", []) or []:
        pos = placement.get("position_xyz", []) or []
        if len(pos) < 3:
            continue
        u, v = _localize_point((float(pos[0]), float(pos[2])), origin_xz=origin_xz, axis_angle_rad=axis_angle)
        localized_placements.append({"category": str(placement.get("category", "") or ""), "u": u, "v": v})

    projected_points: List[Tuple[float, float]] = []
    for polygons in list(local_surface.values()) + list(local_zone.values()) + [local_crossings]:
        for polygon in polygons:
            projected_points.extend(_project_plan_polygon(polygon, angle_rad=plan_angle))
    for box in building_boxes:
        projected_points.extend(
            _project_plan_polygon(
                [
                    (box["min_u"], box["min_v"]),
                    (box["max_u"], box["min_v"]),
                    (box["max_u"], box["max_v"]),
                    (box["min_u"], box["max_v"]),
                ],
                angle_rad=plan_angle,
            )
        )
    if not projected_points:
        projected_points = [(-8.0, -4.0), (8.0, 4.0)]
    min_x, min_y, max_x, max_y = _bbox_from_points(projected_points)
    margin_x = max(4.0, (max_x - min_x) * 0.14)
    margin_y = max(4.0, (max_y - min_y) * 0.18)

    fig, ax = plt.subplots(figsize=(10.4, 6.6))
    ax.set_facecolor(style.background)

    board = [
        (min_x - margin_x * 0.55, min_y - margin_y * 0.45),
        (max_x + margin_x * 0.35, min_y - margin_y * 0.45),
        (max_x + margin_x * 0.35, max_y + margin_y * 0.3),
        (min_x - margin_x * 0.55, max_y + margin_y * 0.3),
    ]
    board_shadow = [(x + style.shadow_offset_x, y + style.shadow_offset_y) for x, y in board]
    _draw_polygon_patch(ax, board_shadow, facecolor=style.board_shadow, zorder=0.1)
    _draw_polygon_patch(ax, board, facecolor=style.board_fill, zorder=0.2)

    for polygon in local_zone.get("green_land_use", []):
        _draw_polygon_patch(ax, _project_plan_polygon(polygon, angle_rad=plan_angle), facecolor=style.green_fill, edgecolor="none", alpha=0.9, zorder=1.0)
    for polygon in local_surface.get("sidewalk", []):
        _draw_polygon_patch(ax, _project_plan_polygon(polygon, angle_rad=plan_angle), facecolor=style.sidewalk_fill, edgecolor="none", zorder=1.5)
    for role in ("left_furnishing", "right_furnishing", "left_transit_edge", "right_transit_edge"):
        for polygon in local_zone.get(role, []):
            _draw_polygon_patch(ax, _project_plan_polygon(polygon, angle_rad=plan_angle), facecolor=style.furnishing_fill, edgecolor="none", alpha=0.92, zorder=1.6)
    for polygon in local_crossings:
        _draw_polygon_patch(ax, _project_plan_polygon(polygon, angle_rad=plan_angle), facecolor=style.activity_fill, edgecolor=style.accent_edge, linewidth=style.detail_lw, alpha=0.95, zorder=2.0)
    for polygon in local_surface.get("carriageway", []):
        _draw_polygon_patch(ax, _project_plan_polygon(polygon, angle_rad=plan_angle), facecolor=style.carriageway_fill, edgecolor="none", zorder=2.2)

    centerline_u_min, centerline_u_max = _centerline_bounds(local_surface.get("carriageway", []))
    dash_length = max(2.6, min(5.0, (centerline_u_max - centerline_u_min) / 14.0))
    gap_length = dash_length * 0.72
    cursor = centerline_u_min + dash_length * 0.5
    while cursor < centerline_u_max - dash_length * 0.5:
        start = _project_plan_point(cursor - dash_length * 0.5, 0.0, angle_rad=plan_angle)
        end = _project_plan_point(cursor + dash_length * 0.5, 0.0, angle_rad=plan_angle)
        ax.plot([start[0], end[0]], [start[1], end[1]], color=style.lane_mark_fill, linewidth=1.55, solid_capstyle="round", zorder=2.5)
        cursor += dash_length + gap_length

    for box in building_boxes:
        polygon = [
            _project_plan_point(box["min_u"], box["min_v"], angle_rad=plan_angle),
            _project_plan_point(box["max_u"], box["min_v"], angle_rad=plan_angle),
            _project_plan_point(box["max_u"], box["max_v"], angle_rad=plan_angle),
            _project_plan_point(box["min_u"], box["max_v"], angle_rad=plan_angle),
        ]
        shadow = [(x + style.shadow_offset_x * 0.55, y + style.shadow_offset_y * 0.45) for x, y in polygon]
        _draw_polygon_patch(ax, shadow, facecolor=(0.0, 0.0, 0.0, 0.08), zorder=3.0)
        _draw_polygon_patch(ax, polygon, facecolor=style.facade_fill, edgecolor=style.outline, linewidth=style.outline_lw, zorder=3.2)
        center_u = (box["min_u"] + box["max_u"]) / 2.0
        center_v = (box["min_v"] + box["max_v"]) / 2.0
        inset_u = max(0.45, (box["max_u"] - box["min_u"]) * 0.11)
        inset_v = max(0.28, (box["max_v"] - box["min_v"]) * 0.14)
        roof_strip = [
            _project_plan_point(box["min_u"] + inset_u, box["min_v"] + inset_v, angle_rad=plan_angle),
            _project_plan_point(box["max_u"] - inset_u, box["min_v"] + inset_v, angle_rad=plan_angle),
            _project_plan_point(box["max_u"] - inset_u, box["max_v"] - inset_v, angle_rad=plan_angle),
            _project_plan_point(box["min_u"] + inset_u, box["max_v"] - inset_v, angle_rad=plan_angle),
        ]
        _draw_polygon_patch(ax, roof_strip, facecolor=style.roof_fill, edgecolor="none", alpha=0.8, zorder=3.3)

    summary = dict(layout_payload.get("summary", {}) or {})
    road_width = float(summary.get("road_width_m", 8.0))
    sidewalk_width = float(summary.get("sidewalk_width_m", 2.5))
    vehicle_specs = _ambient_vehicle_specs(
        u_min=centerline_u_min,
        u_max=centerline_u_max,
        road_width_m=road_width,
        lane_count=int(getattr(config, "lane_count", 2) or 2),
        include_bus=bool(local_poi_points.get("bus_stop")),
    )
    for spec in vehicle_specs:
        _draw_plan_vehicle(
            ax,
            float(spec["u"]),
            float(spec["v"]),
            length_m=float(spec["length_m"]),
            width_m=float(spec["width_m"]),
            plan_angle_rad=plan_angle,
            facecolor=style.bus_fill if str(spec.get("kind", "car")) == "bus" else style.vehicle_fill,
            style=style,
        )

    people_points = _ambient_people_points(
        poi_points_local=local_poi_points,
        u_min=centerline_u_min,
        u_max=centerline_u_max,
        road_width_m=road_width,
        sidewalk_width_m=sidewalk_width,
    )
    for u, v in people_points:
        x, y = _project_plan_point(u, v, angle_rad=plan_angle)
        _draw_plan_person(ax, x, y, style=style)

    for placement in localized_placements:
        _draw_plan_furniture(
            ax,
            float(placement["u"]),
            float(placement["v"]),
            category=str(placement["category"]),
            plan_angle_rad=plan_angle,
            style=style,
        )

    ax.set_xlim(min_x - margin_x, max_x + margin_x)
    ax.set_ylim(min_y - margin_y, max_y + margin_y)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    _save_fig_to_path(fig, out_path, dpi=220, facecolor=style.background)
    plt.close(fig)
    return {"name": "final_plan_axonometric", "title": "Final Plan Axonometric", "path": str(Path(out_path).resolve())}


def _render_axonometric_oblique_view(
    layout_payload: Mapping[str, Any],
    *,
    out_path: Path,
    config: StreetComposeConfig,
) -> Dict[str, str]:
    plt = _require_matplotlib()
    if plt is None:
        return {}
    style = _AXONOMETRIC_BOARD_STYLE
    origin_xz = _scene_origin(layout_payload)
    axis_angle = _street_axis_angle(layout_payload)
    surface_world = _world_surface_polygons(layout_payload)
    zone_world = _world_zone_polygons(layout_payload)
    local_surface = {
        role: [_localize_polygon(polygon, origin_xz=origin_xz, axis_angle_rad=axis_angle) for polygon in polygons]
        for role, polygons in surface_world.items()
    }
    local_zone = {
        role: [_localize_polygon(polygon, origin_xz=origin_xz, axis_angle_rad=axis_angle) for polygon in polygons]
        for role, polygons in zone_world.items()
    }
    local_crossings = [
        _localize_polygon(polygon, origin_xz=origin_xz, axis_angle_rad=axis_angle)
        for polygon in _crossing_world_polygons(layout_payload)
    ]
    building_boxes = _local_building_boxes(layout_payload, origin_xz=origin_xz, axis_angle_rad=axis_angle)
    visible_building_boxes = _visible_oblique_building_boxes(building_boxes)
    local_building_doors = _local_building_doors(layout_payload, origin_xz=origin_xz, axis_angle_rad=axis_angle)
    localized_placements = []
    for placement in layout_payload.get("placements", []) or []:
        pos = placement.get("position_xyz", []) or []
        if len(pos) < 3:
            continue
        u, v = _localize_point((float(pos[0]), float(pos[2])), origin_xz=origin_xz, axis_angle_rad=axis_angle)
        localized_placements.append({"category": str(placement.get("category", "") or ""), "u": u, "v": v})
    local_poi_points = {
        poi_type: [
            _localize_point((float(point[0]), float(point[1])), origin_xz=origin_xz, axis_angle_rad=axis_angle)
            for point in points
        ]
        for poi_type, points in nonempty_poi_points(
            ((layout_payload.get("summary", {}) or {}).get("spatial_context", {}) or {}).get("poi_points_by_type_xz", {}) or {}
        ).items()
    }

    projected_points: List[Tuple[float, float]] = []
    for polygons in list(local_surface.values()) + list(local_zone.values()) + [local_crossings]:
        for polygon in polygons:
            projected_points.extend(_project_oblique_polygon(polygon, h=0.0))
    for box in visible_building_boxes:
        projected_points.extend(
            _project_oblique_polygon(
                [
                    (box["min_u"], box["min_v"]),
                    (box["max_u"], box["min_v"]),
                    (box["max_u"], box["max_v"]),
                    (box["min_u"], box["max_v"]),
                ],
                h=max(float(box["height_m"]), 3.6),
            )
        )
    if not projected_points:
        projected_points = [(-8.0, -4.0), (8.0, 4.0)]
    min_x, min_y, max_x, max_y = _bbox_from_points(projected_points)
    margin_x = max(5.0, (max_x - min_x) * 0.18)
    margin_y = max(3.8, (max_y - min_y) * 0.22)

    fig, ax = plt.subplots(figsize=(10.8, 6.8))
    ax.set_facecolor(style.background)

    board_uv = [
        (min(point[0] for polygon in local_surface.get("sidewalk", []) + local_surface.get("carriageway", []) for point in polygon) - 4.0,
         min(point[1] for polygon in local_surface.get("sidewalk", []) + local_surface.get("carriageway", []) for point in polygon) - 4.0),
        (max(point[0] for polygon in local_surface.get("sidewalk", []) + local_surface.get("carriageway", []) for point in polygon) + 4.0,
         min(point[1] for polygon in local_surface.get("sidewalk", []) + local_surface.get("carriageway", []) for point in polygon) - 4.0),
        (max(point[0] for polygon in local_surface.get("sidewalk", []) + local_surface.get("carriageway", []) for point in polygon) + 4.0,
         max(point[1] for polygon in local_surface.get("sidewalk", []) + local_surface.get("carriageway", []) for point in polygon) + 4.5),
        (min(point[0] for polygon in local_surface.get("sidewalk", []) + local_surface.get("carriageway", []) for point in polygon) - 4.0,
         max(point[1] for polygon in local_surface.get("sidewalk", []) + local_surface.get("carriageway", []) for point in polygon) + 4.5),
    ] if (local_surface.get("sidewalk") or local_surface.get("carriageway")) else [(-20.0, -8.0), (20.0, -8.0), (20.0, 8.0), (-20.0, 8.0)]
    board = _project_oblique_polygon(board_uv, h=-0.15)
    board_shadow = [(x + style.shadow_offset_x, y + style.shadow_offset_y) for x, y in board]
    _draw_polygon_patch(ax, board_shadow, facecolor=style.board_shadow, zorder=0.1)
    _draw_polygon_patch(ax, board, facecolor=style.board_fill, zorder=0.2)

    for polygon in local_zone.get("green_land_use", []):
        _draw_polygon_patch(ax, _project_oblique_polygon(polygon, h=0.0), facecolor=style.green_fill, edgecolor="none", alpha=0.92, zorder=1.0)
    for polygon in local_surface.get("sidewalk", []):
        _draw_polygon_patch(ax, _project_oblique_polygon(polygon, h=0.0), facecolor=style.sidewalk_fill, edgecolor="none", zorder=1.4)
    for role in ("left_furnishing", "right_furnishing", "left_transit_edge", "right_transit_edge"):
        for polygon in local_zone.get(role, []):
            _draw_polygon_patch(ax, _project_oblique_polygon(polygon, h=0.01), facecolor=style.furnishing_fill, edgecolor="none", alpha=0.96, zorder=1.6)
    for polygon in local_crossings:
        _draw_polygon_patch(ax, _project_oblique_polygon(polygon, h=0.02), facecolor=style.activity_fill, edgecolor=style.accent_edge, linewidth=style.detail_lw, alpha=0.95, zorder=1.8)
    for polygon in local_surface.get("carriageway", []):
        _draw_polygon_patch(ax, _project_oblique_polygon(polygon, h=0.0), facecolor=style.carriageway_fill, edgecolor="none", zorder=2.0)

    centerline_u_min, centerline_u_max = _centerline_bounds(local_surface.get("carriageway", []))
    dash_length = max(2.6, min(5.0, (centerline_u_max - centerline_u_min) / 14.0))
    gap_length = dash_length * 0.72
    cursor = centerline_u_min + dash_length * 0.5
    while cursor < centerline_u_max - dash_length * 0.5:
        start = _project_oblique_point(cursor - dash_length * 0.5, 0.0, 0.03)
        end = _project_oblique_point(cursor + dash_length * 0.5, 0.0, 0.03)
        ax.plot([start[0], end[0]], [start[1], end[1]], color=style.lane_mark_fill, linewidth=1.4, solid_capstyle="round", zorder=2.3)
        cursor += dash_length + gap_length

    visible_building_boxes = sorted(
        visible_building_boxes,
        key=lambda item: float(item.get("center_u", 0.0)),
        reverse=True,
    )
    for box in visible_building_boxes:
        min_u = float(box["min_u"])
        max_u = float(box["max_u"])
        min_v = float(box["min_v"])
        max_v = float(box["max_v"])
        display_h = max(float(box["height_m"]), 3.6)
        roof = _project_oblique_polygon(
            [(min_u, min_v), (max_u, min_v), (max_u, max_v), (min_u, max_v)],
            h=display_h,
        )
        street_face_v = min_v if ((min_v + max_v) / 2.0) >= 0.0 else max_v
        street_face = _project_oblique_polygon(
            [(min_u, street_face_v), (max_u, street_face_v), (max_u, street_face_v), (min_u, street_face_v)],
            h=0.0,
        )
        street_face_top = _project_oblique_polygon(
            [(min_u, street_face_v), (max_u, street_face_v), (max_u, street_face_v), (min_u, street_face_v)],
            h=display_h,
        )
        near_end = _project_oblique_polygon(
            [(min_u, min_v), (min_u, max_v), (min_u, max_v), (min_u, min_v)],
            h=0.0,
        )
        near_end_top = _project_oblique_polygon(
            [(min_u, min_v), (min_u, max_v), (min_u, max_v), (min_u, min_v)],
            h=display_h,
        )
        street_quad = [street_face[0], street_face[1], street_face_top[1], street_face_top[0]]
        end_quad = [near_end[0], near_end[1], near_end_top[1], near_end_top[0]]
        _draw_polygon_patch(ax, end_quad, facecolor=style.facade_shadow_fill, edgecolor=style.outline, linewidth=style.detail_lw, zorder=4.0)
        _draw_polygon_patch(ax, street_quad, facecolor=style.facade_fill, edgecolor=style.outline, linewidth=style.detail_lw, zorder=4.1)
        _draw_polygon_patch(ax, roof, facecolor=style.roof_fill, edgecolor=style.outline, linewidth=style.detail_lw, zorder=4.2)
        floor_count = max(2, min(18, int(round(display_h / 3.4))))
        facade_cols = max(3, min(9, int(round((max_u - min_u) / 2.0))))
        end_cols = max(2, min(4, int(round(abs(max_v - min_v) / 2.0))))
        _draw_building_windows(ax, street_quad[0], street_quad[1], street_quad[3], street_quad[2], style=style, floor_count=floor_count, column_count=facade_cols)
        _draw_building_windows(ax, end_quad[0], end_quad[1], end_quad[3], end_quad[2], style=style, floor_count=floor_count, column_count=end_cols)

    for door in local_building_doors:
        if float(door.get("center_v", 0.0)) >= 0.0:
            continue
        left_bottom = _project_oblique_point(float(door["left_u"]), float(door["left_v"]), 0.02)
        right_bottom = _project_oblique_point(float(door["right_u"]), float(door["right_v"]), 0.02)
        left_top = _project_oblique_point(float(door["left_u"]), float(door["left_v"]), float(door["height_m"]))
        right_top = _project_oblique_point(float(door["right_u"]), float(door["right_v"]), float(door["height_m"]))
        _draw_polygon_patch(
            ax,
            [left_bottom, right_bottom, right_top, left_top],
            facecolor=style.activity_fill,
            edgecolor="white",
            linewidth=0.45,
            alpha=0.95,
            zorder=4.25,
        )

    summary = dict(layout_payload.get("summary", {}) or {})
    road_width = float(summary.get("road_width_m", 8.0))
    sidewalk_width = float(summary.get("sidewalk_width_m", 2.5))
    vehicle_specs = _ambient_vehicle_specs(
        u_min=centerline_u_min,
        u_max=centerline_u_max,
        road_width_m=road_width,
        lane_count=int(getattr(config, "lane_count", 2) or 2),
        include_bus=bool(local_poi_points.get("bus_stop")),
    )
    for spec in vehicle_specs:
        _draw_oblique_vehicle(
            ax,
            float(spec["u"]),
            float(spec["v"]),
            length_m=float(spec["length_m"]),
            width_m=float(spec["width_m"]),
            facecolor=style.bus_fill if str(spec.get("kind", "car")) == "bus" else style.vehicle_fill,
            style=style,
        )

    people_points = _ambient_people_points(
        poi_points_local=local_poi_points,
        u_min=centerline_u_min,
        u_max=centerline_u_max,
        road_width_m=road_width,
        sidewalk_width_m=sidewalk_width,
    )
    for u, v in people_points:
        _draw_oblique_person(ax, u, v, style=style)

    for placement in localized_placements:
        _draw_oblique_furniture(
            ax,
            float(placement["u"]),
            float(placement["v"]),
            category=str(placement["category"]),
            style=style,
        )

    ax.set_xlim(min_x - margin_x, max_x + margin_x)
    ax.set_ylim(min_y - margin_y, max_y + margin_y)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    _save_fig_to_path(fig, out_path, dpi=220, facecolor=style.background)
    plt.close(fig)
    return {"name": "final_oblique_45_axonometric", "title": "Final Oblique 45 Axonometric", "path": str(Path(out_path).resolve())}


def _layout_bounds(layout_payload: Mapping[str, Any]) -> Tuple[float, float, float, float]:
    summary = dict(layout_payload.get("summary", {}) or {})
    osm_geometry = dict(summary.get("osm_geometry", {}) or {})
    bbox = osm_geometry.get("aoi_bbox_m")
    if bbox and len(bbox) == 4:
        return float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
    xs: List[float] = []
    zs: List[float] = []
    for placement in layout_payload.get("placements", []) or []:
        pos = placement.get("position_xyz", [])
        if len(pos) >= 3:
            xs.append(float(pos[0]))
            zs.append(float(pos[2]))
    if xs and zs:
        return min(xs) - 6.0, min(zs) - 6.0, max(xs) + 6.0, max(zs) + 6.0
    length_m = float(summary.get("length_m", 80.0))
    road_width_m = float(summary.get("road_width_m", 8.0))
    sidewalk_width_m = float(summary.get("sidewalk_width_m", 2.5))
    return (-length_m / 2.0 - 4.0, -(road_width_m / 2.0 + sidewalk_width_m + 4.0), length_m / 2.0 + 4.0, road_width_m / 2.0 + sidewalk_width_m + 4.0)


def _building_polygons_with_height(layout_payload: Mapping[str, Any]) -> List[Tuple[Tuple[Tuple[float, float], ...], float]]:
    polygons: List[Tuple[Tuple[Tuple[float, float], ...], float]] = []
    for item in layout_payload.get("building_footprints", []) or []:
        polygon = tuple(
            (float(point[0]), float(point[1]))
            for point in (item.get("polygon_xz", []) or [])
            if len(point) >= 2
        )
        if len(polygon) >= 4:
            polygons.append((polygon, float(item.get("target_height_m", 18.0) or 18.0)))
    if polygons:
        return polygons
    for item in layout_payload.get("generated_lots", []) or []:
        polygon = tuple(
            (float(point[0]), float(point[1]))
            for point in (item.get("polygon_xz", []) or [])
            if len(point) >= 2
        )
        if len(polygon) >= 4:
            polygons.append((polygon, float(item.get("target_height_m", 16.0) or 16.0)))
    return polygons


def _save_fig_to_path(fig, out_path: Path, *, dpi: int = 180, facecolor: str = "white") -> None:
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight", facecolor=facecolor)


def _watercolorize_image(
    source_path: Path,
    *,
    out_path: Path,
    paper_rgb: Tuple[int, int, int] = (246, 241, 232),
    blur_radius: float = 1.4,
    posterize_bits: int = 5,
    paper_noise: float = 10.0,
    paper_blend: float = 0.82,
    edge_blur_radius: float = 0.9,
    edge_strength: float = 0.28,
    bloom_radius: float = 1.8,
    bloom_blend: float = 0.18,
    color_boost: float = 0.82,
    contrast_boost: float = 0.96,
    vignette_strength: float = 0.12,
    ink_rgb: Tuple[int, int, int] = (78, 69, 58),
) -> bool:
    pillow = _require_pillow()
    if pillow is None:
        return False
    Image, ImageChops, ImageEnhance, ImageFilter, ImageOps = pillow
    try:
        image = Image.open(Path(source_path).resolve()).convert("RGBA")
    except Exception:
        return False
    rgb = image.convert("RGB")
    wash = rgb.filter(ImageFilter.SMOOTH_MORE).filter(ImageFilter.GaussianBlur(radius=float(blur_radius)))
    wash = ImageOps.posterize(wash, int(posterize_bits))
    wash = wash.filter(ImageFilter.ModeFilter(size=3))

    noise = Image.effect_noise(image.size, float(paper_noise)).convert("L")
    noise = ImageOps.autocontrast(noise)
    paper = ImageOps.colorize(
        noise,
        black=tuple(max(0, channel - 18) for channel in paper_rgb),
        white=paper_rgb,
    ).convert("RGBA")
    blended = Image.blend(paper, wash.convert("RGBA"), float(paper_blend))

    edges = wash.convert("L").filter(ImageFilter.FIND_EDGES).filter(ImageFilter.GaussianBlur(radius=float(edge_blur_radius)))
    edge_alpha = ImageOps.invert(edges).point(lambda value: int(max(0, min(255, (255 - value) * float(edge_strength)))))
    ink = Image.new("RGBA", image.size, (int(ink_rgb[0]), int(ink_rgb[1]), int(ink_rgb[2]), 0))
    ink.putalpha(edge_alpha)
    blended.alpha_composite(ink)

    bloom = blended.filter(ImageFilter.GaussianBlur(radius=float(bloom_radius)))
    blended = Image.blend(blended, bloom, float(bloom_blend))
    toned = ImageEnhance.Color(blended.convert("RGB")).enhance(float(color_boost)).convert("RGBA")
    toned = ImageEnhance.Contrast(toned.convert("RGB")).enhance(float(contrast_boost)).convert("RGBA")

    vignette = Image.radial_gradient("L").resize(image.size)
    vignette = ImageOps.invert(vignette).point(lambda value: int(value * float(vignette_strength)))
    vignette_layer = Image.new("RGBA", image.size, (255, 249, 240, 0))
    vignette_layer.putalpha(vignette)
    toned.alpha_composite(vignette_layer)

    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    toned.save(out_path)
    return True


def _draw_ground(ax: Any, layout_payload: Mapping[str, Any], palette: Mapping[str, Tuple[int, int, int, int]]) -> None:
    from matplotlib.patches import Polygon as MplPolygon, Rectangle

    summary = dict(layout_payload.get("summary", {}) or {})
    osm_geometry = dict(summary.get("osm_geometry", {}) or {})
    if osm_geometry.get("carriageway_rings"):
        for ring in osm_geometry.get("carriageway_rings", []) or []:
            ax.add_patch(MplPolygon(ring, closed=True, facecolor=[c / 255.0 for c in palette["carriageway"][:3]], edgecolor="none", alpha=0.95))
        for ring in osm_geometry.get("sidewalk_rings", []) or []:
            ax.add_patch(MplPolygon(ring, closed=True, facecolor=[c / 255.0 for c in palette["sidewalk"][:3]], edgecolor="none", alpha=0.95))
        for ring in osm_geometry.get("left_sidewalk_rings", []) or []:
            ax.add_patch(MplPolygon(ring, closed=True, facecolor=[c / 255.0 for c in palette["furnishing"][:3]], edgecolor="none", alpha=0.45))
        for ring in osm_geometry.get("right_sidewalk_rings", []) or []:
            ax.add_patch(MplPolygon(ring, closed=True, facecolor=[c / 255.0 for c in palette["clear_path"][:3]], edgecolor="none", alpha=0.3))
        return

    bounds = _layout_bounds(layout_payload)
    length = bounds[2] - bounds[0]
    road_width = float(summary.get("road_width_m", 8.0))
    sidewalk_width = float(summary.get("sidewalk_width_m", 2.5))
    ax.add_patch(Rectangle((bounds[0], -road_width / 2.0), length, road_width, facecolor=[c / 255.0 for c in palette["carriageway"][:3]], edgecolor="none"))
    ax.add_patch(Rectangle((bounds[0], road_width / 2.0), length, sidewalk_width, facecolor=[c / 255.0 for c in palette["sidewalk"][:3]], edgecolor="none"))
    ax.add_patch(Rectangle((bounds[0], -road_width / 2.0 - sidewalk_width), length, sidewalk_width, facecolor=[c / 255.0 for c in palette["sidewalk"][:3]], edgecolor="none"))


def _plot_top_view(fig, ax: Any, layout_payload: Mapping[str, Any], palette: Mapping[str, Tuple[int, int, int, int]], *, zoom: Optional[Tuple[float, float, float, float]] = None, title: str = "") -> None:
    _draw_ground(ax, layout_payload, palette)
    summary = dict(layout_payload.get("summary", {}) or {})
    spatial_ctx = dict(summary.get("spatial_context", {}) or {})
    for poi_type, points in nonempty_poi_points(spatial_ctx.get("poi_points_by_type_xz", {}) or {}).items():
        cfg = poi_plot_config(poi_type)
        xs = [float(point[0]) for point in points]
        zs = [float(point[1]) for point in points]
        ax.scatter(xs, zs, s=42, marker=str(cfg["marker"]), color=str(cfg["color"]), edgecolors="white", linewidths=0.6, alpha=0.95, zorder=4)
    for placement in layout_payload.get("placements", []) or []:
        pos = placement.get("position_xyz", [])
        if len(pos) < 3:
            continue
        category = str(placement.get("category", ""))
        color = {
            "bench": "#a34f2a",
            "lamp": "#f2b705",
            "trash": "#51624f",
            "tree": "#4d8b31",
            "bus_stop": "#3f74bf",
            "mailbox": "#8b4db8",
            "hydrant": "#e85d04",
            "bollard": "#404040",
        }.get(category, "#777777")
        ax.scatter([float(pos[0])], [float(pos[2])], s=64, marker="o", color=color, edgecolors="black", linewidths=0.4, zorder=5)
    min_x, min_z, max_x, max_z = zoom or _layout_bounds(layout_payload)
    ax.set_xlim(min_x, max_x)
    ax.set_ylim(min_z, max_z)
    ax.set_aspect("equal")
    ax.set_title(title)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.tight_layout()


def _project_iso(x: float, z: float, h: float, view_sign: float) -> Tuple[float, float]:
    return (x + view_sign * 0.55 * z, 0.16 * x - 0.75 * z + h)


def _plot_hero_view(fig, ax: Any, layout_payload: Mapping[str, Any], palette: Mapping[str, Tuple[int, int, int, int]], *, focus_x: float, view_sign: float, title: str) -> None:
    from matplotlib.patches import Polygon as MplPolygon

    bounds = _layout_bounds(layout_payload)
    road_poly = [
        _project_iso(bounds[0], -2.0, 0.0, view_sign),
        _project_iso(bounds[2], -2.0, 0.0, view_sign),
        _project_iso(bounds[2], 2.0, 0.0, view_sign),
        _project_iso(bounds[0], 2.0, 0.0, view_sign),
    ]
    side_poly = [
        _project_iso(bounds[0], 2.0, 0.0, view_sign),
        _project_iso(bounds[2], 2.0, 0.0, view_sign),
        _project_iso(bounds[2], 5.4, 0.0, view_sign),
        _project_iso(bounds[0], 5.4, 0.0, view_sign),
    ]
    ax.add_patch(MplPolygon(road_poly, closed=True, facecolor=[c / 255.0 for c in palette["carriageway"][:3]], edgecolor="none", alpha=0.95))
    ax.add_patch(MplPolygon(side_poly, closed=True, facecolor=[c / 255.0 for c in palette["sidewalk"][:3]], edgecolor="none", alpha=0.92))
    placements = sorted(layout_payload.get("placements", []) or [], key=lambda placement: float((placement.get("position_xyz") or [0.0, 0.0, 0.0])[2]), reverse=view_sign < 0)
    for placement in placements:
        pos = placement.get("position_xyz", [])
        if len(pos) < 3:
            continue
        x = float(pos[0]) - focus_x
        z = float(pos[2])
        h = _PRESENTATION_HEIGHTS.get(str(placement.get("category", "")), 1.2)
        base = _project_iso(x, z, 0.0, view_sign)
        top = _project_iso(x, z, h, view_sign)
        color = {
            "bench": "#a34f2a",
            "lamp": "#f2b705",
            "trash": "#51624f",
            "tree": "#4d8b31",
            "bus_stop": "#3f74bf",
            "mailbox": "#8b4db8",
            "hydrant": "#e85d04",
            "bollard": "#404040",
        }.get(str(placement.get("category", "")), "#777777")
        ax.plot([base[0], top[0]], [base[1], top[1]], color=color, linewidth=2.0, alpha=0.9)
        ax.scatter([base[0]], [base[1]], s=90, color="black", alpha=0.18, zorder=2)
        ax.scatter([top[0]], [top[1]], s=90 + h * 18.0, color=color, edgecolors="white", linewidths=0.7, zorder=5)
    ax.set_title(title)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.tight_layout()


def _project_polygon_iso(
    polygon_xz: Sequence[Tuple[float, float]],
    *,
    h: float,
    view_sign: float,
) -> List[Tuple[float, float]]:
    return [_project_iso(float(x), float(z), float(h), float(view_sign)) for x, z in polygon_xz]


def _plot_watercolor_oblique_view(
    fig,
    ax: Any,
    layout_payload: Mapping[str, Any],
    palette: Mapping[str, Tuple[int, int, int, int]],
    *,
    title: str,
    view_sign: float = 1.0,
) -> None:
    from matplotlib.patches import Polygon as MplPolygon

    summary = dict(layout_payload.get("summary", {}) or {})
    bounds = _layout_bounds(layout_payload)
    osm_geometry = dict(summary.get("osm_geometry", {}) or {})
    paper_rgb = (246 / 255.0, 241 / 255.0, 232 / 255.0)
    ax.set_facecolor(paper_rgb)

    if osm_geometry.get("carriageway_rings"):
        for ring in osm_geometry.get("sidewalk_rings", []) or []:
            projected = _project_polygon_iso(tuple((float(x), float(z)) for x, z in ring), h=0.0, view_sign=view_sign)
            if len(projected) >= 3:
                ax.add_patch(MplPolygon(projected, closed=True, facecolor=[c / 255.0 for c in palette["sidewalk"][:3]], edgecolor="none", alpha=0.90))
        for ring in osm_geometry.get("carriageway_rings", []) or []:
            projected = _project_polygon_iso(tuple((float(x), float(z)) for x, z in ring), h=0.0, view_sign=view_sign)
            if len(projected) >= 3:
                ax.add_patch(MplPolygon(projected, closed=True, facecolor=[c / 255.0 for c in palette["carriageway"][:3]], edgecolor="none", alpha=0.95))
    else:
        road_width = float(summary.get("road_width_m", 8.0))
        sidewalk_width = float(summary.get("sidewalk_width_m", 2.5))
        surfaces = [
            (
                [
                    (bounds[0], -road_width / 2.0 - sidewalk_width),
                    (bounds[2], -road_width / 2.0 - sidewalk_width),
                    (bounds[2], -road_width / 2.0),
                    (bounds[0], -road_width / 2.0),
                ],
                palette["sidewalk"],
                0.88,
            ),
            (
                [
                    (bounds[0], -road_width / 2.0),
                    (bounds[2], -road_width / 2.0),
                    (bounds[2], road_width / 2.0),
                    (bounds[0], road_width / 2.0),
                ],
                palette["carriageway"],
                0.95,
            ),
            (
                [
                    (bounds[0], road_width / 2.0),
                    (bounds[2], road_width / 2.0),
                    (bounds[2], road_width / 2.0 + sidewalk_width),
                    (bounds[0], road_width / 2.0 + sidewalk_width),
                ],
                palette["sidewalk"],
                0.90,
            ),
        ]
        for polygon, color, alpha in surfaces:
            projected = _project_polygon_iso(tuple((float(x), float(z)) for x, z in polygon), h=0.0, view_sign=view_sign)
            ax.add_patch(MplPolygon(projected, closed=True, facecolor=[c / 255.0 for c in color[:3]], edgecolor="none", alpha=alpha))

    buildings = sorted(
        _building_polygons_with_height(layout_payload),
        key=lambda item: sum(point[1] for point in item[0]) / max(len(item[0]), 1),
        reverse=view_sign < 0,
    )
    for polygon_xz, target_height_m in buildings:
        base = _project_polygon_iso(polygon_xz, h=0.0, view_sign=view_sign)
        display_height = min(max(float(target_height_m) * 0.18, 2.8), 11.0)
        roof = _project_polygon_iso(polygon_xz, h=display_height, view_sign=view_sign)
        if len(base) < 3 or len(roof) < 3:
            continue
        wall_fill = (214 / 255.0, 203 / 255.0, 190 / 255.0)
        wall_shadow = (185 / 255.0, 172 / 255.0, 158 / 255.0)
        roof_fill = (240 / 255.0, 234 / 255.0, 226 / 255.0)
        for idx in range(len(base) - 1):
            quad = [base[idx], base[idx + 1], roof[idx + 1], roof[idx]]
            avg_z = (polygon_xz[idx][1] + polygon_xz[idx + 1][1]) / 2.0
            alpha = 0.62 if avg_z * view_sign <= 0.0 else 0.48
            face = wall_fill if avg_z * view_sign <= 0.0 else wall_shadow
            ax.add_patch(MplPolygon(quad, closed=True, facecolor=face, edgecolor="none", alpha=alpha))
        ax.add_patch(MplPolygon(roof, closed=True, facecolor=roof_fill, edgecolor=(0.56, 0.52, 0.48, 0.35), linewidth=0.6, alpha=0.92))

    placements = sorted(
        layout_payload.get("placements", []) or [],
        key=lambda placement: float((placement.get("position_xyz") or [0.0, 0.0, 0.0])[2]),
        reverse=view_sign < 0,
    )
    category_colors = {
        "bench": "#a76a3c",
        "lamp": "#d9b14c",
        "trash": "#596954",
        "tree": "#5c8c47",
        "bus_stop": "#5b82bf",
        "mailbox": "#9165b7",
        "hydrant": "#d96b3b",
        "bollard": "#575757",
    }
    for placement in placements:
        pos = placement.get("position_xyz", []) or []
        if len(pos) < 3:
            continue
        category = str(placement.get("category", "") or "")
        color = category_colors.get(category, "#777777")
        h = _PRESENTATION_HEIGHTS.get(category, 1.2)
        base = _project_iso(float(pos[0]), float(pos[2]), 0.0, view_sign)
        top = _project_iso(float(pos[0]), float(pos[2]), min(float(h), 5.0), view_sign)
        ax.plot([base[0], top[0]], [base[1], top[1]], color=color, linewidth=2.2, alpha=0.78)
        ax.scatter([top[0]], [top[1]], s=70 + h * 18.0, color=color, edgecolors="white", linewidths=0.7, alpha=0.88, zorder=6)

    ax.set_title(title)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_aspect("equal")
    fig.tight_layout()


def render_presentation_views(
    layout_payload: Mapping[str, Any],
    *,
    out_dir: Path,
    config: StreetComposeConfig,
) -> List[Dict[str, str]]:
    preset = load_style_preset(getattr(config, "style_preset", None))
    palette = preset.scene_colors
    pillow = _require_pillow()
    render_preset = str(getattr(config, "render_preset", "axonometric_board_v1") or "axonometric_board_v1").strip().lower()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    view_dir = out_dir / "presentation_views"
    view_dir.mkdir(parents=True, exist_ok=True)

    views: List[Dict[str, str]] = []
    placements = layout_payload.get("placements", []) or []
    if placements:
        hero_anchor_x = float((placements[0].get("position_xyz") or [0.0, 0.0, 0.0])[0])
        if any(str(item.get("category", "")) == "bus_stop" for item in placements):
            hero_anchor_x = float(next((item.get("position_xyz") or [0.0, 0.0, 0.0])[0] for item in placements if str(item.get("category", "")) == "bus_stop"))
    else:
        hero_anchor_x = 0.0

    topdown_mode = str(getattr(config, "topdown_render_mode", "design_tiles_v1")).strip().lower()
    plan_base_path: Optional[Path] = None
    if topdown_mode == "design_tiles_v1":
        try:
            from .topdown_render import render_design_topdown

            design_view = render_design_topdown(
                layout_payload,
                out_dir=out_dir,
                config=config,
                palette=palette,
            )
        except Exception:
            design_view = None
        if design_view is not None:
            views.append(design_view)
            plan_base_path = Path(str(design_view["path"])).resolve()

    plt = _require_matplotlib()
    if topdown_mode == "legacy_vector" or not views:
        if plt is None:
            return views
        overview_path = view_dir / "overview_top.png"
        fig, ax = plt.subplots(figsize=(7.2, 4.6))
        _plot_top_view(fig, ax, layout_payload, palette, title=f"{preset.display_name} Overview")
        _save_fig_to_path(fig, overview_path, dpi=180, facecolor="white")
        plt.close(fig)
        views.append({"name": "overview_top", "title": "Overview Top", "path": str(overview_path)})
        plan_base_path = overview_path.resolve()

    if plt is None:
        return views

    final_views: List[Dict[str, str]] = []
    if render_preset == "axonometric_board_v1":
        final_plan = _render_axonometric_plan_view(
            layout_payload,
            out_path=(view_dir / "final_plan_axonometric.png").resolve(),
            config=config,
        )
        if final_plan:
            final_views.append(final_plan)
        final_oblique = _render_axonometric_oblique_view(
            layout_payload,
            out_path=(view_dir / "final_oblique_45_axonometric.png").resolve(),
            config=config,
        )
        if final_oblique:
            final_views.append(final_oblique)
    elif pillow is not None:
        if plan_base_path is None:
            plan_base_path = (view_dir / "watercolor_plan_base.png").resolve()
            fig, ax = plt.subplots(figsize=(8.4, 8.4))
            _plot_top_view(fig, ax, layout_payload, palette, title=f"{preset.display_name} Watercolor Plan")
            _save_fig_to_path(fig, plan_base_path, dpi=210, facecolor="white")
            plt.close(fig)
        watercolor_plan_path = (view_dir / "final_plan_watercolor.png").resolve()
        if _watercolorize_image(
            plan_base_path,
            out_path=watercolor_plan_path,
            paper_rgb=(246, 241, 232),
            blur_radius=1.4,
            paper_noise=10.0,
            paper_blend=0.82,
            edge_blur_radius=0.9,
            edge_strength=0.28,
            bloom_radius=1.8,
            bloom_blend=0.18,
            color_boost=0.82,
            contrast_boost=0.96,
            vignette_strength=0.12,
            ink_rgb=(78, 69, 58),
        ):
            final_views.append({"name": "final_plan_watercolor", "title": "Final Plan Watercolor", "path": str(watercolor_plan_path)})

        oblique_base_path = (view_dir / "watercolor_oblique_45_base.png").resolve()
        fig, ax = plt.subplots(figsize=(8.8, 5.8))
        _plot_watercolor_oblique_view(
            fig,
            ax,
            layout_payload,
            palette,
            title=f"{preset.display_name} Oblique 45",
            view_sign=1.0,
        )
        _save_fig_to_path(fig, oblique_base_path, dpi=210, facecolor="white")
        plt.close(fig)
        watercolor_oblique_path = (view_dir / "final_oblique_45_watercolor.png").resolve()
        if _watercolorize_image(
            oblique_base_path,
            out_path=watercolor_oblique_path,
            paper_rgb=(245, 239, 231),
            blur_radius=1.2,
            paper_noise=9.0,
            paper_blend=0.80,
            edge_blur_radius=0.8,
            edge_strength=0.24,
            bloom_radius=1.5,
            bloom_blend=0.16,
            color_boost=0.80,
            contrast_boost=0.97,
            vignette_strength=0.10,
            ink_rgb=(86, 74, 62),
        ):
            final_views.append({"name": "final_oblique_45_watercolor", "title": "Final Oblique 45 Watercolor", "path": str(watercolor_oblique_path)})

    if final_views:
        views = final_views + views

    hero_left_path = view_dir / "hero_left.png"
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    _plot_hero_view(fig, ax, layout_payload, palette, focus_x=hero_anchor_x - 6.0, view_sign=-1.0, title=f"{preset.display_name} Hero Left")
    _save_fig_to_path(fig, hero_left_path, dpi=180, facecolor="white")
    plt.close(fig)
    views.append({"name": "hero_left", "title": "Hero Left", "path": str(hero_left_path)})

    hero_right_path = view_dir / "hero_right.png"
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    _plot_hero_view(fig, ax, layout_payload, palette, focus_x=hero_anchor_x + 6.0, view_sign=1.0, title=f"{preset.display_name} Hero Right")
    _save_fig_to_path(fig, hero_right_path, dpi=180, facecolor="white")
    plt.close(fig)
    views.append({"name": "hero_right", "title": "Hero Right", "path": str(hero_right_path)})

    poi_focus_path = view_dir / "poi_focus.png"
    poi_points = nonempty_poi_points(((layout_payload.get("summary", {}) or {}).get("spatial_context", {}) or {}).get("poi_points_by_type_xz", {}) or {})
    if poi_points:
        flat_points = [point for points in poi_points.values() for point in points]
        xs = [float(point[0]) for point in flat_points]
        zs = [float(point[1]) for point in flat_points]
        zoom = (min(xs) - 5.0, min(zs) - 5.0, max(xs) + 5.0, max(zs) + 5.0)
    else:
        zoom = None
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    _plot_top_view(fig, ax, layout_payload, palette, zoom=zoom, title="POI / Furniture Focus")
    _save_fig_to_path(fig, poi_focus_path, dpi=180, facecolor="white")
    plt.close(fig)
    views.append({"name": "poi_focus", "title": "POI Focus", "path": str(poi_focus_path)})
    return views
