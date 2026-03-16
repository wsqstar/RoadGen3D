"""Presentation-oriented style presets, curation, composition, and views."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
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


def _scene_eligible(row: Mapping[str, Any]) -> bool:
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
    if str(row.get("category", "")).strip().lower() != "tree":
        return False
    provenance = asset_generator_type(row)
    if provenance in {"parametric", "legacy", "procedural_fallback"}:
        return False
    source = str(row.get("source", "") or "").strip().lower()
    return source not in {"procedural_generated", "parametric_generated", "procedural_fallback", "external_import"} and _tree_upright_validated(row)


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


def _require_matplotlib():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    return plt


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


def render_presentation_views(
    layout_payload: Mapping[str, Any],
    *,
    out_dir: Path,
    config: StreetComposeConfig,
) -> List[Dict[str, str]]:
    plt = _require_matplotlib()
    if plt is None:
        return []
    preset = load_style_preset(getattr(config, "style_preset", None))
    palette = preset.scene_colors
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

    overview_path = view_dir / "overview_top.png"
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    _plot_top_view(fig, ax, layout_payload, palette, title=f"{preset.display_name} Overview")
    fig.savefig(overview_path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    views.append({"name": "overview_top", "title": "Overview Top", "path": str(overview_path)})

    hero_left_path = view_dir / "hero_left.png"
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    _plot_hero_view(fig, ax, layout_payload, palette, focus_x=hero_anchor_x - 6.0, view_sign=-1.0, title=f"{preset.display_name} Hero Left")
    fig.savefig(hero_left_path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    views.append({"name": "hero_left", "title": "Hero Left", "path": str(hero_left_path)})

    hero_right_path = view_dir / "hero_right.png"
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    _plot_hero_view(fig, ax, layout_payload, palette, focus_x=hero_anchor_x + 6.0, view_sign=1.0, title=f"{preset.display_name} Hero Right")
    fig.savefig(hero_right_path, dpi=180, bbox_inches="tight", facecolor="white")
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
    fig.savefig(poi_focus_path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    views.append({"name": "poi_focus", "title": "POI Focus", "path": str(poi_focus_path)})
    return views
