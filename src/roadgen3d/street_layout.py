"""Street-level scene composition utilities for M3."""

from __future__ import annotations

import json
import logging
import math
import random
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

from .beauty import (
    apply_composition_pass,
    asset_generator_type,
    compute_presentation_report,
    curate_candidates,
    render_presentation_views,
    shape_program_for_style,
    style_palette,
    surface_roughness,
)
from .asset_scale import VALID_ASSET_SCALE_MODES, compute_asset_scale, summarize_asset_scales
from .design_rules import load_constraint_set
from .embedder import ClipTextEmbedder
from .entrance_analysis import (
    CarriagewayBoundary,
    PlacedAssetRegistry,
    evaluate_all_entrances,
    score_entrance_impact,
)
from .eval_metrics import (
    compute_balance_score,
    compute_cross_section_feasibility,
    compute_dropped_slot_rate,
    compute_editability,
    compute_explainability,
    compute_latency_ms_per_instance,
    compute_overlap_rate,
    compute_rule_satisfaction_rate,
    compute_spacing_uniformity,
    compute_style_consistency,
    compute_topology_validity,
    evaluate_topk_category_hits,
)
from .index_store import FaissIndexStore
from .layout_features import CandidateDescriptor, PolicyFeatureContext, vectorize_slot_candidates
from .layout_policy import LayoutPolicyRuntime
from .layout_solver import LayoutSolverRuntime, solve_layout
from .placement_field import (
    UniformSpatialHash,
    compose_candidate_energy,
    load_placement_field_config,
    pair_cutoff_radius_m,
    pair_interaction_scores,
    placement_field_path,
    placement_priority_rank,
    poi_attraction_score,
)
from .spatial_features import build_spatial_context, compute_slot_distances
from .osm_segment_graph import build_segment_graph
from .poi_taxonomy import (
    CANONICAL_FIRE_POI,
    canonicalize_poi_type,
    asset_backed_poi_anchor_counts,
    asset_category_for_poi,
    core_poi_count,
    extract_poi_points_by_type,
    nonempty_poi_points,
    normalize_poi_counts,
    poi_plot_config,
    poi_weighted_score,
    qualifies_poi_counts,
)
from .program_generator import ProgramGeneratorRuntime
from .poi_rules import load_rule_set
from .scene_graph_viz import build_scene_graph
from .scene_textures import (
    VALID_SCENE_TEXTURE_MODES,
    apply_default_scene_texture,
    create_scene_texture_tracker,
    scene_texture_pack_name,
)
from .spatial_viz import (
    plot_poi_exclusion_overview,
    plot_zoning_grid_preview as plot_zoning_grid_preview_2d,
)
from .street_priors import DEFAULT_CATEGORIES, DEFAULT_SPACING_M, SIDE_PREF
from .street_program import infer_street_program
from .theme_buildings import (
    assign_theme_id_for_point,
    build_zoning_grid_preview,
    building_query,
    collect_building_footprints,
    generate_frontage_infill_footprints,
    generate_grid_growth_lots,
    infer_theme_segments,
    rerank_building_candidates,
    summarize_land_use_grid,
    theme_profile_style,
)
from .types import (
    BuildingFootprint,
    BuildingPlacementPlan,
    GeneratedLot,
    InventorySummary,
    LayoutSolverInput,
    LayoutSolverResult,
    ProductionStepRecord,
    ProgramGenerationInput,
    ThemeSegment,
    StreetComposeConfig,
    StreetComposeResult,
    StreetPlacement,
)

SOFTMAX_TEMPERATURE = 0.12
CATEGORY_NO_REPEAT_FIRST = True
FILL_PRIORITY = True


@dataclass(frozen=True)
class _MeshCacheEntry:
    mesh: object
    half_x: float
    half_z: float
    min_y: float
    is_scene: bool = False
    native_height_y: float = 0.0


@dataclass(frozen=True)
class _SurroundingBuildingResult:
    building_footprints: Tuple[BuildingFootprint, ...]
    generated_lots: Tuple[GeneratedLot, ...]
    placements: Tuple[StreetPlacement, ...]
    plans: Tuple[BuildingPlacementPlan, ...]
    retrieval_predictions: Tuple[Dict[str, object], ...]
    building_summary: Dict[str, object]
    land_use_summary: Dict[str, object]
    lot_generation_summary: Dict[str, object]
    zoning_grid: Tuple[Dict[str, object], ...]
    zoning_preview_summary: Dict[str, object]
    instance_index: int


def _require_trimesh():
    try:
        import trimesh
    except ImportError as exc:
        raise RuntimeError("`trimesh` is required for M3 scene composition. Install requirements-m2.txt.") from exc
    return trimesh


def _resolve_path(path_text: object, base_dir: Path) -> str:
    path = Path(str(path_text)).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return str(path)


def _row_scene_eligible(row: Mapping[str, object]) -> bool:
    value = row.get("scene_eligible")
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if not text:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    if text in {"1", "true", "yes", "on"}:
        return True
    return True


_PARALLEL_TO_CARRIAGEWAY_CATEGORIES = {"bench", "bus_stop"}


def _row_quality_notes(row: Mapping[str, object]) -> Tuple[str, ...]:
    notes = row.get("quality_notes")
    if notes is None:
        return ()
    if isinstance(notes, str):
        text = notes.strip()
        return (text,) if text else ()
    return tuple(str(item).strip() for item in notes if str(item).strip())


def _tree_upright_validated(row: Mapping[str, object]) -> bool:
    if "tree_upright_validated" in _row_quality_notes(row):
        return True
    metrics = row.get("quality_metrics")
    if isinstance(metrics, Mapping):
        validation = metrics.get("tree_upright_validation")
        if isinstance(validation, Mapping):
            return not bool(str(validation.get("failure_reason", "")).strip())
    return False


def _is_external_tree_asset(row: Mapping[str, object]) -> bool:
    if str(row.get("category", "")).strip().lower() != "tree":
        return False
    provenance = asset_generator_type(row)
    if provenance in {"parametric", "legacy", "procedural_fallback"}:
        return False
    source = str(row.get("source", "") or "").strip().lower()
    return source not in {"procedural_generated", "parametric_generated", "procedural_fallback"} and _tree_upright_validated(row)


def _yaw_for_asset_category(category: str, facing_yaw_deg: float) -> float:
    yaw_deg = float(facing_yaw_deg)
    if str(category).strip().lower() in _PARALLEL_TO_CARRIAGEWAY_CATEGORIES:
        yaw_deg -= 90.0
    yaw_deg = math.fmod(yaw_deg, 360.0)
    if yaw_deg < 0.0:
        yaw_deg += 360.0
    return float(yaw_deg)


def _placement_asset_source_key(
    row: Mapping[str, object] | None,
    *,
    selection_source: str = "",
) -> str:
    """Return a stable provenance/source key for one placed asset."""

    if row is not None:
        source = str(row.get("source", "") or "").strip().lower()
        if source:
            return source
        generator = asset_generator_type(row)
        if generator:
            return str(generator).strip().lower()
    if str(selection_source).strip().lower() == "procedural_fallback":
        return "procedural_fallback"
    return "unknown"


def _native_size_for_entry(entry: _MeshCacheEntry) -> Dict[str, float]:
    return {
        "width_m": float(max(entry.half_x * 2.0, 0.0)),
        "depth_m": float(max(entry.half_z * 2.0, 0.0)),
        "height_m": float(max(entry.native_height_y, 0.0)),
    }


def _street_furniture_scale_info(
    *,
    category: str,
    entry: _MeshCacheEntry,
    config: StreetComposeConfig,
) -> Dict[str, Any]:
    native_size = _native_size_for_entry(entry)
    return compute_asset_scale(
        category=category,
        width_m=float(native_size["width_m"]),
        depth_m=float(native_size["depth_m"]),
        height_m=float(native_size["height_m"]),
        mode=str(getattr(config, "asset_scale_mode", "canonical_v1")),
    )


def _validate_config(config: StreetComposeConfig) -> None:
    if not config.query.strip():
        raise ValueError("query cannot be empty")
    if config.length_m <= 1.0:
        raise ValueError("length_m must be > 1.0")
    if config.road_width_m <= 0.5:
        raise ValueError("road_width_m must be > 0.5")
    if config.sidewalk_width_m <= 0.2:
        raise ValueError("sidewalk_width_m must be > 0.2")
    if config.lane_count <= 0:
        raise ValueError("lane_count must be >= 1")
    if config.density <= 0:
        raise ValueError("density must be > 0")
    if config.topk_per_category <= 0:
        raise ValueError("topk_per_category must be >= 1")
    if config.max_trials_per_slot <= 0:
        raise ValueError("max_trials_per_slot must be >= 1")
    # -- M5 validation --
    if config.layout_mode not in ("template", "osm"):
        raise ValueError("layout_mode must be 'template' or 'osm'")
    if config.constraint_mode not in ("off", "soft"):
        raise ValueError("constraint_mode must be 'off' or 'soft'")
    if config.layout_mode == "osm":
        if config.aoi_bbox is None or len(config.aoi_bbox) != 4:
            raise ValueError("aoi_bbox must be a 4-element tuple (min_lon, min_lat, max_lon, max_lat) when layout_mode='osm'")
    if not 0.0 <= config.constraint_weight <= 1.0:
        raise ValueError("constraint_weight must be in [0.0, 1.0]")
    if not 0.0 <= config.constraint_veto_threshold <= 1.0:
        raise ValueError("constraint_veto_threshold must be in [0.0, 1.0]")
    if str(config.program_generator).strip().lower() not in {"heuristic_v1", "learned_v1"}:
        raise ValueError("program_generator must be 'heuristic_v1' or 'learned_v1'")
    if str(config.layout_solver).strip().lower() not in {"banded", "milp_template_v1", "hybrid_milp_v1"}:
        raise ValueError("layout_solver must be 'banded', 'milp_template_v1', or 'hybrid_milp_v1'")
    if str(getattr(config, "objective_profile", "balanced")).strip().lower() not in {"balanced", "greening", "commerce", "transit"}:
        raise ValueError("objective_profile must be 'balanced', 'greening', 'commerce', or 'transit'")
    demand_levels = {"low", "medium", "high"}
    for field_name in ("ped_demand_level", "bike_demand_level", "transit_demand_level", "vehicle_demand_level"):
        if str(getattr(config, field_name, "medium")).strip().lower() not in demand_levels:
            raise ValueError(f"{field_name} must be 'low', 'medium', or 'high'")
    if float(getattr(config, "segment_length_m", 12.0)) <= 0.0:
        raise ValueError("segment_length_m must be > 0")
    if str(getattr(config, "width_budget_mode", "expand_total_width")).strip().lower() != "expand_total_width":
        raise ValueError("width_budget_mode must be 'expand_total_width'")
    if str(getattr(config, "sidewalk_distribution", "per_side")).strip().lower() != "per_side":
        raise ValueError("sidewalk_distribution must be 'per_side'")
    if str(getattr(config, "poi_fit_mode", "hard_containment")).strip().lower() != "hard_containment":
        raise ValueError("poi_fit_mode must be 'hard_containment'")
    base_lane_width_m = getattr(config, "base_lane_width_m", None)
    if base_lane_width_m is not None and float(base_lane_width_m) <= 0.0:
        raise ValueError("base_lane_width_m must be > 0 when provided")
    if str(getattr(config, "beauty_mode", "presentation_v1")).strip().lower() not in {"presentation_v1"}:
        raise ValueError("beauty_mode must be 'presentation_v1'")
    if str(getattr(config, "render_preset", "jury_default_v1")).strip().lower() not in {"jury_default_v1"}:
        raise ValueError("render_preset must be 'jury_default_v1'")
    if str(getattr(config, "topdown_render_mode", "design_tiles_v1")).strip().lower() not in {
        "legacy_vector",
        "design_tiles_v1",
    }:
        raise ValueError("topdown_render_mode must be 'legacy_vector' or 'design_tiles_v1'")
    if str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")).strip().lower() not in VALID_SCENE_TEXTURE_MODES:
        raise ValueError("scene_texture_mode must be 'topdown_tiles_v1' or 'solid_color_legacy'")
    if int(getattr(config, "topdown_canvas_px", 2048)) <= 0:
        raise ValueError("topdown_canvas_px must be > 0")
    if str(getattr(config, "asset_curation_mode", "scene_ready_first")).strip().lower() not in {
        "scene_ready_first",
        "parametric_first",
        "curated_first",
        "legacy",
    }:
        raise ValueError("asset_curation_mode must be 'scene_ready_first', 'parametric_first', 'curated_first' or 'legacy'")
    if str(getattr(config, "asset_scale_mode", "canonical_v1")).strip().lower() not in VALID_ASSET_SCALE_MODES:
        raise ValueError("asset_scale_mode must be 'canonical_v1' or 'native_raw'")
    if str(getattr(config, "road_selection", "walkable_neighborhood")).strip().lower() not in {
        "all",
        "primary_road",
        "longest",
        "walkable_neighborhood",
    }:
        raise ValueError("road_selection must be 'all', 'primary_road', 'longest' or 'walkable_neighborhood'")
    if int(getattr(config, "building_search_topk", 1)) <= 0:
        raise ValueError("building_search_topk must be >= 1")
    if str(getattr(config, "surrounding_building_mode", "grid_growth")).strip().lower() not in {"footprint_based", "grid_growth"}:
        raise ValueError("surrounding_building_mode must be 'footprint_based' or 'grid_growth'")
    if str(getattr(config, "zoning_granularity", "fine")).strip().lower() not in {"coarse", "balanced", "fine"}:
        raise ValueError("zoning_granularity must be 'coarse', 'balanced' or 'fine'")
    if not 0.0 <= float(getattr(config, "streetwall_continuity", 0.95)) <= 1.0:
        raise ValueError("streetwall_continuity must be in [0.0, 1.0]")
    if str(getattr(config, "infill_policy", "aggressive")).strip().lower() not in {
        "off",
        "large_gap_only",
        "balanced",
        "aggressive",
    }:
        raise ValueError("infill_policy must be 'off', 'large_gap_only', 'balanced' or 'aggressive'")
    if str(getattr(config, "theme_inference_mode", "deterministic_auto")).strip().lower() not in {"deterministic_auto"}:
        raise ValueError("theme_inference_mode must be 'deterministic_auto'")
    if str(getattr(config, "theme_vocab_name", "fixed_v1")).strip().lower() not in {"fixed_v1"}:
        raise ValueError("theme_vocab_name must be 'fixed_v1'")


def _validate_export_format(export_format: str) -> str:
    value = export_format.strip().lower()
    if value not in {"glb", "ply", "both"}:
        raise ValueError("export_format must be one of: glb, ply, both")
    return value


def _load_real_manifest(manifest_path: Path) -> List[Dict[str, object]]:
    if not manifest_path.exists():
        raise FileNotFoundError(f"real manifest not found: {manifest_path}")
    required = ("asset_id", "category", "text_desc", "mesh_path", "latent_path")
    rows: List[Dict[str, object]] = []
    base_dir = manifest_path.parent.resolve()
    for line_no, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        missing = [key for key in required if key not in payload or str(payload[key]).strip() == ""]
        if missing:
            raise ValueError(
                f"missing required fields in line {line_no} ({manifest_path}): {', '.join(missing)}"
            )
        row = {
            "asset_id": str(payload["asset_id"]).strip(),
            "category": str(payload["category"]).strip().lower(),
            "text_desc": str(payload["text_desc"]).strip(),
            "mesh_path": _resolve_path(payload["mesh_path"], base_dir),
            "latent_path": _resolve_path(payload["latent_path"], base_dir),
        }
        for optional_key in (
            "style_tags",
            "quality_tier",
            "material_family",
            "hero_asset",
            "avoid_with_presets",
            "asset_role",
            "theme_tags",
            "frontage_width_m",
            "depth_m",
            "height_class",
            "source",
            "generator_type",
            "runtime_profile",
            "parameter_snapshot",
            "quality_metrics",
            "scene_eligible",
            "mesh_face_count",
            "quality_notes",
        ):
            if optional_key in payload:
                row[optional_key] = payload[optional_key]
        if "asset_role" not in row:
            row["asset_role"] = "building" if row["category"] == "building" else "street_furniture"
        rows.append(row)
    if not rows:
        raise ValueError(f"real manifest is empty: {manifest_path}")
    return rows


def _load_mesh_cache(rows: List[Dict[str, str]]) -> Dict[str, _MeshCacheEntry]:
    trimesh = _require_trimesh()
    cache: Dict[str, _MeshCacheEntry] = {}
    for row in rows:
        asset_id = row["asset_id"]
        mesh_path = Path(row["mesh_path"]).resolve()
        if not mesh_path.exists():
            raise FileNotFoundError(f"mesh missing for asset '{asset_id}': {mesh_path}")
        mesh_or_scene = trimesh.load(mesh_path, force="scene")
        if isinstance(mesh_or_scene, trimesh.Scene):
            if not mesh_or_scene.geometry:
                raise ValueError(f"empty mesh scene for asset '{asset_id}': {mesh_path}")
            display_geom = mesh_or_scene
            bounds = np.asarray(display_geom.bounds, dtype=np.float64)
            is_scene = True
        else:
            if mesh_or_scene.is_empty:
                raise ValueError(f"empty mesh for asset '{asset_id}': {mesh_path}")
            display_geom = mesh_or_scene
            bounds = np.asarray(display_geom.bounds, dtype=np.float64)
            is_scene = False
        span = bounds[1] - bounds[0]
        cache[asset_id] = _MeshCacheEntry(
            mesh=display_geom,
            half_x=float(max(span[0] / 2.0, 1e-3)),
            half_z=float(max(span[2] / 2.0, 1e-3)),
            min_y=float(bounds[0][1]),
            is_scene=bool(is_scene),
            native_height_y=float(max(span[1], 1e-3)),
        )
    return cache


def _bbox_intersects(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> bool:
    return not (a[1] <= b[0] or b[1] <= a[0] or a[3] <= b[2] or b[3] <= a[2])


def _compute_bbox(
    x: float,
    z: float,
    yaw_deg: float,
    half_x: float,
    half_z: float,
    scale: float | Sequence[float],
    clearance: float,
) -> Tuple[float, float, float, float]:
    if isinstance(scale, (list, tuple)):
        scale_x = float(scale[0]) if len(scale) >= 1 else 1.0
        scale_z = float(scale[2]) if len(scale) >= 3 else float(scale[-1]) if len(scale) >= 1 else 1.0
    else:
        scale_x = float(scale)
        scale_z = float(scale)
    yaw_rad = math.radians(yaw_deg)
    cos_y = abs(math.cos(yaw_rad))
    sin_y = abs(math.sin(yaw_rad))
    aabb_half_x = cos_y * half_x * scale_x + sin_y * half_z * scale_z + clearance
    aabb_half_z = sin_y * half_x * scale_x + cos_y * half_z * scale_z + clearance
    return (x - aabb_half_x, x + aabb_half_x, z - aabb_half_z, z + aabb_half_z)


def _sample_pose(
    category: str,
    slot_idx: int,
    trial_idx: int,
    x_center: float,
    length_m: float,
    road_width_m: float,
    sidewalk_width_m: float,
    spacing_m: float,
    rng: random.Random,
) -> Tuple[float, float, float]:
    jitter_x = min(1.5, max(0.25, 0.2 * spacing_m))
    min_x = -length_m / 2.0 + 0.5
    max_x = length_m / 2.0 - 0.5
    x = float(np.clip(x_center + rng.uniform(-jitter_x, jitter_x), min_x, max_x))

    side_pref = SIDE_PREF.get(category, "both")
    if side_pref == "right":
        side = -1.0
    elif side_pref == "left":
        side = 1.0
    else:
        side = 1.0 if ((slot_idx + trial_idx) % 2 == 0) else -1.0

    z_center = side * (road_width_m / 2.0 + sidewalk_width_m * 0.5)
    z_jitter = sidewalk_width_m * 0.2
    z = z_center + rng.uniform(-z_jitter, z_jitter)

    yaw_base = 180.0 if side > 0 else 0.0
    yaw_deg = yaw_base + rng.uniform(-8.0, 8.0)
    return x, z, yaw_deg


def _sample_pose_for_slot(
    *,
    slot_x_center: float,
    slot_z_center: float,
    slot_side: str,
    slot_spacing_m: float,
    band_width_m: float,
    length_m: float,
    rng: random.Random,
) -> Tuple[float, float, float]:
    jitter_x = min(1.5, max(0.25, 0.2 * float(slot_spacing_m)))
    min_x = -float(length_m) / 2.0 + 0.5
    max_x = float(length_m) / 2.0 - 0.5
    x = float(np.clip(float(slot_x_center) + rng.uniform(-jitter_x, jitter_x), min_x, max_x))

    z_jitter = max(0.1, float(band_width_m) * 0.18)
    z = float(slot_z_center) + rng.uniform(-z_jitter, z_jitter)

    if slot_side == "left":
        yaw_base = 180.0
    elif slot_side == "right":
        yaw_base = 0.0
    else:
        yaw_base = 0.0
    yaw_deg = yaw_base + rng.uniform(-8.0, 8.0)
    return x, z, yaw_deg


def _softmax_weights(scores: Sequence[float], temperature: float) -> List[float]:
    if not scores:
        return []
    temp = max(float(temperature), 1e-6)
    arr = np.asarray([float(score) for score in scores], dtype=np.float64)
    shifted = (arr - float(arr.max())) / temp
    weights = np.exp(shifted)
    total = float(weights.sum())
    if not np.isfinite(total) or total <= 0.0:
        return [1.0 / len(scores)] * len(scores)
    return (weights / total).tolist()


def _segment_node_lookup(road_segment_graph: object | None) -> Dict[str, object]:
    return {
        str(getattr(node, "segment_id", "")): node
        for node in getattr(road_segment_graph, "nodes", ()) or ()
    }


def _aggregate_solver_results(
    *,
    resolved_program,
    solver_results: Sequence[LayoutSolverResult],
    slot_plans: Sequence[object],
    road_segment_graph_summary: Dict[str, object] | None = None,
) -> LayoutSolverResult:
    if not solver_results:
        raise RuntimeError("solver_results cannot be empty")
    backend_requested = str(solver_results[0].backend_requested)
    backend_used_values = tuple(dict.fromkeys(str(result.backend_used) for result in solver_results))
    fallback_values = [str(result.fallback_reason).strip() for result in solver_results if str(result.fallback_reason).strip()]
    return LayoutSolverResult(
        resolved_program=resolved_program,
        band_solutions=tuple(
            band_solution
            for result in solver_results
            for band_solution in result.band_solutions
        ),
        slot_plans=tuple(slot_plans),
        rule_evaluations=tuple(
            evaluation
            for result in solver_results
            for evaluation in result.rule_evaluations
        ),
        edits=tuple(edit for result in solver_results for edit in result.edits),
        conflicts=tuple(conflict for result in solver_results for conflict in result.conflicts),
        topology_validity=float(sum(float(result.topology_validity) for result in solver_results) / len(solver_results)),
        cross_section_feasibility=float(sum(float(result.cross_section_feasibility) for result in solver_results) / len(solver_results)),
        rule_satisfaction_rate=float(sum(float(result.rule_satisfaction_rate) for result in solver_results) / len(solver_results)),
        editability=float(sum(float(result.editability) for result in solver_results) / len(solver_results)),
        conflict_explainability=float(sum(float(result.conflict_explainability) for result in solver_results) / len(solver_results)),
        active_constraints=tuple(
            dict.fromkeys(
                constraint_name
                for result in solver_results
                for constraint_name in result.active_constraints
            )
        ),
        throughput_feasibility={
            "overall_satisfied": all(bool(result.throughput_feasibility.get("overall_satisfied", True)) for result in solver_results),
            "by_mode": {
                mode: data
                for result in solver_results
                for mode, data in dict(result.throughput_feasibility.get("by_mode", {})).items()
            },
        },
        objective_profile=str(getattr(resolved_program, "objective_profile", "balanced")),
        objective_score_breakdown={
            key: float(sum(float(result.objective_score_breakdown.get(key, 0.0)) for result in solver_results))
            for key in {"total_width_score", "unused_row_budget_m", "slot_mix_bias"}
        },
        backend_requested=backend_requested,
        backend_used=backend_used_values[0] if len(backend_used_values) == 1 else "mixed",
        fallback_reason=" | ".join(dict.fromkeys(fallback_values)),
        road_segment_graph_summary=road_segment_graph_summary,
    )


def _globalize_theme_slot_plans(
    slot_plans: Sequence[object],
    *,
    theme_segment: ThemeSegment,
    road_segment_graph: object | None,
) -> Tuple[Tuple[object, ...], Dict[str, object]]:
    nodes_by_id = _segment_node_lookup(road_segment_graph)
    theme_nodes = [
        nodes_by_id[segment_id]
        for segment_id in theme_segment.segment_ids
        if segment_id in nodes_by_id
    ]
    theme_nodes = sorted(theme_nodes, key=lambda node: float(getattr(node, "station_center_m", 0.0)))
    ordered_slots = sorted(slot_plans, key=lambda slot: float(getattr(slot, "x_center_m", 0.0)))
    slot_to_segment: Dict[str, object] = {}
    updated_slots: List[object] = []
    for idx, slot in enumerate(ordered_slots):
        slot_id = f"{theme_segment.theme_id}_{getattr(slot, 'slot_id', f'slot_{idx:03d}')}"
        node = None
        if getattr(slot, "anchor_position_xz", None) is not None and theme_nodes:
            anchor_x, anchor_z = getattr(slot, "anchor_position_xz")
            node = min(
                theme_nodes,
                key=lambda item: math.hypot(
                    float(getattr(item, "center_xy", (0.0, 0.0))[0]) - float(anchor_x),
                    float(getattr(item, "center_xy", (0.0, 0.0))[1]) - float(anchor_z),
                ),
            )
            slot_x = float(anchor_x)
            slot_z = float(anchor_z)
        elif theme_nodes:
            node_idx = min(int(math.floor(idx * len(theme_nodes) / max(len(ordered_slots), 1))), len(theme_nodes) - 1)
            node = theme_nodes[node_idx]
            slot_x = float(getattr(node, "center_xy", (0.0, 0.0))[0])
            slot_z = float(getattr(node, "center_xy", (0.0, 0.0))[1])
        else:
            slot_x = float(getattr(slot, "x_center_m", 0.0)) + float(theme_segment.center_x_m)
            slot_z = float(getattr(slot, "z_center_m", 0.0))
        updated = replace(
            slot,
            slot_id=slot_id,
            x_center_m=float(slot_x),
            z_center_m=float(slot_z),
            theme_id=theme_segment.theme_id,
        )
        updated_slots.append(updated)
        if node is not None:
            slot_to_segment[slot_id] = node
    return tuple(updated_slots), slot_to_segment


def _sample_pose_osm_for_segment(
    category: str,
    placement_ctx: object,
    rng: random.Random,
    *,
    segment_node: object | None = None,
    slot_side: str = "",
    band_width_m: float = 1.0,
    anchor_position_xz: Optional[Tuple[float, float]] = None,
) -> Optional[Tuple[float, float, float]]:
    from .placement_zones import compute_facing_angle, sample_slot_on_sidewalk

    if anchor_position_xz is not None:
        point = (float(anchor_position_xz[0]), float(anchor_position_xz[1]))
        yaw = _yaw_for_asset_category(
            category,
            compute_facing_angle(point, placement_ctx.carriageway),  # type: ignore[attr-defined]
        )
        return point[0], point[1], yaw

    if segment_node is not None:
        try:
            from shapely.geometry import Point as ShapelyPoint
        except Exception:
            segment_node = None
        else:
            start_xy = tuple(float(v) for v in getattr(segment_node, "start_xy", (0.0, 0.0)))
            end_xy = tuple(float(v) for v in getattr(segment_node, "end_xy", (0.0, 0.0)))
            center_xy = tuple(float(v) for v in getattr(segment_node, "center_xy", (0.0, 0.0)))
            dx = end_xy[0] - start_xy[0]
            dz = end_xy[1] - start_xy[1]
            length = math.hypot(dx, dz)
            if length > 1e-6:
                tangent = (dx / length, dz / length)
                left_normal = (-tangent[1], tangent[0])
                side_pref = slot_side or SIDE_PREF.get(category, "both")
                sign = 1.0 if side_pref == "left" else -1.0 if side_pref == "right" else (1.0 if rng.random() >= 0.5 else -1.0)
                normal = left_normal if sign > 0 else (-left_normal[0], -left_normal[1])
                carriageway_half = float(getattr(placement_ctx, "carriageway_width_m", 8.0) or 8.0) / 2.0
                lateral = carriageway_half + max(float(band_width_m) * 0.45, 0.8)
                along = rng.uniform(-max(length * 0.25, 0.5), max(length * 0.25, 0.5))
                point = (
                    center_xy[0] + tangent[0] * along + normal[0] * lateral,
                    center_xy[1] + tangent[1] * along + normal[1] * lateral,
                )
                preferred_zone = getattr(placement_ctx, "left_sidewalk_zone", None) if sign > 0 else getattr(placement_ctx, "right_sidewalk_zone", None)
                candidate_zone = preferred_zone if preferred_zone is not None and not getattr(preferred_zone, "is_empty", False) else placement_ctx.sidewalk_zone
                if candidate_zone is not None and not getattr(candidate_zone, "is_empty", False) and candidate_zone.buffer(0.05).contains(ShapelyPoint(point)):
                    yaw = _yaw_for_asset_category(
                        category,
                        compute_facing_angle(point, placement_ctx.carriageway),  # type: ignore[attr-defined]
                    )
                    return point[0], point[1], yaw

    side_pref = SIDE_PREF.get(category, "both")
    overall_zone = placement_ctx.sidewalk_zone  # type: ignore[attr-defined]
    if side_pref == "left":
        preferred_zone = getattr(placement_ctx, "left_sidewalk_zone", None)
    elif side_pref == "right":
        preferred_zone = getattr(placement_ctx, "right_sidewalk_zone", None)
    else:
        preferred_zone = overall_zone
    zone = preferred_zone
    if zone is None or getattr(zone, "is_empty", False):
        zone = overall_zone
    point = sample_slot_on_sidewalk(zone, rng)
    if point is None and zone is not overall_zone:
        point = sample_slot_on_sidewalk(overall_zone, rng)
    if point is None:
        return None
    yaw = _yaw_for_asset_category(
        category,
        compute_facing_angle(point, placement_ctx.carriageway),  # type: ignore[attr-defined]
    )
    return point[0], point[1], yaw


def _placeholder_building_entry(
    *,
    asset_id: str,
    frontage_width_m: float,
    depth_m: float,
    height_class: str,
    theme_name: str,
    target_height_m: float = 0.0,
) -> _MeshCacheEntry:
    try:
        from .parametric_assets import generate_parametric_asset

        params: Dict[str, object] = {
            "frontage_width_m": float(frontage_width_m),
            "depth_m": float(depth_m),
            "height_class": str(height_class),
            "theme_name": str(theme_name),
        }
        if target_height_m > 0.0:
            params["height_m"] = float(target_height_m)
        result = generate_parametric_asset(
            {
                "asset_kind": "building",
                "runtime_profile": "preview",
                "params": params,
            }
        )
        mesh = result.mesh
    except Exception:
        trimesh = _require_trimesh()
        if target_height_m > 0.0:
            height_m = float(target_height_m)
        else:
            height_m = {
                "lowrise": max(float(frontage_width_m) * 0.8, 8.0),
                "midrise": max(float(frontage_width_m) * 1.4, 14.0),
                "highrise": max(float(frontage_width_m) * 2.0, 22.0),
            }.get(str(height_class), max(float(frontage_width_m) * 1.2, 12.0))
        mesh = trimesh.creation.box(extents=(float(frontage_width_m), float(height_m), float(depth_m)))
        face_color = {
            "residential": (188, 174, 153, 255),
            "commercial": (176, 184, 192, 255),
            "transit": (151, 165, 182, 255),
            "green": (166, 171, 148, 255),
        }.get(str(theme_name), (178, 180, 178, 255))
        mesh.visual.face_colors = list(face_color)
    bounds = mesh.bounds
    span = bounds[1] - bounds[0]
    return _MeshCacheEntry(
        mesh=mesh,
        half_x=float(max(span[0] / 2.0, 1e-3)),
        half_z=float(max(span[2] / 2.0, 1e-3)),
        min_y=float(bounds[0][1]),
        native_height_y=float(max(span[1], 1e-3)),
    )


def _pick_category_candidate(
    query: str,
    category: str,
    topk: int,
    embedder: ClipTextEmbedder,
    index_store: FaissIndexStore,
    asset_by_id: Dict[str, Dict[str, object]],
    category_pool: List[Dict[str, object]],
    used_asset_ids: set[str],
    rng: random.Random,
    config: Optional[StreetComposeConfig] = None,
    placement_policy: str = "rule",
    policy_runtime: Optional[LayoutPolicyRuntime] = None,
    policy_temperature: float = SOFTMAX_TEMPERATURE,
    feature_context: Optional[PolicyFeatureContext] = None,
    return_details: bool = False,
) -> Tuple[Dict[str, object], float, str] | Tuple[Dict[str, object], float, str, Dict[str, object]]:
    def _pick_weighted(
        candidates: List[Tuple[Dict[str, object], float]],
        temperature: float,
    ) -> Tuple[Dict[str, object], float, int]:
        scores = [float(score) for _, score in candidates]
        weights = _softmax_weights(scores, temperature)
        pick_idx = rng.choices(range(len(candidates)), weights=weights, k=1)[0]
        row, score = candidates[pick_idx]
        return row, float(score), int(pick_idx)

    def _pick_with_policy(candidates: List[Tuple[Dict[str, object], float]]) -> Tuple[Dict[str, object], float, int]:
        if not candidates:
            raise RuntimeError("Policy candidate set cannot be empty.")
        if policy_runtime is None or feature_context is None:
            row, score, idx = _pick_weighted(candidates, policy_temperature)
            return row, score, idx

        candidate_desc = [
            CandidateDescriptor(asset_id=row["asset_id"], category=row["category"], score=float(score))
            for row, score in candidates
        ]
        features = vectorize_slot_candidates(feature_context, candidate_desc)
        logits = policy_runtime.score_candidates(features)
        weights = _softmax_weights(logits.tolist(), policy_temperature)
        pick_idx = int(rng.choices(range(len(candidates)), weights=weights, k=1)[0])
        row, score = candidates[pick_idx]
        return row, float(score), pick_idx

    slot_query = f"{query}, {category} street asset"
    query_embedding = embedder.encode_texts([slot_query])
    hits = index_store.search(query_embedding, topk=max(1, int(topk)))[0]
    matching_hits: List[Tuple[Dict[str, object], float]] = []
    all_hits: List[Dict[str, object]] = []
    for hit in hits:
        row = asset_by_id.get(hit.asset_id)
        if row is not None:
            all_hits.append(
                {
                    "asset_id": row["asset_id"],
                    "category": row["category"],
                    "score": float(hit.score),
                }
            )
        if row is not None and row["category"] == category:
            matching_hits.append((row, float(hit.score)))

    top3_hit = any(str(item.get("category", "")).strip().lower() == category for item in all_hits[:3])

    decision_payload: Dict[str, object] = {
        "candidates": all_hits,
        "chosen_index": -1,
        "top3_hit": bool(top3_hit),
    }

    if matching_hits:
        ranked_hits = list(matching_hits)
        if config is not None:
            ranked_hits, curation_info = curate_candidates(ranked_hits, category=category, config=config)
            decision_payload.update(curation_info)
        available_hits = [candidate for candidate in ranked_hits if candidate[0]["asset_id"] not in used_asset_ids]
        if CATEGORY_NO_REPEAT_FIRST and available_hits:
            if placement_policy == "learned":
                row, score, local_idx = _pick_with_policy(available_hits)
                source = "policy_softmax"
            else:
                row, score, local_idx = _pick_weighted(available_hits, policy_temperature)
                source = "faiss_softmax"
            decision_payload["chosen_index"] = int(local_idx)
            if return_details:
                return row, score, source, decision_payload
            return row, score, source
        if FILL_PRIORITY:
            if placement_policy == "learned":
                row, score, local_idx = _pick_with_policy(ranked_hits)
                source = "policy_relaxed_repeat"
            else:
                row, score, local_idx = _pick_weighted(ranked_hits, policy_temperature)
                source = "faiss_relaxed_repeat"
            decision_payload["chosen_index"] = int(local_idx)
            if return_details:
                return row, score, source, decision_payload
            return row, score, source

    if not category_pool:
        raise RuntimeError(f"empty category pool: {category}")

    pool_for_pick = list(category_pool)
    if config is not None:
        curated_pool, curation_info = curate_candidates(
            [(row, 0.0) for row in category_pool],
            category=category,
            config=config,
        )
        pool_for_pick = [row for row, _score in curated_pool]
        decision_payload["fallback_curated_used"] = bool(curation_info.get("curated_used", False))
        decision_payload["fallback_curated_candidate_count"] = int(curation_info.get("curated_candidate_count", 0))

    available_pool = [row for row in pool_for_pick if row["asset_id"] not in used_asset_ids]
    if CATEGORY_NO_REPEAT_FIRST and available_pool:
        row = rng.choice(available_pool)
        if return_details:
            decision_payload["chosen_index"] = 0
            return row, 0.0, "fallback_pool", decision_payload
        return row, 0.0, "fallback_pool"
    if FILL_PRIORITY:
        row = rng.choice(pool_for_pick)
        if return_details:
            decision_payload["chosen_index"] = 0
            return row, 0.0, "fallback_pool", decision_payload
        return row, 0.0, "fallback_pool"

    raise RuntimeError(
        f"Unable to pick candidate for category '{category}' from FAISS or fallback pool."
    )


def _build_base_scene(
    length_m: float,
    road_width_m: float,
    left_side_width_m: float,
    right_side_width_m: float,
    *,
    street_program: object | None = None,
    palette: Optional[Dict[str, Tuple[int, int, int, int]]] = None,
    roughness: Optional[Dict[str, float]] = None,
    texture_mode: str = "topdown_tiles_v1",
    texture_tracker=None,
):
    trimesh = _require_trimesh()
    scene = trimesh.Scene()
    total_width_m = float(road_width_m + left_side_width_m + right_side_width_m)
    context_ground = trimesh.creation.box(
        extents=(float(length_m) + 24.0, 0.04, max(total_width_m + 28.0, 24.0))
    )
    ctx_color = list((palette or {}).get("context_ground", (168, 163, 150, 255)))
    context_ground.apply_translation([0.0, -0.10, 0.0])
    context_ground = _apply_surface_finish(
        context_ground,
        surface_role="context_ground",
        rgba=ctx_color,
        roughness=(roughness or {}).get("context_ground", 0.85),
        texture_mode=texture_mode,
        texture_tracker=texture_tracker,
    )
    scene.add_geometry(context_ground, node_name="context_ground")

    road = trimesh.creation.box(extents=(length_m, 0.06, road_width_m))
    colors = palette or {}
    road_color = list(colors.get("carriageway", (65, 68, 72, 255)))
    road.apply_translation([0.0, -0.03, 0.0])
    road = _apply_surface_finish(
        road,
        surface_role="carriageway",
        rgba=road_color,
        roughness=(roughness or {}).get("carriageway", 0.95),
        texture_mode=texture_mode,
        texture_tracker=texture_tracker,
    )
    scene.add_geometry(road, node_name="road_slab")

    sidewalk_color = list(colors.get("sidewalk", (165, 168, 172, 255)))
    furnishing_color = list(colors.get("furnishing", tuple(sidewalk_color)))
    clear_color = list(colors.get("clear_path", tuple(sidewalk_color)))

    # Sidewalk top at Y = SIDEWALK_ELEVATION_M; slab is 0.08 m thick
    sw_y_translation = SIDEWALK_ELEVATION_M - 0.04  # centre of 0.08-thick slab

    if street_program is not None and getattr(street_program, "bands", None):
        left_offset = road_width_m / 2.0
        right_offset = road_width_m / 2.0
        for band in getattr(street_program, "bands", ()) or ():
            if getattr(band, "kind", "") == "carriageway":
                continue
            width_m = float(getattr(band, "width_m", 0.0) or 0.0)
            if width_m <= 0.0:
                continue
            band_kind = str(getattr(band, "kind", "") or "")
            color = clear_color if band_kind == "clear_path" else furnishing_color
            slab = trimesh.creation.box(extents=(length_m, 0.08, width_m))
            if getattr(band, "side", "") == "left":
                slab.apply_translation([0.0, sw_y_translation, left_offset + width_m / 2.0])
                left_offset += width_m
            elif getattr(band, "side", "") == "right":
                slab.apply_translation([0.0, sw_y_translation, -right_offset - width_m / 2.0])
                right_offset += width_m
            else:
                continue
            r_key = "clear_path" if band_kind == "clear_path" else "furnishing"
            slab = _apply_surface_finish(
                slab,
                surface_role=r_key,
                rgba=color,
                roughness=(roughness or {}).get(r_key, 0.70),
                texture_mode=texture_mode,
                texture_tracker=texture_tracker,
            )
            scene.add_geometry(slab, node_name=f"sidewalk_{getattr(band, 'name', 'band')}")
    else:
        if left_side_width_m > 0.0:
            sidewalk_left = trimesh.creation.box(extents=(length_m, 0.08, left_side_width_m))
            sidewalk_left.apply_translation([0.0, sw_y_translation, road_width_m / 2.0 + left_side_width_m / 2.0])
            sidewalk_left = _apply_surface_finish(
                sidewalk_left,
                surface_role="sidewalk",
                rgba=sidewalk_color,
                roughness=(roughness or {}).get("sidewalk", 0.70),
                texture_mode=texture_mode,
                texture_tracker=texture_tracker,
            )
            scene.add_geometry(sidewalk_left, node_name="sidewalk_left")

        if right_side_width_m > 0.0:
            sidewalk_right = trimesh.creation.box(extents=(length_m, 0.08, right_side_width_m))
            sidewalk_right.apply_translation([0.0, sw_y_translation, -road_width_m / 2.0 - right_side_width_m / 2.0])
            sidewalk_right = _apply_surface_finish(
                sidewalk_right,
                surface_role="sidewalk",
                rgba=sidewalk_color,
                roughness=(roughness or {}).get("sidewalk", 0.70),
                texture_mode=texture_mode,
                texture_tracker=texture_tracker,
            )
            scene.add_geometry(sidewalk_right, node_name="sidewalk_right")

    # Curb stones along road edges
    curb_color = list(colors.get("curb", (145, 145, 145, 255)))
    curb_height = SIDEWALK_ELEVATION_M
    curb_width = 0.12
    for side_name, z_sign in (("left", 1.0), ("right", -1.0)):
        curb = trimesh.creation.box(extents=(length_m, curb_height, curb_width))
        curb.apply_translation([0.0, curb_height / 2.0, z_sign * (road_width_m / 2.0 + curb_width / 2.0)])
        curb = _apply_surface_finish(
            curb,
            surface_role="curb",
            rgba=curb_color,
            roughness=(roughness or {}).get("curb", 0.40),
            texture_mode=texture_mode,
            texture_tracker=texture_tracker,
        )
        scene.add_geometry(curb, node_name=f"curb_{side_name}")

    return scene


def _apply_ground_pose(mesh, *, x_m: float, z_m: float, yaw_deg: float) -> None:
    trimesh = _require_trimesh()
    rotation = trimesh.transformations.rotation_matrix(
        math.radians(float(yaw_deg)),
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0],
    )
    mesh.apply_transform(rotation)
    mesh.apply_translation([float(x_m), 0.0, float(z_m)])


SIDEWALK_ELEVATION_M = 0.15


def _apply_pbr_material(mesh, rgba, roughness=0.9):
    """Apply a PBR material to a mesh instead of plain face colors."""
    trimesh = _require_trimesh()
    from trimesh.visual.material import PBRMaterial

    mat = PBRMaterial(
        baseColorFactor=[rgba[0] / 255.0, rgba[1] / 255.0, rgba[2] / 255.0, rgba[3] / 255.0],
        metallicFactor=0.0,
        roughnessFactor=float(roughness),
    )
    mesh.visual = trimesh.visual.TextureVisuals(material=mat)
    return mesh


def _apply_surface_finish(
    mesh,
    *,
    surface_role: str,
    rgba: Sequence[int],
    roughness: float,
    texture_mode: str,
    texture_tracker=None,
):
    return apply_default_scene_texture(
        mesh,
        surface_role=str(surface_role),
        tint_rgba=list(rgba),
        roughness=float(roughness),
        texture_mode=str(texture_mode),
        tracker=texture_tracker,
    )


def _road_pose_from_context(placement_ctx: object | None, fallback_length_m: float) -> Tuple[float, float, float, float]:
    road_reference = getattr(placement_ctx, "road_reference", None)
    coords = list(getattr(road_reference, "coords", []) or [])
    if len(coords) >= 2:
        start_x, start_z = float(coords[0][0]), float(coords[0][1])
        end_x, end_z = float(coords[-1][0]), float(coords[-1][1])
        dx = end_x - start_x
        dz = end_z - start_z
        seg_length = math.hypot(dx, dz)
        if seg_length > 1e-6:
            return (
                (start_x + end_x) / 2.0,
                (start_z + end_z) / 2.0,
                math.degrees(math.atan2(dz, dx)),
                max(float(fallback_length_m), float(seg_length)),
            )
    return (0.0, 0.0, 0.0, float(fallback_length_m))


def _add_road_box(
    scene,
    *,
    length_m: float,
    width_m: float,
    height_m: float,
    local_x_m: float,
    local_z_m: float,
    road_center_x_m: float,
    road_center_z_m: float,
    road_yaw_deg: float,
    y_min_m: float,
    color: Sequence[int],
    surface_role: str,
    node_name: str,
    roughness: float = 0.7,
    texture_mode: str = "topdown_tiles_v1",
    texture_tracker=None,
) -> None:
    trimesh = _require_trimesh()
    mesh = trimesh.creation.box(extents=(float(length_m), float(height_m), float(width_m)))
    mesh.apply_translation([float(local_x_m), float(y_min_m) + float(height_m) / 2.0, float(local_z_m)])
    _apply_ground_pose(mesh, x_m=road_center_x_m, z_m=road_center_z_m, yaw_deg=road_yaw_deg)
    mesh = _apply_surface_finish(
        mesh,
        surface_role=surface_role,
        rgba=list(color),
        roughness=float(roughness),
        texture_mode=texture_mode,
        texture_tracker=texture_tracker,
    )
    scene.add_geometry(mesh, node_name=node_name)


def _add_beauty_scene_proxies(
    scene,
    *,
    config: StreetComposeConfig,
    street_program: object,
    placement_ctx: object | None,
    poi_ctx: object | None,
    placements: List[StreetPlacement],
    texture_mode: str = "topdown_tiles_v1",
    texture_tracker=None,
) -> None:
    colors = style_palette(getattr(config, "style_preset", None))
    rough = surface_roughness(getattr(config, "style_preset", None))
    road_center_x_m, road_center_z_m, road_yaw_deg, road_length_m = _road_pose_from_context(
        placement_ctx,
        float(config.length_m),
    )
    road_width_m = float(getattr(street_program, "road_width_m", config.road_width_m))
    lane_count = max(1, int(getattr(street_program, "lane_count", config.lane_count)))
    render_linear_road_overlays = str(getattr(config, "layout_mode", "template")).strip().lower() != "osm"

    if render_linear_road_overlays:
        if lane_count > 1:
            lane_width_m = road_width_m / float(lane_count)
            dash_length_m = 2.2
            dash_gap_m = 3.8
            dash_x = -road_length_m / 2.0 + 2.5
            dash_idx = 0
            while dash_x < road_length_m / 2.0 - 1.5:
                for lane_idx in range(1, lane_count):
                    lane_z = -road_width_m / 2.0 + lane_width_m * float(lane_idx)
                    _add_road_box(
                        scene,
                        length_m=dash_length_m,
                        width_m=0.14,
                        height_m=0.01,
                        local_x_m=dash_x,
                        local_z_m=lane_z,
                        road_center_x_m=road_center_x_m,
                        road_center_z_m=road_center_z_m,
                        road_yaw_deg=road_yaw_deg,
                        y_min_m=0.004,
                        color=colors.get("lane_mark", (238, 232, 208, 255)),
                        surface_role="lane_mark",
                        node_name=f"lane_mark_{lane_idx}_{dash_idx}",
                        roughness=rough.get("lane_mark", 0.30),
                        texture_mode=texture_mode,
                        texture_tracker=texture_tracker,
                    )
                dash_idx += 1
                dash_x += dash_length_m + dash_gap_m

        curb_half_width = road_width_m / 2.0
        # Curb geometry is now part of _build_base_scene; skip duplicate here.

        crossing_points = nonempty_poi_points(getattr(poi_ctx, "poi_points_by_type_xz", {}) or {}).get("crossing", ())
        for idx, point in enumerate(crossing_points):
            _add_road_box(
                scene,
                length_m=1.8,
                width_m=max(road_width_m + 0.35, 4.0),
                height_m=0.012,
                local_x_m=0.0,
                local_z_m=0.0,
                road_center_x_m=float(point[0]),
                road_center_z_m=float(point[1]),
                road_yaw_deg=road_yaw_deg,
                y_min_m=0.004,
                color=colors.get("crossing", (236, 228, 208, 255)),
                surface_role="crossing",
                node_name=f"crossing_patch_{idx}",
                roughness=rough.get("crossing", 0.35),
                texture_mode=texture_mode,
                texture_tracker=texture_tracker,
            )

    for idx, placement in enumerate(placements):
        x_m = float(placement.position_xyz[0])
        z_m = float(placement.position_xyz[2])
        if placement.category == "tree":
            _add_road_box(
                scene,
                length_m=1.2,
                width_m=1.2,
                height_m=0.03,
                local_x_m=0.0,
                local_z_m=0.0,
                road_center_x_m=x_m,
                road_center_z_m=z_m,
                road_yaw_deg=0.0,
                y_min_m=SIDEWALK_ELEVATION_M + 0.001,
                color=colors.get("tree_pit", (98, 93, 76, 255)),
                surface_role="tree_pit",
                node_name=f"tree_pit_{idx}",
                roughness=rough.get("tree_pit", 0.90),
                texture_mode=texture_mode,
                texture_tracker=texture_tracker,
            )
        elif placement.category == "bus_stop":
            _add_road_box(
                scene,
                length_m=4.5,
                width_m=1.6,
                height_m=0.018,
                local_x_m=0.0,
                local_z_m=0.0,
                road_center_x_m=x_m,
                road_center_z_m=z_m,
                road_yaw_deg=road_yaw_deg,
                y_min_m=SIDEWALK_ELEVATION_M + 0.004,
                color=colors.get("transit_pad", (118, 129, 145, 255)),
                surface_role="transit_pad",
                node_name=f"transit_pad_{idx}",
                roughness=rough.get("transit_pad", 0.50),
                texture_mode=texture_mode,
                texture_tracker=texture_tracker,
            )


def _add_instance_meshes(
    scene,
    placements: List[StreetPlacement],
    mesh_cache: Dict[str, _MeshCacheEntry],
) -> None:
    trimesh = _require_trimesh()
    for placement in placements:
        entry = mesh_cache[placement.asset_id]
        mesh_or_scene = entry.mesh.copy()
        if placement.scale_xyz:
            scale = [float(value) for value in placement.scale_xyz]
        else:
            scale = float(placement.scale)
        rotation = trimesh.transformations.rotation_matrix(
            math.radians(float(placement.yaw_deg)),
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0],
        )
        # Street furniture sits on the elevated sidewalk; buildings stay at ground.
        y_offset = SIDEWALK_ELEVATION_M if placement.placement_group != "building" else 0.0
        translation = trimesh.transformations.translation_matrix(
            [
                float(placement.position_xyz[0]),
                float(placement.position_xyz[1]) + y_offset,
                float(placement.position_xyz[2]),
            ]
        )
        if isinstance(mesh_or_scene, trimesh.Scene):
            # Scene-type entries (e.g. parametric trees with PBR materials):
            # apply transforms, then add each sub-geometry individually.
            if isinstance(scale, list):
                mesh_or_scene.apply_scale(scale)
            else:
                mesh_or_scene.apply_scale(float(scale))
            mesh_or_scene.apply_transform(rotation)
            mesh_or_scene.apply_transform(translation)
            for gidx, node_name in enumerate(mesh_or_scene.graph.nodes_geometry):
                transform, geom_name = mesh_or_scene.graph[node_name]
                geom = mesh_or_scene.geometry[geom_name]
                placed = geom.copy()
                placed.apply_transform(transform)
                scene.add_geometry(placed, node_name=f"{placement.instance_id}_{geom_name}_{gidx}")
        else:
            if isinstance(scale, list):
                mesh_or_scene.apply_scale(scale)
            else:
                mesh_or_scene.apply_scale(float(scale))
            mesh_or_scene.apply_transform(rotation)
            mesh_or_scene.apply_transform(translation)
            scene.add_geometry(mesh_or_scene, node_name=placement.instance_id)


def _export_scene(scene, out_dir: Path, export_format: str) -> Dict[str, str]:
    export_format = _validate_export_format(export_format)
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = {"scene_glb": "", "scene_ply": ""}
    if export_format in {"glb", "both"}:
        glb_path = (out_dir / "scene.glb").resolve()
        scene.export(glb_path)
        outputs["scene_glb"] = str(glb_path)
    if export_format in {"ply", "both"}:
        ply_path = (out_dir / "scene.ply").resolve()
        scene_mesh = scene.to_geometry()
        scene_mesh.export(ply_path)
        outputs["scene_ply"] = str(ply_path)
    return outputs


def _production_step_definitions(layout_mode: str) -> Tuple[Tuple[str, str], ...]:
    if str(layout_mode).strip().lower() == "osm":
        return (
            ("road_base", "Road Base"),
            ("land_use_zoning", "Land Use / Zoning"),
            ("buildings", "Buildings"),
            ("poi_context", "POI Context"),
            ("furniture_anchor", "Furniture Anchor"),
            ("furniture_required", "Furniture Required"),
            ("furniture_optional", "Furniture Optional"),
            ("scene_preview", "Scene Preview"),
        )
    return (
        ("road_base", "Road Base"),
        ("furniture_required", "Furniture Required"),
        ("furniture_optional", "Furniture Optional"),
        ("scene_preview", "Scene Preview"),
    )


def _split_furniture_layers(
    placements: Sequence[StreetPlacement],
) -> Tuple[List[StreetPlacement], List[StreetPlacement], List[StreetPlacement], List[StreetPlacement]]:
    building = [placement for placement in placements if placement.placement_group == "building"]
    anchor = [
        placement
        for placement in placements
        if placement.placement_group == "street_furniture" and str(placement.anchor_poi_type or "").strip()
    ]
    required = [
        placement
        for placement in placements
        if placement.placement_group == "street_furniture"
        and bool(placement.required)
        and not str(placement.anchor_poi_type or "").strip()
    ]
    optional = [
        placement
        for placement in placements
        if placement.placement_group == "street_furniture"
        and not bool(placement.required)
        and not str(placement.anchor_poi_type or "").strip()
    ]
    return building, anchor, required, optional


def _stage_counts(
    *,
    visible_instance_ids: Sequence[str],
    visible_placements: Sequence[StreetPlacement],
    zoning_grid: Sequence[Dict[str, object]],
    building_plans: Sequence[BuildingPlacementPlan],
    poi_points_by_type: Mapping[str, Sequence[Tuple[float, float]]],
) -> Dict[str, int]:
    building_count = sum(1 for placement in visible_placements if placement.placement_group == "building")
    furniture_anchor_count = sum(
        1
        for placement in visible_placements
        if placement.placement_group == "street_furniture" and str(placement.anchor_poi_type or "").strip()
    )
    furniture_required_count = sum(
        1
        for placement in visible_placements
        if placement.placement_group == "street_furniture"
        and bool(placement.required)
        and not str(placement.anchor_poi_type or "").strip()
    )
    furniture_optional_count = sum(
        1
        for placement in visible_placements
        if placement.placement_group == "street_furniture"
        and not bool(placement.required)
        and not str(placement.anchor_poi_type or "").strip()
    )
    poi_count = sum(len(points) for points in nonempty_poi_points(poi_points_by_type).values())
    return {
        "visible_instance_count": int(len(visible_instance_ids)),
        "building_count": int(building_count),
        "building_target_count": int(len(building_plans)),
        "furniture_anchor_count": int(furniture_anchor_count),
        "furniture_required_count": int(furniture_required_count),
        "furniture_optional_count": int(furniture_optional_count),
        "street_furniture_count": int(furniture_anchor_count + furniture_required_count + furniture_optional_count),
        "zoning_cell_count": int(len(zoning_grid)),
        "poi_point_count": int(poi_count),
    }


def _stage_summary_text(record: ProductionStepRecord) -> str:
    counts = record.counts
    return (
        f"{record.index + 1}. {record.title}\n"
        f"- step_id: {record.step_id}\n"
        f"- visible_instances: {int(counts.get('visible_instance_count', 0))}\n"
        f"- buildings: {int(counts.get('building_count', 0))}\n"
        f"- anchor_furniture: {int(counts.get('furniture_anchor_count', 0))}\n"
        f"- required_furniture: {int(counts.get('furniture_required_count', 0))}\n"
        f"- optional_furniture: {int(counts.get('furniture_optional_count', 0))}\n"
        f"- poi_points: {int(counts.get('poi_point_count', 0))}\n"
        f"- zoning_cells: {int(counts.get('zoning_cell_count', 0))}"
    )


def _stage_scene_base(
    *,
    config: StreetComposeConfig,
    resolved_program: object,
    placement_ctx: object | None,
    palette: Mapping[str, Tuple[int, int, int, int]],
    roughness: Optional[Dict[str, float]] = None,
    texture_tracker=None,
):
    if config.layout_mode == "osm" and placement_ctx is not None:
        return _build_osm_base_scene(
            placement_ctx,
            palette=palette,
            roughness=roughness,
            texture_mode=str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")),
            texture_tracker=texture_tracker,
        )
    left_side_width = sum(float(band.width_m) for band in resolved_program.bands if band.side == "left")
    right_side_width = sum(float(band.width_m) for band in resolved_program.bands if band.side == "right")
    return _build_base_scene(
        length_m=float(config.length_m),
        road_width_m=float(resolved_program.road_width_m),
        left_side_width_m=float(left_side_width),
        right_side_width_m=float(right_side_width),
        street_program=resolved_program,
        palette=palette,
        roughness=roughness,
        texture_mode=str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")),
        texture_tracker=texture_tracker,
    )


def _add_polygon_slab(
    scene,
    *,
    polygon_xz: Sequence[Sequence[float]],
    height_m: float,
    y_min_m: float,
    color: Sequence[int],
    surface_role: str,
    roughness: float,
    texture_mode: str,
    node_name: str,
    texture_tracker=None,
) -> None:
    if len(polygon_xz) < 3:
        return
    trimesh = _require_trimesh()
    try:
        from shapely.geometry import Polygon as ShapelyPolygon
    except Exception:
        ShapelyPolygon = None  # type: ignore[assignment]

    if ShapelyPolygon is not None:
        try:
            poly = ShapelyPolygon([(float(point[0]), float(point[1])) for point in polygon_xz])
            mesh = trimesh.creation.extrude_polygon(poly, float(height_m))
            verts = mesh.vertices.copy()
            old_y = verts[:, 1].copy()
            old_z = verts[:, 2].copy()
            verts[:, 1] = old_z + float(y_min_m)
            verts[:, 2] = old_y
            mesh.vertices = verts
            mesh.fix_normals()
            mesh = _apply_surface_finish(
                mesh,
                surface_role=surface_role,
                rgba=list(color),
                roughness=float(roughness),
                texture_mode=texture_mode,
                texture_tracker=texture_tracker,
            )
            scene.add_geometry(mesh, node_name=node_name)
            return
        except Exception:
            logger.debug("Falling back to bbox zoning slab for %s", node_name)

    xs = [float(point[0]) for point in polygon_xz]
    zs = [float(point[1]) for point in polygon_xz]
    if not xs or not zs:
        return
    length_m = max(max(xs) - min(xs), 0.1)
    width_m = max(max(zs) - min(zs), 0.1)
    mesh = trimesh.creation.box(extents=(length_m, float(height_m), width_m))
    mesh.visual.face_colors = list(color)
    mesh.apply_translation(
        [
            float((min(xs) + max(xs)) / 2.0),
            float(y_min_m) + float(height_m) / 2.0,
            float((min(zs) + max(zs)) / 2.0),
        ]
    )
    mesh = _apply_surface_finish(
        mesh,
        surface_role=surface_role,
        rgba=list(color),
        roughness=float(roughness),
        texture_mode=texture_mode,
        texture_tracker=texture_tracker,
    )
    scene.add_geometry(mesh, node_name=node_name)


def _zoning_proxy_color(cell: Mapping[str, object]) -> Tuple[int, int, int, int]:
    lane_role = str(cell.get("lane_role", "") or "")
    land_use_type = str(cell.get("land_use_type", "") or "")
    if lane_role == "carriageway":
        return (85, 90, 96, 220)
    if "sidewalk" in lane_role:
        return (196, 199, 204, 220)
    if lane_role.startswith("left_building_buffer") or lane_role.startswith("right_building_buffer"):
        return {
            "commercial": (224, 122, 95, 220),
            "transit": (77, 150, 255, 220),
            "residential": (127, 176, 105, 220),
            "green": (42, 157, 143, 220),
        }.get(land_use_type, (168, 164, 158, 220))
    return (170, 170, 170, 220)


def _zoning_proxy_surface_role(cell: Mapping[str, object]) -> str:
    lane_role = str(cell.get("lane_role", "") or "")
    land_use_type = str(cell.get("land_use_type", "") or "")
    if lane_role == "carriageway":
        return "carriageway"
    if "sidewalk" in lane_role:
        return "clear_path"
    if lane_role.startswith("left_building_buffer") or lane_role.startswith("right_building_buffer"):
        if land_use_type == "green":
            return "grass"
        return "building_buffer"
    return "furnishing"


def _add_zoning_proxies(
    scene,
    zoning_grid: Sequence[Dict[str, object]],
    *,
    roughness: Optional[Dict[str, float]] = None,
    texture_mode: str = "topdown_tiles_v1",
    texture_tracker=None,
) -> None:
    for idx, cell in enumerate(zoning_grid):
        polygon_xz = cell.get("polygon_xz", []) or []
        if not polygon_xz:
            continue
        surface_role = _zoning_proxy_surface_role(cell)
        _add_polygon_slab(
            scene,
            polygon_xz=polygon_xz,
            height_m=0.04 if str(cell.get("lane_role", "") or "") == "carriageway" else 0.08,
            y_min_m=0.01,
            color=_zoning_proxy_color(cell),
            surface_role=surface_role,
            roughness=(roughness or {}).get(surface_role, 0.70),
            texture_mode=texture_mode,
            node_name=f"zoning_proxy_{idx:03d}",
            texture_tracker=texture_tracker,
        )


def _save_stage_companion_figure(fig: object | None, out_path: Path) -> str:
    if fig is None:
        return ""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fig.savefig(out_path, dpi=180, bbox_inches="tight", facecolor="white")
        try:
            import matplotlib.pyplot as plt

            plt.close(fig)
        except Exception:
            pass
        return str(out_path)
    except Exception:
        try:
            import matplotlib.pyplot as plt

            plt.close(fig)
        except Exception:
            pass
        logger.debug("Failed to save companion figure: %s", out_path)
        return ""


def _build_poi_companion_figure(
    *,
    spatial_ctx: object | None,
    placements: Sequence[StreetPlacement],
    config: StreetComposeConfig,
    osm_geometry: Mapping[str, object] | None,
    exclusion_zones: Sequence[object],
):
    if spatial_ctx is None:
        return None
    if not exclusion_zones and not nonempty_poi_points(getattr(spatial_ctx, "poi_points_by_type_xz", {}) or {}):
        return None
    zones = [
        {
            "poi_type": getattr(zone, "poi_type", ""),
            "position_xz": [float(getattr(zone, "position_xz", (0.0, 0.0))[0]), float(getattr(zone, "position_xz", (0.0, 0.0))[1])],
            "radius_m": float(getattr(zone, "radius_m", 0.0) or 0.0),
            "rule_name": str(getattr(zone, "rule_name", "")),
        }
        for zone in exclusion_zones
    ]
    return plot_poi_exclusion_overview(
        spatial_ctx,
        placements,
        config,
        poi_exclusion_zones=zones,
        poi_conflicts=[],
        osm_geometry=osm_geometry,
    )


def _build_production_steps(
    *,
    out_dir: Path,
    config: StreetComposeConfig,
    resolved_program: object,
    placement_ctx: object | None,
    poi_ctx: object | None,
    spatial_ctx: object | None,
    placements: Sequence[StreetPlacement],
    zoning_grid: Sequence[Dict[str, object]],
    building_footprints: Sequence[BuildingFootprint],
    generated_lots: Sequence[GeneratedLot],
    building_plans: Sequence[BuildingPlacementPlan],
    mesh_cache: Mapping[str, _MeshCacheEntry],
    exclusion_zones: Sequence[object],
    palette: Mapping[str, Tuple[int, int, int, int]],
    osm_geometry: Mapping[str, object] | None,
    overall_texture_tracker=None,
) -> Tuple[ProductionStepRecord, ...]:
    step_dir = (out_dir / "production_steps").resolve()
    step_dir.mkdir(parents=True, exist_ok=True)
    building_placements, anchor_placements, required_placements, optional_placements = _split_furniture_layers(placements)
    poi_points_by_type = extract_poi_points_by_type(poi_ctx, suffix="xz") if poi_ctx is not None else {}
    rough = surface_roughness(getattr(config, "style_preset", None))

    stage_visibility: Dict[str, Tuple[bool, bool, Tuple[StreetPlacement, ...], Tuple[str, ...]]] = {}
    if str(config.layout_mode).strip().lower() == "osm":
        stage_visibility = {
            "road_base": (False, False, tuple(), tuple()),
            "land_use_zoning": (True, False, tuple(), tuple()),
            "buildings": (True, False, tuple(building_placements), tuple(placement.instance_id for placement in building_placements)),
            "poi_context": (True, True, tuple(building_placements), tuple()),
            "furniture_anchor": (
                True,
                True,
                tuple(list(building_placements) + list(anchor_placements)),
                tuple(placement.instance_id for placement in anchor_placements),
            ),
            "furniture_required": (
                True,
                True,
                tuple(list(building_placements) + list(anchor_placements) + list(required_placements)),
                tuple(placement.instance_id for placement in required_placements),
            ),
            "furniture_optional": (
                True,
                True,
                tuple(list(building_placements) + list(anchor_placements) + list(required_placements) + list(optional_placements)),
                tuple(placement.instance_id for placement in optional_placements),
            ),
            "scene_preview": (
                False,
                False,
                tuple(list(building_placements) + list(anchor_placements) + list(required_placements) + list(optional_placements)),
                tuple(),
            ),
        }
    else:
        non_optional = list(anchor_placements) + list(required_placements)
        all_placements = non_optional + list(optional_placements)
        stage_visibility = {
            "road_base": (False, False, tuple(), tuple()),
            "furniture_required": (
                False,
                False,
                tuple(non_optional),
                tuple(placement.instance_id for placement in non_optional),
            ),
            "furniture_optional": (
                False,
                False,
                tuple(all_placements),
                tuple(placement.instance_id for placement in optional_placements),
            ),
            "scene_preview": (
                False,
                False,
                tuple(all_placements),
                tuple(),
            ),
        }

    records: List[ProductionStepRecord] = []
    for index, (step_id, title) in enumerate(_production_step_definitions(config.layout_mode)):
        include_zoning, include_poi_overlays, visible_placements, delta_ids = stage_visibility[step_id]
        step_texture_tracker = create_scene_texture_tracker(str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")))
        scene = _stage_scene_base(
            config=config,
            resolved_program=resolved_program,
            placement_ctx=placement_ctx,
            palette=palette,
            roughness=rough,
            texture_tracker=step_texture_tracker,
        )
        _add_beauty_scene_proxies(
            scene,
            config=config,
            street_program=resolved_program,
            placement_ctx=placement_ctx,
            poi_ctx=poi_ctx,
            placements=list(visible_placements),
            texture_mode=str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")),
            texture_tracker=step_texture_tracker,
        )
        if include_zoning:
            _add_zoning_proxies(
                scene,
                zoning_grid,
                roughness=rough,
                texture_mode=str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")),
                texture_tracker=step_texture_tracker,
            )
        if visible_placements:
            _add_instance_meshes(scene=scene, placements=list(visible_placements), mesh_cache=dict(mesh_cache))
        if include_poi_overlays:
            _add_poi_markers_and_zones(scene, extract_poi_points_by_type(poi_ctx, suffix="xz") if poi_ctx is not None else {}, exclusion_zones)

        glb_path = (step_dir / f"{index:02d}_{step_id}.glb").resolve()
        scene.export(glb_path)

        companion_path = ""
        if step_id == "land_use_zoning":
            try:
                from .topdown_render import render_design_zoning_companion

                companion_path = str(
                    render_design_zoning_companion(
                        out_path=step_dir / f"{index:02d}_{step_id}.png",
                        config=config,
                        palette=palette,
                        zoning_grid=zoning_grid,
                        building_footprints=building_footprints,
                        generated_lots=generated_lots,
                        osm_geometry=osm_geometry,
                    )
                    or ""
                )
            except Exception:
                companion_path = ""
            if not str(companion_path).strip():
                companion = plot_zoning_grid_preview_2d(
                    zoning_grid,
                    building_footprints=[],
                    generated_lots=[],
                    building_placements=[],
                    osm_geometry=osm_geometry,
                )
                companion_path = _save_stage_companion_figure(companion, step_dir / f"{index:02d}_{step_id}.png")
        elif step_id == "poi_context":
            companion = _build_poi_companion_figure(
                spatial_ctx=spatial_ctx,
                placements=visible_placements,
                config=config,
                osm_geometry=osm_geometry,
                exclusion_zones=exclusion_zones,
            )
            companion_path = _save_stage_companion_figure(companion, step_dir / f"{index:02d}_{step_id}.png")

        visible_ids = tuple(placement.instance_id for placement in visible_placements)
        counts = _stage_counts(
            visible_instance_ids=visible_ids,
            visible_placements=visible_placements,
            zoning_grid=zoning_grid if include_zoning else tuple(),
            building_plans=building_plans if step_id in {"buildings", "poi_context", "furniture_anchor", "furniture_required", "furniture_optional", "scene_preview"} else tuple(),
            poi_points_by_type=poi_points_by_type if include_poi_overlays else {},
        )
        if overall_texture_tracker is not None:
            overall_texture_tracker.merge(step_texture_tracker)
        records.append(
            ProductionStepRecord(
                step_id=step_id,
                index=index,
                title=title,
                glb_path=str(glb_path),
                companion_path=str(companion_path),
                scene_texture_mode=str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")),
                textured_base_enabled=bool(step_texture_tracker.textured_geometry_count > 0),
                visible_instance_ids=visible_ids,
                delta_instance_ids=tuple(delta_ids),
                counts=counts,
            )
        )

    manifest_path = (step_dir / "production_steps.json").resolve()
    manifest_path.write_text(
        json.dumps([record.to_dict() for record in records], indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    return tuple(records)


# ---------------------------------------------------------------------------
# M5: OSM pose sampling and scene building
# ---------------------------------------------------------------------------

def _sample_pose_osm(
    category: str,
    placement_ctx: object,
    rng: random.Random,
    anchor_position_xz: Optional[Tuple[float, float]] = None,
) -> Optional[Tuple[float, float, float]]:
    """Sample a (x, z, yaw_deg) pose inside the sidewalk zone of *placement_ctx*."""
    from .placement_zones import compute_facing_angle, sample_slot_on_sidewalk

    if anchor_position_xz is not None:
        point = (float(anchor_position_xz[0]), float(anchor_position_xz[1]))
    else:
        side_pref = SIDE_PREF.get(category, "both")
        overall_zone = placement_ctx.sidewalk_zone  # type: ignore[attr-defined]
        if side_pref == "left":
            preferred_zone = getattr(placement_ctx, "left_sidewalk_zone", None)
        elif side_pref == "right":
            preferred_zone = getattr(placement_ctx, "right_sidewalk_zone", None)
        else:
            preferred_zone = overall_zone
        zone = preferred_zone
        if zone is None or getattr(zone, "is_empty", False):
            zone = overall_zone
        point = sample_slot_on_sidewalk(zone, rng)
        if point is None and zone is not overall_zone:
            point = sample_slot_on_sidewalk(overall_zone, rng)
    if point is None:
        return None
    x, z = point
    yaw = _yaw_for_asset_category(
        category,
        compute_facing_angle(point, placement_ctx.carriageway),  # type: ignore[attr-defined]
    )
    return x, z, yaw


def _build_osm_base_scene(
    placement_ctx: object,
    *,
    palette: Optional[Dict[str, Tuple[int, int, int, int]]] = None,
    roughness: Optional[Dict[str, float]] = None,
    texture_mode: str = "topdown_tiles_v1",
    texture_tracker=None,
):
    """Build a trimesh Scene with carriageway + sidewalk extruded slabs from OSM geometry."""
    trimesh = _require_trimesh()
    scene = trimesh.Scene()

    carriageway = placement_ctx.carriageway  # type: ignore[attr-defined]
    sidewalk_zone = placement_ctx.sidewalk_zone  # type: ignore[attr-defined]
    colors = palette or {}

    scene_bounds: List[Tuple[float, float, float, float]] = []
    for geom in (carriageway, sidewalk_zone):
        if geom is None or getattr(geom, "is_empty", True):
            continue
        bounds = getattr(geom, "bounds", None)
        if bounds is None or len(bounds) != 4:
            continue
        scene_bounds.append(tuple(float(value) for value in bounds))

    if scene_bounds:
        min_x = min(bounds[0] for bounds in scene_bounds)
        min_z = min(bounds[1] for bounds in scene_bounds)
        max_x = max(bounds[2] for bounds in scene_bounds)
        max_z = max(bounds[3] for bounds in scene_bounds)
        pad_m = 12.0
        ground = trimesh.creation.box(
            extents=(max(max_x - min_x + pad_m * 2.0, 20.0), 0.04, max(max_z - min_z + pad_m * 2.0, 20.0))
        )
        ground_color = list(colors.get("context_ground", (168, 163, 150, 255)))
        ground.apply_translation(
            [
                float((min_x + max_x) / 2.0),
                -0.10,
                float((min_z + max_z) / 2.0),
            ]
        )
        ground = _apply_surface_finish(
            ground,
            surface_role="context_ground",
            rgba=ground_color,
            roughness=(roughness or {}).get("context_ground", 0.85),
            texture_mode=texture_mode,
            texture_tracker=texture_tracker,
        )
        scene.add_geometry(ground, node_name="context_ground")

    def _extrude_polygon(
        geom,
        height: float,
        color,
        name_prefix: str,
        *,
        y_offset: float = 0.0,
        roughness_key: str = "",
        surface_role: str = "",
    ) -> None:
        """Extrude a shapely geometry into a thin 3D slab and add to scene.

        ``extrude_polygon`` maps the 2-D polygon (x_east, y_north) to mesh
        (X, Y) and extrudes along Z (0 ... height).  The scene convention is
        **Y-up** (XZ = ground), so we swap Y<->Z after extrusion:
            X_3d = x_east,  Y_3d = z_extrude - height + y_offset,  Z_3d = y_north
        This puts the top surface at Y = y_offset with the road lying flat on XZ.
        """
        from shapely.geometry import MultiPolygon, Polygon as ShapelyPolygon
        polygons = []
        if isinstance(geom, ShapelyPolygon):
            polygons = [geom]
        elif isinstance(geom, MultiPolygon):
            polygons = list(geom.geoms)
        for idx, poly in enumerate(polygons):
            if poly.is_empty:
                continue
            try:
                mesh = trimesh.creation.extrude_polygon(poly, height)
                # Swap Y<->Z so road lies flat on XZ ground plane (Y-up)
                verts = mesh.vertices.copy()
                old_y = verts[:, 1].copy()   # was northing
                old_z = verts[:, 2].copy()   # was extrusion 0..height
                verts[:, 1] = old_z - height + y_offset  # Y = extrusion shifted + offset
                verts[:, 2] = old_y           # Z = northing
                mesh.vertices = verts
                mesh.fix_normals()
                mesh = _apply_surface_finish(
                    mesh,
                    surface_role=surface_role or roughness_key or "sidewalk",
                    rgba=list(color),
                    roughness=(roughness or {}).get(roughness_key or surface_role or "sidewalk", 0.9),
                    texture_mode=texture_mode,
                    texture_tracker=texture_tracker,
                )
                scene.add_geometry(mesh, node_name=f"{name_prefix}_{idx}")
            except (ValueError, RuntimeError, IndexError):
                logger.debug("Skipping degenerate %s polygon %d", name_prefix, idx)
                continue

    if not carriageway.is_empty:
        _extrude_polygon(
            carriageway,
            0.06,
            list(colors.get("carriageway", (65, 68, 72, 255))),
            "carriageway",
            roughness_key="carriageway",
            surface_role="carriageway",
        )
    if not sidewalk_zone.is_empty:
        _extrude_polygon(
            sidewalk_zone, 0.08, list(colors.get("sidewalk", (165, 168, 172, 255))), "sidewalk",
            y_offset=SIDEWALK_ELEVATION_M, roughness_key="sidewalk", surface_role="sidewalk",
        )

    # Curb: thin ring around the carriageway edge, extruded to sidewalk elevation
    curb_width = 0.12
    curb_color = list(colors.get("curb", (145, 145, 145, 255)))
    if not carriageway.is_empty:
        try:
            curb_zone = carriageway.buffer(curb_width).difference(carriageway)
            if not curb_zone.is_empty:
                _extrude_polygon(
                    curb_zone, SIDEWALK_ELEVATION_M, curb_color, "curb",
                    y_offset=0.0, roughness_key="curb", surface_role="curb",
                )
        except Exception:
            logger.debug("Skipping curb geometry in OSM base scene")

    return scene


def _add_poi_markers_and_zones(scene, poi_points_by_type_or_exclusion_zones, exclusion_zones=None) -> None:
    """Add POI marker spheres and exclusion-zone rings to a trimesh Scene.

    Coordinate convention (Y-up): X_3d = x_east, Y_3d = height, Z_3d = y_north.
    """
    if exclusion_zones is None:
        poi_points_by_type = {}
        exclusion_zones = poi_points_by_type_or_exclusion_zones
    else:
        poi_points_by_type = poi_points_by_type_or_exclusion_zones
    normalized_points = nonempty_poi_points(poi_points_by_type)
    if not exclusion_zones and not normalized_points:
        return
    trimesh = _require_trimesh()
    from shapely.geometry import Point as ShapelyPoint

    _BASE_COLOR = [25, 25, 30, 255]
    _RING_COLOR = [255, 70, 70, 48]  # lighter translucent red

    seen_positions: dict = {}  # (poi_type, x, y) -> idx to avoid duplicate markers

    def _build_marker_mesh(poi_type: str):
        poi_type = canonicalize_poi_type(poi_type)
        if poi_type == "entrance":
            mesh = trimesh.creation.cone(radius=0.55, height=1.8, sections=24)
            mesh.apply_translation([0.0, 0.9, 0.0])
            return mesh
        if poi_type == CANONICAL_FIRE_POI:
            mesh = trimesh.creation.cylinder(radius=0.42, height=1.6, sections=24)
            mesh.apply_translation([0.0, 0.8, 0.0])
            return mesh
        if poi_type == "bus_stop":
            mesh = trimesh.creation.box(extents=(0.95, 2.2, 0.38))
            mesh.apply_translation([0.0, 1.1, 0.0])
            return mesh
        if poi_type in {"crossing", "traffic_signals"}:
            mesh = trimesh.creation.box(extents=(0.8, 1.6, 0.18))
            mesh.apply_translation([0.0, 0.8, 0.0])
            return mesh
        if poi_type in {"parking_entrance", "subway_entrance"}:
            mesh = trimesh.creation.cone(radius=0.42, height=1.5, sections=18)
            mesh.apply_translation([0.0, 0.75, 0.0])
            return mesh
        if poi_type == "post_box":
            mesh = trimesh.creation.box(extents=(0.52, 1.2, 0.52))
            mesh.apply_translation([0.0, 0.6, 0.0])
            return mesh
        if poi_type == "waste_basket":
            mesh = trimesh.creation.cylinder(radius=0.35, height=0.9, sections=20)
            mesh.apply_translation([0.0, 0.45, 0.0])
            return mesh
        if poi_type == "bollard":
            mesh = trimesh.creation.cylinder(radius=0.18, height=1.0, sections=16)
            mesh.apply_translation([0.0, 0.5, 0.0])
            return mesh
        mesh = trimesh.creation.icosphere(subdivisions=2, radius=0.5)
        mesh.apply_translation([0.0, 0.5, 0.0])
        return mesh

    def _add_marker(poi_type: str, point: Tuple[float, float]) -> None:
        key = (poi_type, point[0], point[1])
        if key in seen_positions:
            return
        idx = len(seen_positions)
        seen_positions[key] = idx
        x_east, y_north = point
        marker = _build_marker_mesh(poi_type)
        color_hex = str(poi_plot_config(poi_type)["color"]).lstrip("#")
        marker.visual.face_colors = [
            int(color_hex[0:2], 16),
            int(color_hex[2:4], 16),
            int(color_hex[4:6], 16),
            255,
        ]
        marker.apply_translation([x_east, 0.0, y_north])
        scene.add_geometry(marker, node_name=f"poi_{poi_type}_{idx}")

        base = trimesh.creation.cylinder(radius=0.72, height=0.08, sections=24)
        base.visual.face_colors = _BASE_COLOR
        base.apply_translation([x_east, 0.04, y_north])
        scene.add_geometry(base, node_name=f"poi_base_{poi_type}_{idx}")

    for poi_type, points in normalized_points.items():
        for point in points:
            _add_marker(poi_type, point)

    for zone in exclusion_zones:
        key = (zone.poi_type, zone.position_xz[0], zone.position_xz[1])
        _add_marker(zone.poi_type, zone.position_xz)
        idx = seen_positions[key]
        # Exclusion zone ring (annulus via Shapely buffer difference)
        r = zone.radius_m
        if r < 0.15:
            continue
        inner_r = max(r - 0.08, 0.0)
        x_east, y_north = zone.position_xz
        ring_poly = ShapelyPoint(x_east, y_north).buffer(r).difference(
            ShapelyPoint(x_east, y_north).buffer(inner_r)
        )
        if ring_poly.is_empty:
            continue
        try:
            ring_mesh = trimesh.creation.extrude_polygon(ring_poly, 0.02)
            # Apply same Y↔Z swap as _extrude_polygon
            verts = ring_mesh.vertices.copy()
            old_y = verts[:, 1].copy()
            old_z = verts[:, 2].copy()
            verts[:, 1] = old_z + 0.01
            verts[:, 2] = old_y
            ring_mesh.vertices = verts
            ring_mesh.fix_normals()
            ring_mesh.visual.face_colors = _RING_COLOR
            scene.add_geometry(ring_mesh, node_name=f"exclusion_{zone.poi_type}_{idx}")
        except (ValueError, RuntimeError, IndexError):
            logger.debug("Skipping degenerate exclusion ring for %s", zone.rule_name)
            continue


def _should_embed_debug_scene_overlays(config: StreetComposeConfig) -> bool:
    # Keep exported GLB focused on presentation geometry; POI diagnostics remain in JSON and 2D plots.
    return False


def _serialize_osm_geometry(placement_ctx: object) -> dict:
    """Extract simplified polygon exterior rings for 2D visualization in layout JSON."""
    from shapely.geometry import MultiPolygon, Polygon as ShapelyPolygon

    def _extract_rings(geom, tolerance: float = 0.5, max_points: int = 200):
        polys: list = []
        if isinstance(geom, ShapelyPolygon):
            polys = [geom]
        elif isinstance(geom, MultiPolygon):
            polys = list(geom.geoms)
        rings: list = []
        for poly in polys:
            if poly.is_empty:
                continue
            simplified = poly.simplify(tolerance)
            coords = list(simplified.exterior.coords)
            if len(coords) > max_points:
                simplified = poly.simplify(tolerance * 2)
                coords = list(simplified.exterior.coords)
            rings.append([[round(c[0], 2), round(c[1], 2)] for c in coords])
        return rings

    result: dict = {}
    carriageway = placement_ctx.carriageway  # type: ignore[attr-defined]
    sidewalk = placement_ctx.sidewalk_zone  # type: ignore[attr-defined]
    if not carriageway.is_empty:
        result["carriageway_rings"] = _extract_rings(carriageway)
    if not sidewalk.is_empty:
        result["sidewalk_rings"] = _extract_rings(sidewalk)
    left_sidewalk = getattr(placement_ctx, "left_sidewalk_zone", None)
    right_sidewalk = getattr(placement_ctx, "right_sidewalk_zone", None)
    if left_sidewalk is not None and not left_sidewalk.is_empty:
        result["left_sidewalk_rings"] = _extract_rings(left_sidewalk)
    if right_sidewalk is not None and not right_sidewalk.is_empty:
        result["right_sidewalk_rings"] = _extract_rings(right_sidewalk)
    aoi = getattr(placement_ctx, "aoi_polygon", None)
    if aoi is not None and not aoi.is_empty:
        b = aoi.bounds  # (minx, miny, maxx, maxy)
        result["aoi_bbox_m"] = [round(v, 2) for v in b]
    return result


def _slot_spatial_kwargs(slot, spatial_ctx) -> dict:
    """Compute spatial distance fields for a PolicyFeatureContext."""
    if spatial_ctx is None:
        return {}
    sd = compute_slot_distances((float(slot.x_center_m), float(slot.z_center_m)), spatial_ctx)
    return {
        "dist_to_road_edge_m": sd.dist_to_road_edge_m,
        "dist_to_nearest_junction_m": sd.dist_to_nearest_junction_m,
        "dist_to_nearest_entrance_m": sd.dist_to_nearest_entrance_m,
    }


def _slot_placement_sort_key(slot: object) -> Tuple[int, int, str, float, float, str]:
    anchor_type = str(getattr(slot, "anchor_poi_type", "") or "").strip()
    if anchor_type:
        bucket = 0
        anchor_rank = placement_priority_rank(anchor_type)
    elif bool(getattr(slot, "required", False)):
        bucket = 1
        anchor_rank = 999
    else:
        bucket = 2
        anchor_rank = 999
    return (
        int(bucket),
        int(anchor_rank),
        str(getattr(slot, "theme_id", "") or ""),
        -float(getattr(slot, "priority", 0.0) or 0.0),
        float(getattr(slot, "x_center_m", 0.0) or 0.0),
        str(getattr(slot, "slot_id", "") or ""),
    )


def _placement_status(anchor_distance_m: Optional[float], *, required: bool, placed: bool) -> str:
    if not placed:
        return "unplaced_required" if required else "unplaced_optional"
    if anchor_distance_m is not None and anchor_distance_m >= 0.0:
        if anchor_distance_m <= 0.75:
            return "anchored_exact"
        return "anchored_relaxed"
    return "placed"


def _point_in_zone(zone: object | None, point_xz: Tuple[float, float], *, tolerance_m: float = 0.05) -> bool:
    if zone is None or getattr(zone, "is_empty", False):
        return False
    try:
        from shapely.geometry import Point as ShapelyPoint
    except Exception:
        return True
    point = ShapelyPoint(float(point_xz[0]), float(point_xz[1]))
    return bool(zone.buffer(float(tolerance_m)).contains(point))


def _point_side_matches_slot(
    point_xz: Tuple[float, float],
    *,
    slot_side: str,
    placement_ctx: object | None,
) -> Tuple[bool, bool]:
    if placement_ctx is None:
        return True, True
    overall_zone = getattr(placement_ctx, "sidewalk_zone", None)
    in_overall = _point_in_zone(overall_zone, point_xz)
    side_name = str(slot_side or "").strip().lower()
    if side_name == "left":
        side_zone = getattr(placement_ctx, "left_sidewalk_zone", None)
    elif side_name == "right":
        side_zone = getattr(placement_ctx, "right_sidewalk_zone", None)
    else:
        return True, in_overall
    if side_zone is None or getattr(side_zone, "is_empty", False):
        return True, in_overall
    return _point_in_zone(side_zone, point_xz), in_overall


def _segment_tangent_normal(segment_node: object | None) -> Optional[Tuple[Tuple[float, float], Tuple[float, float], float]]:
    if segment_node is None:
        return None
    start_xy = tuple(float(v) for v in getattr(segment_node, "start_xy", (0.0, 0.0)))
    end_xy = tuple(float(v) for v in getattr(segment_node, "end_xy", (0.0, 0.0)))
    dx = end_xy[0] - start_xy[0]
    dz = end_xy[1] - start_xy[1]
    length = math.hypot(dx, dz)
    if length <= 1e-6:
        return None
    tangent = (dx / length, dz / length)
    left_normal = (-tangent[1], tangent[0])
    return tangent, left_normal, float(length)


def _theme_nodes_for_segment(theme_segment: ThemeSegment, road_segment_graph: object | None) -> Tuple[object, ...]:
    nodes_by_id = _segment_node_lookup(road_segment_graph)
    nodes = [
        nodes_by_id[segment_id]
        for segment_id in theme_segment.segment_ids
        if segment_id in nodes_by_id
    ]
    return tuple(sorted(nodes, key=lambda node: float(getattr(node, "station_center_m", 0.0) or 0.0)))


def _point_within_theme_segment(
    point_xz: Tuple[float, float],
    *,
    theme_segment: ThemeSegment | None,
    road_segment_graph: object | None,
) -> bool:
    if theme_segment is None:
        return True
    if road_segment_graph is not None and getattr(road_segment_graph, "nodes", None):
        nodes = list(getattr(road_segment_graph, "nodes", ()) or ())
        if not nodes:
            return True
        nearest = min(
            nodes,
            key=lambda node: math.hypot(
                float(getattr(node, "center_xy", (0.0, 0.0))[0]) - float(point_xz[0]),
                float(getattr(node, "center_xy", (0.0, 0.0))[1]) - float(point_xz[1]),
            ),
        )
        return str(getattr(nearest, "segment_id", "")) in set(theme_segment.segment_ids)
    return bool(
        float(theme_segment.x_start_m) - 1e-6
        <= float(point_xz[0])
        <= float(theme_segment.x_end_m) + 1e-6
    )


def _theme_poi_points(
    *,
    theme_segment: ThemeSegment | None,
    theme_segments: Sequence[ThemeSegment],
    poi_ctx: object | None,
    road_segment_graph: object | None,
) -> Dict[str, Tuple[Tuple[float, float], ...]]:
    if poi_ctx is None:
        return {}
    points_by_type = nonempty_poi_points(getattr(poi_ctx, "poi_points_by_type_xz", {}) or {})
    if theme_segment is None:
        return {
            poi_type: tuple((float(point[0]), float(point[1])) for point in points)
            for poi_type, points in points_by_type.items()
        }
    filtered: Dict[str, List[Tuple[float, float]]] = {}
    for poi_type, points in points_by_type.items():
        for point in points:
            point_xz = (float(point[0]), float(point[1]))
            if assign_theme_id_for_point(point_xz, theme_segments, road_segment_graph) != theme_segment.theme_id:
                continue
            filtered.setdefault(str(poi_type), []).append(point_xz)
    return {
        poi_type: tuple(points)
        for poi_type, points in filtered.items()
        if points
    }


def _max_pair_cutoff(category: str, existing_categories: Iterable[str]) -> float:
    cutoffs = [8.0]
    for other_category in existing_categories:
        cutoffs.append(pair_cutoff_radius_m(category, str(other_category)))
    return float(max(cutoffs))


def _pair_scores_for_neighbors(
    *,
    category: str,
    point_xz: Tuple[float, float],
    neighbor_indices: Sequence[int],
    placements: Sequence[StreetPlacement],
) -> Tuple[float, float]:
    pair_attraction = 0.0
    pair_repulsion = 0.0
    for idx in neighbor_indices:
        placement = placements[int(idx)]
        attraction, repulsion = pair_interaction_scores(
            str(category),
            point_xz,
            str(placement.category),
            (float(placement.position_xyz[0]), float(placement.position_xyz[2])),
        )
        pair_attraction += float(attraction)
        pair_repulsion += float(repulsion)
    return float(pair_attraction), float(pair_repulsion)


def _band_deviation_penalty(
    *,
    point_xz: Tuple[float, float],
    slot: object,
    band_width_m: float,
) -> float:
    target_x = float(getattr(slot, "x_center_m", 0.0) or 0.0)
    target_z = float(getattr(slot, "z_center_m", 0.0) or 0.0)
    return float(
        math.hypot(float(point_xz[0]) - target_x, float(point_xz[1]) - target_z)
        / max(float(band_width_m), 1.0)
    )


def _search_tier_exact_candidates(
    *,
    category: str,
    anchor_target_xz: Tuple[float, float],
    placement_ctx: object,
) -> Tuple[Dict[str, object], ...]:
    from .placement_zones import compute_facing_angle

    yaw = _yaw_for_asset_category(
        category,
        compute_facing_angle(anchor_target_xz, placement_ctx.carriageway),  # type: ignore[attr-defined]
    )
    return (
        {
            "tier": "tier_1_exact",
            "point_xz": (float(anchor_target_xz[0]), float(anchor_target_xz[1])),
            "yaw_deg": float(yaw),
            "anchor_distance_m": 0.0,
        },
    )


def _search_tier_ring_candidates(
    *,
    category: str,
    anchor_target_xz: Tuple[float, float],
    placement_ctx: object,
) -> Tuple[Dict[str, object], ...]:
    from .placement_zones import compute_facing_angle

    candidates: List[Dict[str, object]] = []
    anchor_x, anchor_z = float(anchor_target_xz[0]), float(anchor_target_xz[1])
    for radius_m in (0.6, 1.2, 2.0, 3.0):
        for step_idx in range(8):
            angle = (2.0 * math.pi * float(step_idx)) / 8.0
            point = (
                anchor_x + math.cos(angle) * float(radius_m),
                anchor_z + math.sin(angle) * float(radius_m),
            )
            yaw = _yaw_for_asset_category(
                category,
                compute_facing_angle(point, placement_ctx.carriageway),  # type: ignore[attr-defined]
            )
            candidates.append(
                {
                    "tier": "tier_2_ring",
                    "point_xz": point,
                    "yaw_deg": float(yaw),
                    "anchor_distance_m": float(radius_m),
                }
            )
    return tuple(candidates)


def _search_tier_segment_candidates(
    *,
    category: str,
    anchor_target_xz: Tuple[float, float],
    segment_node: object | None,
    placement_ctx: object,
    config: StreetComposeConfig,
) -> Tuple[Dict[str, object], ...]:
    from .placement_zones import compute_facing_angle

    tangent_payload = _segment_tangent_normal(segment_node)
    if tangent_payload is None:
        return tuple()
    tangent, _left_normal, segment_length_m = tangent_payload
    search_extent = max(float(segment_length_m), 6.0, float(getattr(config, "segment_length_m", 6.0)))
    candidates: List[Dict[str, object]] = []
    for offset_m in np.arange(-search_extent, search_extent + 1e-6, 1.0):
        if abs(float(offset_m)) < 1e-6:
            continue
        point = (
            float(anchor_target_xz[0]) + tangent[0] * float(offset_m),
            float(anchor_target_xz[1]) + tangent[1] * float(offset_m),
        )
        anchor_distance_m = float(math.hypot(point[0] - anchor_target_xz[0], point[1] - anchor_target_xz[1]))
        if anchor_distance_m > 8.0 + 1e-6:
            continue
        yaw = _yaw_for_asset_category(
            category,
            compute_facing_angle(point, placement_ctx.carriageway),  # type: ignore[attr-defined]
        )
        candidates.append(
            {
                "tier": "tier_3_segment",
                "point_xz": point,
                "yaw_deg": float(yaw),
                "anchor_distance_m": anchor_distance_m,
            }
        )
    return tuple(candidates)


def _search_tier_theme_side_candidates(
    *,
    category: str,
    anchor_target_xz: Tuple[float, float],
    placement_ctx: object,
    theme_segment: ThemeSegment | None,
    road_segment_graph: object | None,
    slot_side: str,
    band_width_m: float,
) -> Tuple[Dict[str, object], ...]:
    from .placement_zones import compute_facing_angle

    if theme_segment is None:
        return tuple()
    candidates: List[Dict[str, object]] = []
    theme_nodes = _theme_nodes_for_segment(theme_segment, road_segment_graph)
    carriageway_half = float(getattr(placement_ctx, "carriageway_width_m", 8.0) or 8.0) / 2.0
    lateral = carriageway_half + max(float(band_width_m) * 0.45, 0.8)
    side_name = str(slot_side or "").strip().lower()
    sign = 1.0 if side_name == "left" else -1.0
    for node in theme_nodes:
        tangent_payload = _segment_tangent_normal(node)
        if tangent_payload is None:
            continue
        tangent, left_normal, _segment_length_m = tangent_payload
        normal = left_normal if sign > 0 else (-left_normal[0], -left_normal[1])
        center_x, center_z = tuple(float(v) for v in getattr(node, "center_xy", (0.0, 0.0)))
        for along_offset_m in (-2.0, 0.0, 2.0):
            point = (
                center_x + tangent[0] * float(along_offset_m) + normal[0] * lateral,
                center_z + tangent[1] * float(along_offset_m) + normal[1] * lateral,
            )
            anchor_distance_m = float(math.hypot(point[0] - anchor_target_xz[0], point[1] - anchor_target_xz[1]))
            if anchor_distance_m > 8.0 + 1e-6:
                continue
            yaw = _yaw_for_asset_category(
                category,
                compute_facing_angle(point, placement_ctx.carriageway),  # type: ignore[attr-defined]
            )
            candidates.append(
                {
                    "tier": "tier_4_theme_side",
                    "point_xz": point,
                    "yaw_deg": float(yaw),
                    "anchor_distance_m": anchor_distance_m,
                }
            )
    return tuple(candidates)


def _iter_slot_candidate_groups(
    *,
    slot: object,
    category: str,
    config: StreetComposeConfig,
    placement_ctx: object | None,
    segment_node: object | None,
    theme_segment: ThemeSegment | None,
    road_segment_graph: object | None,
    band_width_m: float,
    rng: random.Random,
) -> Tuple[Tuple[Dict[str, object], ...], ...]:
    anchor_target_xz = getattr(slot, "anchor_position_xz", None)
    if anchor_target_xz is not None and placement_ctx is not None and config.layout_mode == "osm":
        target_point = (float(anchor_target_xz[0]), float(anchor_target_xz[1]))
        return (
            _search_tier_exact_candidates(category=category, anchor_target_xz=target_point, placement_ctx=placement_ctx),
            _search_tier_ring_candidates(category=category, anchor_target_xz=target_point, placement_ctx=placement_ctx),
            _search_tier_segment_candidates(
                category=category,
                anchor_target_xz=target_point,
                segment_node=segment_node,
                placement_ctx=placement_ctx,
                config=config,
            ),
            _search_tier_theme_side_candidates(
                category=category,
                anchor_target_xz=target_point,
                placement_ctx=placement_ctx,
                theme_segment=theme_segment,
                road_segment_graph=road_segment_graph,
                slot_side=str(getattr(slot, "side", "") or ""),
                band_width_m=float(band_width_m),
            ),
        )
    candidates: List[Dict[str, object]] = []
    for _trial_idx in range(int(config.max_trials_per_slot)):
        if config.layout_mode == "osm" and placement_ctx is not None:
            pose = _sample_pose_osm_for_segment(
                category,
                placement_ctx,
                rng,
                segment_node=segment_node,
                slot_side=str(getattr(slot, "side", "") or ""),
                band_width_m=float(band_width_m),
                anchor_position_xz=None,
            )
        else:
            pose = _sample_pose_for_slot(
                slot_x_center=float(getattr(slot, "x_center_m", 0.0) or 0.0),
                slot_z_center=float(getattr(slot, "z_center_m", 0.0) or 0.0),
                slot_side=str(getattr(slot, "side", "") or ""),
                slot_spacing_m=float(getattr(slot, "spacing_m", 1.0) or 1.0),
                band_width_m=float(band_width_m),
                length_m=float(config.length_m),
                rng=rng,
            )
        if pose is None:
            continue
        x, z, yaw_deg = pose
        candidates.append(
            {
                "tier": "tier_optional_sampling",
                "point_xz": (float(x), float(z)),
                "yaw_deg": float(yaw_deg),
                "anchor_distance_m": None,
            }
        )
    return (tuple(candidates),)


def _evaluate_slot_candidate(
    *,
    candidate: Mapping[str, object],
    slot: object,
    category: str,
    band_width_m: float,
    entry: _MeshCacheEntry,
    scale_info: Mapping[str, object],
    placements: Sequence[StreetPlacement],
    spatial_hash: UniformSpatialHash,
    existing_bboxes: Sequence[Tuple[float, float, float, float]],
    placement_ctx: object | None,
    theme_segment: ThemeSegment | None,
    road_segment_graph: object | None,
    theme_poi_points: Mapping[str, Sequence[Tuple[float, float]]],
    poi_ctx: object | None,
    rule_set: object | None,
    config: StreetComposeConfig,
    entrance_registry: PlacedAssetRegistry,
    carriageway_boundary: Optional[CarriagewayBoundary],
    entrance_points_xz: Sequence[Tuple[float, float]],
) -> Tuple[Optional[Dict[str, object]], Optional[str]]:
    point_xz = (
        float(candidate["point_xz"][0]),
        float(candidate["point_xz"][1]),
    )
    side_matches, in_overall = _point_side_matches_slot(
        point_xz,
        slot_side=str(getattr(slot, "side", "") or ""),
        placement_ctx=placement_ctx,
    )
    if not in_overall:
        return None, "out_of_sidewalk"
    if not side_matches:
        return None, "side_mismatch"
    if not _point_within_theme_segment(point_xz, theme_segment=theme_segment, road_segment_graph=road_segment_graph):
        return None, "out_of_theme_range"

    bbox = _compute_bbox(
        x=float(point_xz[0]),
        z=float(point_xz[1]),
        yaw_deg=float(candidate["yaw_deg"]),
        half_x=entry.half_x,
        half_z=entry.half_z,
        scale=float(scale_info.get("applied_scale", 1.0) or 1.0),
        clearance=0.2,
    )
    neighbor_bbox_indices = spatial_hash.query_bbox(bbox)
    if any(_bbox_intersects(bbox, existing_bboxes[int(idx)]) for idx in neighbor_bbox_indices):
        return None, "overlap_blocked"

    poi_repulsion = 0.0
    constraint_penalty = 0.0
    feasibility_score = 1.0
    violated_rules: Tuple[str, ...] = ()
    if rule_set is not None and poi_ctx is not None:
        from .poi_rules import evaluate_repulsion_field, score_placement as _score_placement

        poi_repulsion = float(evaluate_repulsion_field(point_xz, category, rule_set, poi_ctx, aggregate="nearest"))
        if config.constraint_mode == "soft":
            constraint_result = _score_placement(point_xz, category, rule_set, poi_ctx)
            if float(constraint_result.penalty) > float(config.constraint_veto_threshold):
                return None, "constraint_vetoed"
            constraint_penalty = float(constraint_result.penalty)
            feasibility_score = float(constraint_result.feasibility_score)
            violated_rules = tuple(constraint_result.violated_rules)

    if entrance_points_xz and carriageway_boundary is not None:
        entrance_penalty, entrance_bonus, entrance_violated = score_entrance_impact(
            candidate_xz=point_xz,
            candidate_category=category,
            candidate_bbox_xz=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
            entrance_points_xz=tuple((float(point[0]), float(point[1])) for point in entrance_points_xz),
            registry=entrance_registry,
            carriageway_boundary=carriageway_boundary,
        )
        poi_repulsion += max(0.0, float(entrance_penalty) - float(entrance_bonus))
        if config.constraint_mode == "soft":
            constraint_penalty += max(0.0, float(entrance_penalty) - float(entrance_bonus))
            feasibility_score *= math.exp(-max(0.0, float(entrance_penalty)))
            violated_rules = tuple(list(violated_rules) + list(entrance_violated))

    neighbor_pair_radius = _max_pair_cutoff(category, (placement.category for placement in placements))
    neighbor_indices = spatial_hash.query_radius(point_xz, neighbor_pair_radius)
    pair_attraction, pair_repulsion = _pair_scores_for_neighbors(
        category=category,
        point_xz=point_xz,
        neighbor_indices=neighbor_indices,
        placements=placements,
    )
    poi_cutoff_m = max(7.5, float(band_width_m) + 6.0)
    poi_attraction = float(
        poi_attraction_score(
            category,
            point_xz,
            theme_poi_points,
            cutoff_m=poi_cutoff_m,
        )
    )
    energy = compose_candidate_energy(
        anchor_distance_m=(
            float(candidate["anchor_distance_m"])
            if candidate.get("anchor_distance_m") is not None
            else None
        ),
        poi_attraction=poi_attraction,
        poi_repulsion=poi_repulsion,
        pair_attraction=pair_attraction,
        pair_repulsion=pair_repulsion,
        band_deviation_penalty=_band_deviation_penalty(
            point_xz=point_xz,
            slot=slot,
            band_width_m=float(band_width_m),
        ),
    )
    return (
        {
            "x": float(point_xz[0]),
            "z": float(point_xz[1]),
            "yaw_deg": float(candidate["yaw_deg"]),
            "bbox": bbox,
            "scale": float(scale_info.get("applied_scale", 1.0) or 1.0),
            "native_size_m": dict(scale_info.get("native_size_m", {}) or {}),
            "canonical_target": dict(scale_info.get("canonical_target", {}) or {}),
            "asset_scale_mode": str(scale_info.get("asset_scale_mode", "")),
            "scale_fallback_used": bool(scale_info.get("scale_fallback_used", False)),
            "constraint_penalty": float(constraint_penalty),
            "feasibility_score": float(feasibility_score),
            "violated_rules": tuple(violated_rules),
            "placement_energy": float(energy.total_energy),
            "anchor_distance_m": (
                float(candidate["anchor_distance_m"])
                if candidate.get("anchor_distance_m") is not None
                else None
            ),
            "candidate_tier": str(candidate["tier"]),
        },
        None,
    )

def _pick_building_candidate(
    *,
    query: str,
    theme_name: str,
    frontage_width_m: float,
    depth_m: float,
    road_type: str,
    height_class: str,
    embedder: ClipTextEmbedder,
    index_store: FaissIndexStore,
    asset_by_id: Dict[str, Dict[str, object]],
    search_topk: int,
    rng: random.Random,
) -> Tuple[Optional[Dict[str, object]], float, str, Dict[str, object]]:
    query_text = building_query(
        query,
        theme_name=theme_name,
        frontage_width_m=float(frontage_width_m),
        depth_m=float(depth_m),
        road_type=road_type,
        height_class=height_class,
    )
    query_embedding = embedder.encode_texts([query_text])
    hits = index_store.search(query_embedding, topk=max(50, int(search_topk), 1))[0]
    reranked = rerank_building_candidates(
        hits=hits,
        asset_by_id=asset_by_id,
        theme_name=theme_name,
        frontage_width_m=float(frontage_width_m),
        depth_m=float(depth_m),
        height_class=height_class,
        limit=max(int(search_topk), 1),
    )
    payload = {
        "query": query_text,
        "hit_count": len(hits),
        "candidate_count": len(reranked),
        "candidates": [
            {
                "asset_id": row["asset_id"],
                "category": row["category"],
                "score": float(score),
            }
            for row, score in reranked
        ],
    }
    if not reranked:
        return None, 0.0, "procedural_fallback", payload
    weights = _softmax_weights([float(score) for _row, score in reranked], SOFTMAX_TEMPERATURE)
    pick_idx = int(rng.choices(range(len(reranked)), weights=weights, k=1)[0])
    row, score = reranked[pick_idx]
    payload["chosen_index"] = pick_idx
    return row, float(score), "building_asset", payload


def _dominant_building_road_type(
    road_segment_graph: object | None,
    resolved_program: object,
) -> str:
    highway_counts: Dict[str, int] = {}
    for node in getattr(road_segment_graph, "nodes", ()) or ():
        highway_type = str(getattr(node, "highway_type", "") or "").strip().lower()
        if highway_type:
            highway_counts[highway_type] = highway_counts.get(highway_type, 0) + 1
    if highway_counts:
        return max(sorted(highway_counts), key=lambda key: highway_counts[key])
    return str(getattr(resolved_program, "road_type", "") or "").strip().lower()


def _building_size_class(frontage_width_m: float, depth_m: float) -> str:
    major = max(float(frontage_width_m), float(depth_m))
    if major >= 24.0:
        return "large"
    if major >= 14.0:
        return "medium"
    return "small"


def _footprint_target_records(footprints: Sequence[BuildingFootprint]) -> List[Dict[str, object]]:
    return [
        {
            "target_id": str(footprint.footprint_id),
            "target_kind": "footprint",
            "source": str(footprint.source),
            "polygon_xz": tuple((float(x), float(z)) for x, z in footprint.polygon_xz),
            "center_xz": (float(footprint.centroid_xz[0]), float(footprint.centroid_xz[1])),
            "placement_xz": (float(footprint.placement_xz[0]), float(footprint.placement_xz[1])),
            "street_edge_xz": (float(footprint.street_edge_xz[0]), float(footprint.street_edge_xz[1])),
            "frontage_width_m": float(footprint.frontage_width_m),
            "depth_m": float(footprint.building_depth_m or footprint.depth_m),
            "parcel_depth_m": float(footprint.depth_m),
            "yaw_deg": float(footprint.yaw_deg),
            "theme_id": str(footprint.theme_id),
            "land_use_type": str(footprint.land_use_type),
            "side": str(footprint.side),
            "height_class": str(footprint.height_class),
            "target_height_m": float(footprint.target_height_m),
            "anchor_geom_id": str(footprint.anchor_geom_id),
            "size_class": str(footprint.size_class),
            "front_setback_m": float(footprint.front_setback_m),
            "placement_strategy": str(footprint.placement_strategy),
        }
        for footprint in footprints
    ]


def _lot_target_records(lots: Sequence[GeneratedLot]) -> List[Dict[str, object]]:
    return [
        {
            "target_id": str(lot.lot_id),
            "target_kind": "lot",
            "source": str(lot.source),
            "polygon_xz": tuple((float(x), float(z)) for x, z in lot.polygon_xz),
            "center_xz": (float(lot.center_xz[0]), float(lot.center_xz[1])),
            "placement_xz": (float(lot.placement_xz[0]), float(lot.placement_xz[1])),
            "street_edge_xz": (float(lot.street_edge_xz[0]), float(lot.street_edge_xz[1])),
            "frontage_width_m": float(lot.frontage_width_m),
            "depth_m": float(lot.building_depth_m or lot.depth_m),
            "parcel_depth_m": float(lot.depth_m),
            "yaw_deg": float(lot.yaw_deg),
            "theme_id": str(lot.theme_id),
            "height_class": str(lot.height_class),
            "target_height_m": float(lot.target_height_m),
            "anchor_geom_id": str(lot.lot_id),
            "size_class": str(_building_size_class(lot.frontage_width_m, lot.building_depth_m or lot.depth_m)),
            "land_use_type": str(lot.land_use_type),
            "side": str(lot.side),
            "front_setback_m": float(lot.front_setback_m),
            "placement_strategy": str(lot.placement_strategy),
        }
        for lot in lots
    ]


def _place_building_targets(
    *,
    targets: Sequence[Mapping[str, object]],
    config: StreetComposeConfig,
    theme_segments: Sequence[ThemeSegment],
    resolved_program: object,
    embedder: ClipTextEmbedder,
    index_store: FaissIndexStore,
    asset_by_id: Dict[str, Dict[str, object]],
    mesh_cache: Dict[str, _MeshCacheEntry],
    rng: random.Random,
    start_instance_index: int,
    road_type: str,
) -> Tuple[Tuple[StreetPlacement, ...], Tuple[BuildingPlacementPlan, ...], Tuple[Dict[str, object], ...], Dict[str, object], int]:
    theme_by_id = {segment.theme_id: segment for segment in theme_segments}
    placements: List[StreetPlacement] = []
    plans: List[BuildingPlacementPlan] = []
    retrieval_predictions: List[Dict[str, object]] = []
    fallback_count = 0
    asset_count = 0
    instance_index = int(start_instance_index)
    source_counts: Dict[str, int] = {}
    placement_strategy_counts: Dict[str, int] = {}
    front_setbacks: List[float] = []

    for target_idx, target in enumerate(targets):
        theme_id = str(target.get("theme_id", "") or "")
        theme_segment = theme_by_id.get(theme_id, theme_segments[0] if theme_segments else None)
        theme_name = (
            str(target.get("land_use_type", "") or "")
            or (theme_segment.theme_name if theme_segment is not None else "commercial")
        )
        row, score, source, retrieval_payload = _pick_building_candidate(
            query=config.query,
            theme_name=theme_name,
            frontage_width_m=float(target.get("frontage_width_m", 12.0) or 12.0),
            depth_m=float(target.get("depth_m", 10.0) or 10.0),
            road_type=str(road_type),
            height_class=str(target.get("height_class", "") or ""),
            embedder=embedder,
            index_store=index_store,
            asset_by_id=asset_by_id,
            search_topk=int(getattr(config, "building_search_topk", 5)),
            rng=rng,
        )
        retrieval_payload.update(
            {
                f"{str(target.get('target_kind', 'footprint'))}_id": str(target.get("target_id", "") or ""),
                "theme_id": theme_id,
                "source": str(target.get("source", "") or ""),
                "height_class": str(target.get("height_class", "") or ""),
                "target_height_m": float(target.get("target_height_m", 0.0) or 0.0),
            }
        )
        retrieval_predictions.append(retrieval_payload)

        frontage_width_m = float(target.get("frontage_width_m", 12.0) or 12.0)
        depth_m = float(target.get("depth_m", 10.0) or 10.0)
        _target_height_m = float(target.get("target_height_m", 0.0) or 0.0)
        if row is not None:
            entry = mesh_cache[row["asset_id"]]
            scale_x = max(frontage_width_m / max(entry.half_x * 2.0, 1e-3), 0.1)
            scale_z = max(depth_m / max(entry.half_z * 2.0, 1e-3), 0.1)
            if _target_height_m > 0.0 and entry.native_height_y > 0.01:
                scale_y = max(0.75, min(3.0, _target_height_m / entry.native_height_y))
            else:
                height_multiplier = {"lowrise": 1.0, "midrise": 1.4, "highrise": 1.8}.get(
                    str(row.get("height_class", target.get("height_class", "midrise"))),
                    {"lowrise": 1.0, "midrise": 1.4, "highrise": 1.8}.get(str(target.get("height_class", "midrise")), 1.2),
                )
                scale_y = max(scale_x, scale_z) * float(height_multiplier)
            scale_xyz = [float(scale_x), float(scale_y), float(scale_z)]
            asset_id = str(row["asset_id"])
            asset_count += 1
            fallback_reason = ""
        else:
            asset_id = f"building_fallback_{str(target.get('target_kind', 'footprint'))}_{target_idx:03d}"
            mesh_cache[asset_id] = _placeholder_building_entry(
                asset_id=asset_id,
                frontage_width_m=frontage_width_m,
                depth_m=depth_m,
                height_class=str(target.get("height_class", "midrise") or "midrise"),
                theme_name=theme_name,
                target_height_m=_target_height_m,
            )
            asset_by_id[asset_id] = {
                "asset_id": asset_id,
                "category": "building",
                "text_desc": f"{theme_name} {target.get('height_class', 'midrise')} procedural building",
                "asset_role": "building",
                "theme_tags": [theme_name, str(target.get("size_class", ""))],
                "height_class": str(target.get("height_class", "midrise") or "midrise"),
            }
            entry = mesh_cache[asset_id]
            scale_xyz = [1.0, 1.0, 1.0]
            fallback_count += 1
            fallback_reason = "no_building_asset_match"

        placement_xz_raw = target.get("placement_xz", target.get("center_xz", (0.0, 0.0))) or (0.0, 0.0)
        center_xz = (
            float(placement_xz_raw[0]),
            float(placement_xz_raw[1]),
        )
        placement_strategy = str(target.get("placement_strategy", "") or "")
        front_setback_m = float(target.get("front_setback_m", 0.0) or 0.0)
        bbox = _compute_bbox(
            x=float(center_xz[0]),
            z=float(center_xz[1]),
            yaw_deg=float(target.get("yaw_deg", 0.0) or 0.0),
            half_x=entry.half_x,
            half_z=entry.half_z,
            scale=scale_xyz,
            clearance=0.15,
        )
        y = -entry.min_y * float(scale_xyz[1])
        plans.append(
            BuildingPlacementPlan(
                footprint_id=str(target.get("target_id", "") or ""),
                theme_id=theme_id,
                asset_id=asset_id,
                selection_source=source,
                position_xyz=[float(center_xz[0]), float(y), float(center_xz[1])],
                yaw_deg=float(target.get("yaw_deg", 0.0) or 0.0),
                scale=1.0,
                scale_xyz=[float(value) for value in scale_xyz],
                bbox_xz=[float(value) for value in bbox],
                frontage_width_m=frontage_width_m,
                depth_m=depth_m,
                anchor_geom_id=str(target.get("anchor_geom_id", "") or ""),
                retrieval_score=float(score),
                fallback_reason=fallback_reason,
                target_height_m=_target_height_m,
                placement_strategy=placement_strategy,
                front_setback_m=front_setback_m,
            )
        )
        placements.append(
            StreetPlacement(
                instance_id=f"inst_{instance_index:04d}",
                asset_id=asset_id,
                category="building",
                score=float(score),
                position_xyz=[float(center_xz[0]), float(y), float(center_xz[1])],
                yaw_deg=float(target.get("yaw_deg", 0.0) or 0.0),
                scale=1.0,
                bbox_xz=[float(value) for value in bbox],
                selection_source=source,
                placement_group="building",
                theme_id=theme_id,
                anchor_geom_id=str(target.get("anchor_geom_id", "") or ""),
                scale_xyz=[float(value) for value in scale_xyz],
            )
        )
        source_name = str(target.get("source", "") or "")
        source_counts[source_name] = source_counts.get(source_name, 0) + 1
        if placement_strategy:
            placement_strategy_counts[placement_strategy] = placement_strategy_counts.get(placement_strategy, 0) + 1
        if front_setback_m > 0.0:
            front_setbacks.append(front_setback_m)
        instance_index += 1

    summary = {
        "enabled": True,
        "target_count": int(len(targets)),
        "placed_count": int(len(placements)),
        "asset_count": int(asset_count),
        "fallback_count": int(fallback_count),
        "sources": source_counts,
        "placement_strategy_counts": placement_strategy_counts,
    }
    if front_setbacks:
        summary["front_setback_stats"] = {
            "min_m": round(min(front_setbacks), 3),
            "max_m": round(max(front_setbacks), 3),
            "mean_m": round(sum(front_setbacks) / len(front_setbacks), 3),
        }
    return tuple(placements), tuple(plans), tuple(retrieval_predictions), summary, instance_index


def _place_surrounding_buildings(
    *,
    config: StreetComposeConfig,
    projected_features: object | None,
    placement_ctx: object | None,
    road_segment_graph: object | None,
    theme_segments: Sequence[ThemeSegment],
    resolved_program,
    embedder: ClipTextEmbedder,
    index_store: FaissIndexStore,
    asset_by_id: Dict[str, Dict[str, object]],
    mesh_cache: Dict[str, _MeshCacheEntry],
    rng: random.Random,
    start_instance_index: int,
) -> _SurroundingBuildingResult:
    if not bool(getattr(config, "enable_surrounding_buildings", True)) or config.layout_mode != "osm":
        return _SurroundingBuildingResult(
            building_footprints=tuple(),
            generated_lots=tuple(),
            placements=tuple(),
            plans=tuple(),
            retrieval_predictions=tuple(),
            building_summary={"enabled": False, "footprint_count": 0, "lot_count": 0, "placed_count": 0, "fallback_count": 0},
            land_use_summary={},
            lot_generation_summary={"lot_count": 0},
            zoning_grid=tuple(),
            zoning_preview_summary={"enabled": False, "cell_count": 0},
            instance_index=int(start_instance_index),
        )

    mode = str(getattr(config, "surrounding_building_mode", "grid_growth") or "grid_growth").strip().lower()
    road_type = _dominant_building_road_type(road_segment_graph, resolved_program)
    building_footprints: Tuple[BuildingFootprint, ...] = tuple()
    generated_lots: Tuple[GeneratedLot, ...] = tuple()
    zoning_granularity = str(getattr(config, "zoning_granularity", "fine") or "fine")
    streetwall_continuity = float(getattr(config, "streetwall_continuity", 0.95) or 0.95)
    infill_policy = str(getattr(config, "infill_policy", "aggressive") or "aggressive")
    footprint_frontage_summary: Dict[str, object] = {
        "real_footprint_count": 0,
        "infill_footprint_count": 0,
        "frontage_coverage_by_side": {"left": {}, "right": {}},
        "frontage_gap_stats_by_side": {"left": {}, "right": {}},
    }

    if mode == "footprint_based":
        asymmetry_raw = getattr(config, "land_use_asymmetry_strength", 0.0)
        bias_raw = getattr(config, "left_right_bias", 0.0)
        setback_min_raw = getattr(config, "building_front_setback_min_m", 1.0)
        setback_max_raw = getattr(config, "building_front_setback_max_m", 2.0)
        building_footprints = tuple(
            collect_building_footprints(
                projected_features,
                placement_context=placement_ctx,
                theme_segments=theme_segments,
                road_segment_graph=road_segment_graph,
                road_buffer_m=35.0,
                seed=int(getattr(config, "seed", 0) or 0),
                height_mode=str(getattr(config, "building_height_mode", "theme_random") or "theme_random"),
                height_profile=str(getattr(config, "building_height_profile", "urban_default_v1") or "urban_default_v1"),
                asymmetry_strength=float(0.0 if asymmetry_raw is None else asymmetry_raw),
                left_right_bias=float(0.0 if bias_raw is None else bias_raw),
                front_setback_min_m=float(1.0 if setback_min_raw is None else setback_min_raw),
                front_setback_max_m=float(2.0 if setback_max_raw is None else setback_max_raw),
                zoning_granularity=zoning_granularity,
                streetwall_continuity=streetwall_continuity,
            )
        )

    zoning_grid_base, zoning_preview_summary = build_zoning_grid_preview(
        config=config,
        placement_context=placement_ctx,
        road_segment_graph=road_segment_graph,
        theme_segments=theme_segments,
        building_footprints=building_footprints,
        road_buffer_m=35.0,
    )
    zoning_grid = zoning_grid_base
    lot_generation_summary: Dict[str, object] = {"lot_count": 0}
    if mode == "footprint_based":
        setback_min_raw = getattr(config, "building_front_setback_min_m", 1.0)
        setback_max_raw = getattr(config, "building_front_setback_max_m", 2.0)
        infill_footprints, footprint_frontage_summary = generate_frontage_infill_footprints(
            zoning_grid_base,
            building_footprints,
            seed=int(getattr(config, "seed", 0) or 0),
            height_mode=str(getattr(config, "building_height_mode", "theme_random") or "theme_random"),
            height_profile=str(getattr(config, "building_height_profile", "urban_default_v1") or "urban_default_v1"),
            zoning_granularity=zoning_granularity,
            streetwall_continuity=streetwall_continuity,
            infill_policy=infill_policy,
            front_setback_min_m=float(1.0 if setback_min_raw is None else setback_min_raw),
            front_setback_max_m=float(2.0 if setback_max_raw is None else setback_max_raw),
        )
        if infill_footprints:
            building_footprints = tuple(list(building_footprints) + list(infill_footprints))
            zoning_grid_base, zoning_preview_summary = build_zoning_grid_preview(
                config=config,
                placement_context=placement_ctx,
                road_segment_graph=road_segment_graph,
                theme_segments=theme_segments,
                building_footprints=building_footprints,
                road_buffer_m=35.0,
            )
            zoning_grid = zoning_grid_base
    if mode == "grid_growth":
        setback_min_raw = getattr(config, "building_front_setback_min_m", 1.0)
        setback_max_raw = getattr(config, "building_front_setback_max_m", 2.0)
        zoning_grid, generated_lots, lot_generation_summary = generate_grid_growth_lots(
            zoning_grid_base,
            road_type=road_type,
            seed=int(getattr(config, "seed", 0) or 0),
            height_mode=str(getattr(config, "building_height_mode", "theme_random") or "theme_random"),
            height_profile=str(getattr(config, "building_height_profile", "urban_default_v1") or "urban_default_v1"),
            front_setback_min_m=float(1.0 if setback_min_raw is None else setback_min_raw),
            front_setback_max_m=float(2.0 if setback_max_raw is None else setback_max_raw),
            zoning_granularity=zoning_granularity,
            streetwall_continuity=streetwall_continuity,
        )
    land_use_summary = summarize_land_use_grid(zoning_grid)

    if mode == "grid_growth":
        target_records = _lot_target_records(generated_lots)
    else:
        target_records = _footprint_target_records(building_footprints)
    building_placements, building_plans, building_retrieval_predictions, placement_summary, instance_index = _place_building_targets(
        targets=target_records,
        config=config,
        theme_segments=theme_segments,
        resolved_program=resolved_program,
        embedder=embedder,
        index_store=index_store,
        asset_by_id=asset_by_id,
        mesh_cache=mesh_cache,
        rng=rng,
        start_instance_index=start_instance_index,
        road_type=road_type,
    )

    occupied_building_cells = sum(
        1
        for cell in zoning_grid
        if "building_buffer" in str(cell.get("lane_role", "") or "")
        and (
            (cell.get("footprint_ids", []) or [])
            or str(cell.get("lot_id", "") or "")
        )
    )
    zoning_preview_summary = {
        **dict(zoning_preview_summary),
        "occupied_building_cells": int(occupied_building_cells),
        "generated_lot_count": int(len(generated_lots)),
        "zoning_preview_mode": str(zoning_preview_summary.get("zoning_preview_mode", "parcel_first") or "parcel_first"),
        "frontage_cell_count": int(zoning_preview_summary.get("frontage_cell_count", 0) or 0),
        "theme_segment_count": int(zoning_preview_summary.get("theme_segment_count", len(theme_segments)) or len(theme_segments)),
        "frontage_parcel_count": int(
            lot_generation_summary.get("frontage_parcel_count", len(generated_lots))
            if mode == "grid_growth"
            else 0
        ),
    }
    asymmetry_raw = getattr(config, "land_use_asymmetry_strength", 0.0)
    bias_raw = getattr(config, "left_right_bias", 0.0)
    setback_min_raw = getattr(config, "building_front_setback_min_m", 1.0)
    setback_max_raw = getattr(config, "building_front_setback_max_m", 2.0)
    frontage_metrics_source = footprint_frontage_summary if mode == "footprint_based" else lot_generation_summary
    building_summary = {
        **dict(placement_summary),
        "enabled": True,
        "generation_mode": mode,
        "footprint_count": int(len(building_footprints)),
        "lot_count": int(len(generated_lots)),
        "target_type": "lot" if mode == "grid_growth" else "footprint",
        "land_use_asymmetry_strength": float(0.0 if asymmetry_raw is None else asymmetry_raw),
        "left_right_bias": float(0.0 if bias_raw is None else bias_raw),
        "building_front_setback_min_m": float(1.0 if setback_min_raw is None else setback_min_raw),
        "building_front_setback_max_m": float(2.0 if setback_max_raw is None else setback_max_raw),
        "zoning_granularity": str(zoning_granularity),
        "streetwall_continuity": float(streetwall_continuity),
        "infill_policy": str(infill_policy),
        "building_balance_policy": str(
            lot_generation_summary.get("building_balance_policy", "balanced_default")
            if mode == "grid_growth"
            else "manual_realistic_mode"
        ),
        "building_balance_ok": bool(
            lot_generation_summary.get("building_balance_ok", False) if mode == "grid_growth" else False
        ),
        "building_balance_reason": str(
            lot_generation_summary.get("building_balance_reason", "") if mode == "grid_growth" else "footprint_based mode"
        ),
        "frontage_balance_gap": float(
            lot_generation_summary.get("frontage_balance_gap", 0.0)
            if mode == "grid_growth"
            else 0.0
        ),
        "buildable_frontage_by_side": dict(
            lot_generation_summary.get(
                "buildable_frontage_by_side",
                zoning_preview_summary.get("buildable_frontage_by_side", {}),
            )
            or {}
        ),
        "frontage_parcel_count": int(
            lot_generation_summary.get("frontage_parcel_count", len(generated_lots))
            if mode == "grid_growth"
            else 0
        ),
        "zoning_preview_mode": str(zoning_preview_summary.get("zoning_preview_mode", "parcel_first") or "parcel_first"),
        "frontage_cell_count": int(zoning_preview_summary.get("frontage_cell_count", 0) or 0),
        "real_footprint_count": int(footprint_frontage_summary.get("real_footprint_count", 0) or 0),
        "infill_footprint_count": int(footprint_frontage_summary.get("infill_footprint_count", 0) or 0),
        "frontage_coverage_by_side": dict(frontage_metrics_source.get("frontage_coverage_by_side", {}) or {}),
        "frontage_gap_stats_by_side": dict(frontage_metrics_source.get("frontage_gap_stats_by_side", {}) or {}),
    }
    # Attach continuous height stats when available
    _all_heights: list[float] = []
    for fp in building_footprints:
        if fp.target_height_m > 0.0:
            _all_heights.append(fp.target_height_m)
    for lot in generated_lots:
        if lot.target_height_m > 0.0:
            _all_heights.append(lot.target_height_m)
    if _all_heights:
        building_summary["height_stats"] = {
            "min_m": round(min(_all_heights), 1),
            "max_m": round(max(_all_heights), 1),
            "mean_m": round(sum(_all_heights) / len(_all_heights), 1),
        }
    return _SurroundingBuildingResult(
        building_footprints=tuple(building_footprints),
        generated_lots=tuple(generated_lots),
        placements=tuple(building_placements),
        plans=tuple(building_plans),
        retrieval_predictions=tuple(building_retrieval_predictions),
        building_summary=building_summary,
        land_use_summary=land_use_summary,
        lot_generation_summary=lot_generation_summary,
        zoning_grid=tuple(zoning_grid),
        zoning_preview_summary=zoning_preview_summary,
        instance_index=int(instance_index),
    )


def compose_street_scene(
    config: StreetComposeConfig,
    manifest_path: Path,
    artifacts_dir: Path,
    model_name: str = "openai/clip-vit-base-patch32",
    model_dir: Optional[Path] = None,
    local_files_only: bool = False,
    device: str = "auto",
    export_format: str = "both",
    out_dir: Path = Path("artifacts/real"),
    placement_policy: str = "rule",
    policy_ckpt: Optional[Path] = None,
    program_ckpt: Optional[Path] = None,
    policy_temperature: float = SOFTMAX_TEMPERATURE,
) -> StreetComposeResult:
    """
    Compose a street scene by category-aware retrieval and collision-aware placement.

    Outputs:
    - scene.glb/scene.ply under `out_dir` (per `export_format`)
    - scene_layout.json under `out_dir`
    """
    _validate_config(config)
    export_format = _validate_export_format(export_format)
    manifest_path = Path(manifest_path).resolve()
    artifacts_dir = Path(artifacts_dir).resolve()
    out_dir = Path(out_dir).resolve()
    policy_mode = str(placement_policy).strip().lower()
    if policy_mode not in {"rule", "learned"}:
        raise ValueError("placement_policy must be 'rule' or 'learned'")

    rows = _load_real_manifest(manifest_path)
    asset_by_id = {row["asset_id"]: row for row in rows}

    category_to_rows: Dict[str, List[Dict[str, str]]] = {category: [] for category in DEFAULT_CATEGORIES}
    raw_tree_inventory_count = sum(1 for row in rows if str(row.get("category", "")).strip().lower() == "tree")
    for row in rows:
        category = row["category"]
        if category in category_to_rows:
            if category == "tree":
                if not _row_scene_eligible(row) or not _is_external_tree_asset(row):
                    continue
            category_to_rows[category].append(row)
    tree_assets_unavailable = not bool(category_to_rows.get("tree"))

    available_categories = [category for category, pool in category_to_rows.items() if pool]
    if not available_categories:
        raise RuntimeError(
            f"No supported categories found in manifest: {manifest_path}. "
            f"Expected at least one of {DEFAULT_CATEGORIES}."
        )

    mesh_cache = _load_mesh_cache(rows)

    parametric_tree_count = 0

    embedder = ClipTextEmbedder(
        model_name=model_name,
        model_dir=model_dir,
        local_files_only=bool(local_files_only),
        device=device,
    )
    index_store = FaissIndexStore.load(
        index_path=artifacts_dir / "index_ip.faiss",
        id_map_path=artifacts_dir / "id_map.json",
    )

    policy_runtime: Optional[LayoutPolicyRuntime] = None
    policy_used = "rule"
    policy_fallback_reason = ""
    if policy_mode == "learned":
        ckpt_path = Path(policy_ckpt).expanduser().resolve() if policy_ckpt else None
        if ckpt_path is None or not ckpt_path.exists():
            policy_fallback_reason = (
                "Policy checkpoint missing; fallback to rule policy."
                if ckpt_path is None
                else f"Policy checkpoint not found: {ckpt_path}. Fallback to rule policy."
            )
        else:
            try:
                policy_runtime = LayoutPolicyRuntime.from_checkpoint(ckpt_path, device=device)
                policy_used = "learned"
            except Exception as exc:
                policy_fallback_reason = f"Policy runtime load failed ({exc}); fallback to rule policy."

    program_runtime = ProgramGeneratorRuntime(backend="heuristic_v1", device=device)
    program_used = "heuristic_v1"
    program_fallback_reasons: List[str] = []
    if str(config.program_generator).strip().lower() == "learned_v1":
        ckpt_path = Path(program_ckpt).expanduser().resolve() if program_ckpt else None
        if ckpt_path is None or not ckpt_path.exists():
            program_fallback_reasons.append(
                "Program generator checkpoint missing; fallback to heuristic_v1."
                if ckpt_path is None
                else f"Program generator checkpoint not found: {ckpt_path}. Fallback to heuristic_v1."
            )
        else:
            try:
                program_runtime = ProgramGeneratorRuntime.from_checkpoint(ckpt_path, device=device)
                program_used = "learned_v1"
            except Exception as exc:
                program_fallback_reasons.append(f"Program generator load failed ({exc}); fallback to heuristic_v1.")

    rng = random.Random(int(config.seed))
    placements: List[StreetPlacement] = []
    existing_bboxes: List[Tuple[float, float, float, float]] = []
    used_asset_ids_by_category: Dict[str, set[str]] = {category: set() for category in DEFAULT_CATEGORIES}
    retrieval_predictions: List[Dict[str, object]] = []
    dropped_slots = 0
    instance_counter = 1
    clearance = 0.2
    start_perf = time.perf_counter()

    placement_ctx = None
    projected = None
    effective_poi_counts: Dict[str, int] = normalize_poi_counts({})
    if config.layout_mode == "osm":
        from .osm_ingest import fetch_osm_data, parse_osm_features, project_to_local
        from .placement_zones import evaluate_projected_road_context

        raw = fetch_osm_data(bbox=config.aoi_bbox, cache_dir=Path(config.osm_cache_dir))
        features = parse_osm_features(raw)
        projected = project_to_local(features, config.aoi_bbox)
        projected, placement_ctx, effective_poi_counts = evaluate_projected_road_context(projected, config)
        if not getattr(placement_ctx, "poi_fit_feasible", True):
            raise RuntimeError(
                "Selected road failed POI fit synthesis: "
                f"{json.dumps(getattr(placement_ctx, 'poi_fit_report', {}), ensure_ascii=True)}"
            )
        if not qualifies_poi_counts(effective_poi_counts):
            raise RuntimeError(
                "Selected road does not retain enough effective POIs after compose filtering "
                "(requires weighted POI score >= 2.0 and at least 1 core POI)."
            )

    poi_ctx = None
    rule_set = None
    from .poi_rules import PoiContext, build_poi_context
    if placement_ctx is not None:
        poi_ctx = build_poi_context(placement_ctx)
    else:
        poi_ctx = PoiContext((), (), ())
    if poi_ctx is not None:
        rule_set = load_rule_set(config.poi_rule_set)

    entrance_registry = PlacedAssetRegistry()
    entrance_points_xz: Tuple[Tuple[float, float], ...] = ()
    carriageway_boundary: Optional[CarriagewayBoundary] = None
    if poi_ctx is not None and poi_ctx.entrance_points_xz:
        entrance_points_xz = poi_ctx.entrance_points_xz
    if placement_ctx is not None and hasattr(placement_ctx, "carriageway_polygon") and placement_ctx.carriageway_polygon is not None:
        carriageway_boundary = CarriagewayBoundary.from_polygon(placement_ctx.carriageway_polygon)
    else:
        carriageway_boundary = CarriagewayBoundary.from_template(
            road_width_m=float(config.road_width_m),
            length_m=float(config.length_m),
        )

    inventory_summary = InventorySummary(
        category_counts={category: len(pool) for category, pool in category_to_rows.items() if pool},
        asset_ids_by_category={
            category: tuple(row["asset_id"] for row in pool)
            for category, pool in category_to_rows.items()
            if pool
        },
    )
    if config.layout_mode == "osm":
        for poi_type, required_count in asset_backed_poi_anchor_counts(
            extract_poi_points_by_type(placement_ctx) if placement_ctx is not None else {}
        ).items():
            if int(required_count) <= 0:
                continue
            category = asset_category_for_poi(poi_type)
            if category and category not in inventory_summary.category_counts:
                raise RuntimeError(
                    f"Selected road has {poi_type} POIs but the asset inventory has no {category} category."
                )
    road_segment_graph = build_segment_graph(projected, config) if projected is not None else None
    spatial_ctx = build_spatial_context(config, road_segment_graph, poi_ctx)
    theme_segments = infer_theme_segments(
        road_segment_graph,
        query=config.query,
        target_street_type=config.target_street_type,
        fallback_length_m=float(config.length_m),
    )
    theme_by_id = {segment.theme_id: segment for segment in theme_segments}

    program_result = program_runtime.generate(
        ProgramGenerationInput(
            query=config.query,
            compose_config=config,
            available_categories=tuple(available_categories),
            constraint_profile=str(config.design_rule_profile),
            placement_context=placement_ctx,
            inventory_summary=inventory_summary,
            road_segment_graph=road_segment_graph,
            poi_context=poi_ctx,
        )
    )
    if program_result.backend_used == "learned_v1":
        program_used = "learned_v1"
    if program_result.fallback_reason:
        program_fallback_reasons.append(program_result.fallback_reason)
    base_program = shape_program_for_style(program_result.program, config)
    base_constraint_set = load_constraint_set(config.design_rule_profile)
    solver_runtime = LayoutSolverRuntime(backend=str(config.layout_solver))

    zone_solver_results: List[LayoutSolverResult] = []
    slot_plans: List[object] = []
    slot_segment_lookup: Dict[str, object] = {}
    slot_band_lookup: Dict[str, object] = {}
    theme_zone_programs: List[Dict[str, object]] = []
    composition_pass_reports: List[Dict[str, object]] = []

    for theme_segment in theme_segments:
        theme_spec = theme_profile_style(theme_segment.theme_name)
        zone_query = f"{config.query}, {theme_segment.theme_name} streetscape"
        zone_design_rule_profile = (
            str(theme_spec["design_rule_profile"])
            if config.layout_mode == "osm"
            else str(config.design_rule_profile)
        )
        zone_style_preset = (
            str(theme_spec["style_preset"])
            if config.layout_mode == "osm"
            else str(config.style_preset)
        )
        zone_config = replace(
            config,
            query=zone_query,
            length_m=float(max(theme_segment.length_m, min(float(config.segment_length_m), float(config.length_m)))),
            design_rule_profile=zone_design_rule_profile,
            style_preset=zone_style_preset,
            target_street_type=str(theme_segment.theme_name) if config.layout_mode == "osm" else str(config.target_street_type),
        )
        zone_program_result = program_runtime.generate(
            ProgramGenerationInput(
                query=zone_query,
                compose_config=zone_config,
                available_categories=tuple(available_categories),
                constraint_profile=str(zone_config.design_rule_profile),
                placement_context=placement_ctx,
                inventory_summary=inventory_summary,
                road_segment_graph=road_segment_graph,
                poi_context=poi_ctx,
            )
        )
        if zone_program_result.backend_used == "learned_v1":
            program_used = "learned_v1"
        if zone_program_result.fallback_reason:
            program_fallback_reasons.append(zone_program_result.fallback_reason)
        zone_program = shape_program_for_style(zone_program_result.program, zone_config)
        zone_constraint_set = load_constraint_set(zone_config.design_rule_profile)
        zone_solver_result = solver_runtime.solve(
            LayoutSolverInput(
                program=zone_program,
                config=zone_config,
                available_categories=tuple(available_categories),
                constraint_set=zone_constraint_set,
                placement_context=placement_ctx,
                inventory_summary=inventory_summary,
                road_segment_graph=road_segment_graph,
            )
        )
        zone_slots = list(zone_solver_result.slot_plans)
        zone_slots, zone_composition = apply_composition_pass(
            zone_slots,
            config=zone_config,
            poi_context=poi_ctx,
        )
        zone_slots, zone_slot_segments = _globalize_theme_slot_plans(
            zone_slots,
            theme_segment=theme_segment,
            road_segment_graph=road_segment_graph,
        )
        zone_solver_result = replace(zone_solver_result, slot_plans=tuple(zone_slots))
        zone_solver_results.append(zone_solver_result)
        slot_plans.extend(zone_slots)
        slot_segment_lookup.update(zone_slot_segments)
        zone_band_by_name = {band.name: band for band in zone_solver_result.resolved_program.bands}
        for slot in zone_slots:
            slot_band_lookup[str(slot.slot_id)] = zone_band_by_name.get(str(slot.band_name))
        composition_pass_reports.append(dict(zone_composition))
        theme_zone_programs.append(
            {
                "theme_id": theme_segment.theme_id,
                "theme_name": theme_segment.theme_name,
                "query": zone_query,
                "cross_section_type": zone_solver_result.resolved_program.cross_section_type,
                "design_rule_profile": zone_config.design_rule_profile,
                "style_preset": zone_config.style_preset,
                "slot_count": len(zone_slots),
                "backend_used": zone_program_result.backend_used,
                "solver_backend_used": zone_solver_result.backend_used,
            }
        )

    if not slot_plans:
        raise RuntimeError(
            "Layout solver produced zero slots. "
            "Check the design rule profile, theme inference, asset coverage, or scene length."
        )

    building_strategy_summary = {
        "theme_segment_count": int(len(theme_segments)),
        "theme_names": [segment.theme_name for segment in theme_segments],
        "theme_inference_mode": str(getattr(config, "theme_inference_mode", "deterministic_auto")),
        "theme_vocab_name": str(getattr(config, "theme_vocab_name", "fixed_v1")),
    }
    resolved_program = replace(
        base_program,
        theme_segments=tuple(theme_segments),
        building_strategy_summary=dict(building_strategy_summary),
        notes=tuple(dict.fromkeys(list(base_program.notes) + ["multitheme_street_v1"])),
    )
    graph_summary = (
        road_segment_graph.summary()
        if road_segment_graph is not None and hasattr(road_segment_graph, "summary")
        else None
    )
    if graph_summary is not None:
        graph_summary = {
            **dict(graph_summary),
            "theme_segment_count": int(len(theme_segments)),
            "theme_names": [segment.theme_name for segment in theme_segments],
            "theme_vocab_name": str(getattr(config, "theme_vocab_name", "fixed_v1")),
        }
    solver_result = _aggregate_solver_results(
        resolved_program=resolved_program,
        solver_results=zone_solver_results,
        slot_plans=slot_plans,
        road_segment_graph_summary=graph_summary,
    )

    for poi_type, required_count in asset_backed_poi_anchor_counts(
        extract_poi_points_by_type(placement_ctx) if placement_ctx is not None else {}
    ).items():
        category = asset_category_for_poi(poi_type)
        actual_count = sum(
            1
            for slot in slot_plans
            if slot.category == category and slot.anchor_poi_type == poi_type
        )
        if int(required_count) > int(actual_count):
            raise RuntimeError(
                f"Layout solver did not preserve all required POI-backed {category} slots."
            )

    composition_pass_report = {
        "trimmed_optional_slots": int(sum(int(report.get("trimmed_optional_slots", 0)) for report in composition_pass_reports)),
        "required_slots_preserved": int(sum(int(report.get("required_slots_preserved", 0)) for report in composition_pass_reports)),
        "composition_slot_count": int(sum(int(report.get("composition_slot_count", 0)) for report in composition_pass_reports)),
        "composition_optional_count": int(sum(int(report.get("composition_optional_count", 0)) for report in composition_pass_reports)),
        "theme_segment_count": int(len(theme_segments)),
    }

    placement_field_config = load_placement_field_config()
    spatial_hash = UniformSpatialHash(cell_size_m=float(placement_field_config["cell_size_m"]))
    ordered_slot_plans = sorted(slot_plans, key=_slot_placement_sort_key)
    theme_poi_cache: Dict[str, Dict[str, Tuple[Tuple[float, float], ...]]] = {
        segment.theme_id: _theme_poi_points(
            theme_segment=segment,
            theme_segments=theme_segments,
            poi_ctx=poi_ctx,
            road_segment_graph=road_segment_graph,
        )
        for segment in theme_segments
    }
    category_slot_counts: Dict[str, int] = {}
    for slot in ordered_slot_plans:
        category_slot_counts[slot.category] = category_slot_counts.get(slot.category, 0) + 1
    total_scene_slots = max(len(ordered_slot_plans), 1)
    placed_score_sums: Dict[str, float] = {category: 0.0 for category in DEFAULT_CATEGORIES}
    placed_counts: Dict[str, int] = {category: 0 for category in DEFAULT_CATEGORIES}
    slot_index_by_category: Dict[str, int] = {category: 0 for category in DEFAULT_CATEGORIES}
    total_required_slots = sum(
        1 for slot in ordered_slot_plans if bool(getattr(slot, "required", False)) or str(getattr(slot, "anchor_poi_type", "") or "").strip()
    )
    realized_required_slots = 0
    anchor_resolution_summary = {
        "total_anchor_slots": int(sum(1 for slot in ordered_slot_plans if str(getattr(slot, "anchor_poi_type", "") or "").strip())),
        "anchored_exact": 0,
        "anchored_relaxed": 0,
        "unplaced_required": 0,
    }
    unplaced_slot_diagnostics: List[Dict[str, object]] = []

    for slot in ordered_slot_plans:
        category = slot.category
        pool = category_to_rows.get(category, [])
        if not pool:
            dropped_slots += 1
            if bool(getattr(slot, "required", False)) or str(getattr(slot, "anchor_poi_type", "") or "").strip():
                anchor_resolution_summary["unplaced_required"] += 1
                unplaced_slot_diagnostics.append(
                    {
                        "slot_id": str(getattr(slot, "slot_id", "")),
                        "category": str(category),
                        "theme_id": str(getattr(slot, "theme_id", "") or ""),
                        "anchor_poi_type": str(getattr(slot, "anchor_poi_type", "") or ""),
                        "search_tier_reached": "tier_optional_sampling",
                        "best_anchor_distance_m": -1.0,
                        "failure_reason": "no_candidate_after_search",
                        "blocked_reason_counts": {},
                    }
                )
            continue
        theme_segment = theme_by_id.get(str(getattr(slot, "theme_id", "")))
        slot_query = (
            f"{config.query}, {theme_segment.theme_name} streetscape"
            if theme_segment is not None
            else config.query
        )
        feature_ctx = PolicyFeatureContext(
            query=slot_query,
            category=category,
            slot_idx=int(slot_index_by_category.get(category, 0)),
            slot_x=float(slot.x_center_m),
            slot_z=float(slot.z_center_m),
            length_m=float(config.length_m),
            road_width_m=float(resolved_program.road_width_m),
            sidewalk_width_m=float(resolved_program.sidewalk_width_m),
            lane_count=int(resolved_program.lane_count),
            density=float(config.density),
            topk=int(config.topk_per_category),
            used_asset_ids=set(used_asset_ids_by_category.setdefault(category, set())),
            placed_count_in_category=placed_counts.get(category, 0),
            total_slots_in_category=category_slot_counts.get(category, 1),
            category_pool_size=len(pool),
            mean_score_placed=(
                placed_score_sums[category] / placed_counts[category]
                if placed_counts.get(category, 0) > 0
                else 0.0
            ),
            total_slots_in_scene=total_scene_slots,
            **_slot_spatial_kwargs(slot, spatial_ctx),
        )
        row, score, source, decision_details = _pick_category_candidate(
            query=slot_query,
            category=category,
            topk=config.topk_per_category,
            embedder=embedder,
            index_store=index_store,
            asset_by_id=asset_by_id,
            category_pool=pool,
            used_asset_ids=used_asset_ids_by_category.setdefault(category, set()),
            rng=rng,
            config=config,
            placement_policy=policy_used,
            policy_runtime=policy_runtime,
            policy_temperature=policy_temperature,
            feature_context=feature_ctx,
            return_details=True,
        )
        retrieval_predictions.append(
            {
                "target_category": category,
                "theme_id": getattr(slot, "theme_id", ""),
                "hits": decision_details.get("candidates", []),
            }
        )

        band = slot_band_lookup.get(str(slot.slot_id))
        if band is None:
            dropped_slots += 1
            if bool(getattr(slot, "required", False)) or str(getattr(slot, "anchor_poi_type", "") or "").strip():
                anchor_resolution_summary["unplaced_required"] += 1
                unplaced_slot_diagnostics.append(
                    {
                        "slot_id": str(getattr(slot, "slot_id", "")),
                        "category": str(category),
                        "theme_id": str(getattr(slot, "theme_id", "") or ""),
                        "anchor_poi_type": str(getattr(slot, "anchor_poi_type", "") or ""),
                        "search_tier_reached": "tier_optional_sampling",
                        "best_anchor_distance_m": -1.0,
                        "failure_reason": "no_candidate_after_search",
                        "blocked_reason_counts": {},
                    }
                )
            slot_index_by_category[category] = slot_index_by_category.get(category, 0) + 1
            continue

        entry = mesh_cache[row["asset_id"]]
        scale_info = _street_furniture_scale_info(
            category=category,
            entry=entry,
            config=config,
        )
        segment_node = slot_segment_lookup.get(str(slot.slot_id))
        candidate_groups = _iter_slot_candidate_groups(
            slot=slot,
            category=category,
            config=config,
            placement_ctx=placement_ctx,
            segment_node=segment_node,
            theme_segment=theme_segment,
            road_segment_graph=road_segment_graph,
            band_width_m=float(getattr(band, "width_m", 1.0)),
            rng=rng,
        )
        blocked_reason_counts = {
            "overlap_blocked": 0,
            "constraint_vetoed": 0,
            "out_of_sidewalk": 0,
            "out_of_theme_range": 0,
            "side_mismatch": 0,
            "no_candidate_after_search": 0,
        }
        chosen_candidate: Optional[Dict[str, object]] = None
        best_anchor_distance_m = float("inf")
        search_tier_reached = ""
        for candidate_group in candidate_groups:
            if not candidate_group:
                continue
            search_tier_reached = str(candidate_group[0]["tier"])
            feasible_candidates: List[Dict[str, object]] = []
            for candidate in candidate_group:
                if candidate.get("anchor_distance_m") is not None:
                    best_anchor_distance_m = min(best_anchor_distance_m, float(candidate["anchor_distance_m"]))
                resolved_candidate, blocked_reason = _evaluate_slot_candidate(
                    candidate=candidate,
                    slot=slot,
                    category=category,
                    band_width_m=float(getattr(band, "width_m", 1.0)),
                    entry=entry,
                    scale_info=scale_info,
                    placements=placements,
                    spatial_hash=spatial_hash,
                    existing_bboxes=existing_bboxes,
                    placement_ctx=placement_ctx,
                    theme_segment=theme_segment,
                    road_segment_graph=road_segment_graph,
                    theme_poi_points=theme_poi_cache.get(str(getattr(slot, "theme_id", "") or ""), {}),
                    poi_ctx=poi_ctx,
                    rule_set=rule_set,
                    config=config,
                    entrance_registry=entrance_registry,
                    carriageway_boundary=carriageway_boundary,
                    entrance_points_xz=entrance_points_xz,
                )
                if blocked_reason is not None:
                    blocked_reason_counts[blocked_reason] = blocked_reason_counts.get(blocked_reason, 0) + 1
                    continue
                assert resolved_candidate is not None
                feasible_candidates.append(resolved_candidate)
            if feasible_candidates:
                chosen_candidate = max(
                    feasible_candidates,
                    key=lambda item: (
                        float(item["placement_energy"]),
                        -float(item["anchor_distance_m"]) if item.get("anchor_distance_m") is not None else 0.0,
                        -abs(float(item["x"]) - float(getattr(slot, "x_center_m", 0.0) or 0.0)),
                        -abs(float(item["z"]) - float(getattr(slot, "z_center_m", 0.0) or 0.0)),
                    ),
                )
                break

        placed = False
        if chosen_candidate is not None:
            bx = float(chosen_candidate["x"])
            bz = float(chosen_candidate["z"])
            byaw = float(chosen_candidate["yaw_deg"])
            bbbox = tuple(float(value) for value in chosen_candidate["bbox"])
            bpenalty = float(chosen_candidate["constraint_penalty"])
            bfeas = float(chosen_candidate["feasibility_score"])
            bviolated = tuple(chosen_candidate["violated_rules"])
            bscale = float(chosen_candidate.get("scale", 1.0) or 1.0)
            anchor_distance_m = (
                float(chosen_candidate["anchor_distance_m"])
                if chosen_candidate.get("anchor_distance_m") is not None
                else None
            )
            existing_bboxes.append(bbbox)
            spatial_hash.insert(bbbox, len(existing_bboxes) - 1)
            y = -entry.min_y * bscale
            placement_status = _placement_status(
                anchor_distance_m,
                required=bool(getattr(slot, "required", False)) or str(getattr(slot, "anchor_poi_type", "") or "").strip() != "",
                placed=True,
            )
            placements.append(
                StreetPlacement(
                    instance_id=f"inst_{instance_counter:04d}",
                    asset_id=row["asset_id"],
                    category=category,
                    score=float(score),
                    position_xyz=[float(bx), float(y), float(bz)],
                    yaw_deg=float(byaw),
                    scale=float(bscale),
                    bbox_xz=[float(bbbox[0]), float(bbbox[1]), float(bbbox[2]), float(bbbox[3])],
                    selection_source=source,
                    slot_id=str(slot.slot_id),
                    required=bool(getattr(slot, "required", False)),
                    theme_id=str(getattr(slot, "theme_id", "")),
                    anchor_poi_type=str(getattr(slot, "anchor_poi_type", "") or ""),
                    anchor_target_xz=(
                        tuple(float(v) for v in getattr(slot, "anchor_position_xz"))
                        if getattr(slot, "anchor_position_xz", None) is not None
                        else None
                    ),
                    anchor_distance_m=float(anchor_distance_m) if anchor_distance_m is not None else -1.0,
                    placement_energy=float(chosen_candidate["placement_energy"]),
                    placement_status=placement_status,
                    native_size_m=dict(chosen_candidate.get("native_size_m", {}) or {}),
                    canonical_target=dict(chosen_candidate.get("canonical_target", {}) or {}),
                    asset_scale_mode=str(chosen_candidate.get("asset_scale_mode", "")),
                    scale_fallback_used=bool(chosen_candidate.get("scale_fallback_used", False)),
                    constraint_penalty=float(bpenalty),
                    feasibility_score=float(bfeas),
                    violated_rules=bviolated,
                    **_slot_spatial_kwargs(slot, spatial_ctx),
                )
            )
            used_asset_ids_by_category.setdefault(category, set()).add(row["asset_id"])
            placed_score_sums[category] = placed_score_sums.get(category, 0.0) + float(score)
            placed_counts[category] = placed_counts.get(category, 0) + 1
            instance_counter += 1
            placed = True
            entrance_registry.add(
                position_xz=(float(bx), float(bz)),
                category=category,
                bbox_xz=(float(bbbox[0]), float(bbbox[1]), float(bbbox[2]), float(bbbox[3])),
            )
            if bool(getattr(slot, "required", False)) or str(getattr(slot, "anchor_poi_type", "") or "").strip():
                realized_required_slots += 1
            if placement_status == "anchored_exact":
                anchor_resolution_summary["anchored_exact"] += 1
            elif placement_status == "anchored_relaxed":
                anchor_resolution_summary["anchored_relaxed"] += 1

        if not placed:
            dropped_slots += 1
            if bool(getattr(slot, "required", False)) or str(getattr(slot, "anchor_poi_type", "") or "").strip():
                blocked_nonzero = {
                    key: int(value)
                    for key, value in blocked_reason_counts.items()
                    if int(value) > 0
                }
                failure_reason = (
                    sorted(
                        blocked_nonzero.items(),
                        key=lambda item: (-int(item[1]), item[0]),
                    )[0][0]
                    if blocked_nonzero
                    else "no_candidate_after_search"
                )
                anchor_resolution_summary["unplaced_required"] += 1
                unplaced_slot_diagnostics.append(
                    {
                        "slot_id": str(getattr(slot, "slot_id", "")),
                        "category": str(category),
                        "theme_id": str(getattr(slot, "theme_id", "") or ""),
                        "anchor_poi_type": str(getattr(slot, "anchor_poi_type", "") or ""),
                        "search_tier_reached": search_tier_reached or "tier_optional_sampling",
                        "best_anchor_distance_m": float(best_anchor_distance_m) if math.isfinite(best_anchor_distance_m) else -1.0,
                        "failure_reason": failure_reason,
                        "blocked_reason_counts": blocked_nonzero,
                    }
                )
        slot_index_by_category[category] = slot_index_by_category.get(category, 0) + 1

    if not placements:
        raise RuntimeError(
            "Street composition produced zero furniture placements. "
            "Try a different design-rule profile, larger length/density, or check category coverage in manifest."
        )

    surrounding_buildings = _place_surrounding_buildings(
        config=config,
        projected_features=projected,
        placement_ctx=placement_ctx,
        road_segment_graph=road_segment_graph,
        theme_segments=theme_segments,
        resolved_program=resolved_program,
        embedder=embedder,
        index_store=index_store,
        asset_by_id=asset_by_id,
        mesh_cache=mesh_cache,
        rng=rng,
        start_instance_index=instance_counter,
    )
    building_footprints = surrounding_buildings.building_footprints
    generated_lots = surrounding_buildings.generated_lots
    building_plans = list(surrounding_buildings.plans)
    building_retrieval_predictions = list(surrounding_buildings.retrieval_predictions)
    building_summary = dict(surrounding_buildings.building_summary)
    land_use_summary = dict(surrounding_buildings.land_use_summary)
    lot_generation_summary = dict(surrounding_buildings.lot_generation_summary)
    zoning_grid = surrounding_buildings.zoning_grid
    zoning_preview_summary = dict(surrounding_buildings.zoning_preview_summary)
    instance_counter = int(surrounding_buildings.instance_index)
    placements.extend(list(surrounding_buildings.placements))
    resolved_program = replace(
        resolved_program,
        building_strategy_summary={
            **dict(building_strategy_summary),
            **dict(building_summary),
            "land_use_summary": dict(land_use_summary),
            "lot_generation_summary": dict(lot_generation_summary),
        },
    )
    solver_result = replace(solver_result, resolved_program=resolved_program)

    dominant_palette_style = (
        theme_segments[0].style_preset
        if theme_segments and config.layout_mode == "osm"
        else getattr(config, "style_preset", None)
    )
    palette = style_palette(dominant_palette_style)
    rough = surface_roughness(dominant_palette_style)
    scene_texture_tracker = create_scene_texture_tracker(str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")))
    if config.layout_mode == "osm" and placement_ctx is not None:
        scene = _build_osm_base_scene(
            placement_ctx,
            palette=palette,
            roughness=rough,
            texture_mode=str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")),
            texture_tracker=scene_texture_tracker,
        )
    else:
        left_side_width = sum(float(band.width_m) for band in resolved_program.bands if band.side == "left")
        right_side_width = sum(float(band.width_m) for band in resolved_program.bands if band.side == "right")
        scene = _build_base_scene(
            length_m=float(config.length_m),
            road_width_m=float(resolved_program.road_width_m),
            left_side_width_m=float(left_side_width),
            right_side_width_m=float(right_side_width),
            street_program=resolved_program,
            palette=palette,
            roughness=rough,
            texture_mode=str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")),
            texture_tracker=scene_texture_tracker,
        )
    _add_beauty_scene_proxies(
        scene,
        config=config,
        street_program=resolved_program,
        placement_ctx=placement_ctx,
        poi_ctx=poi_ctx,
        placements=placements,
        texture_mode=str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")),
        texture_tracker=scene_texture_tracker,
    )
    _add_instance_meshes(scene=scene, placements=placements, mesh_cache=mesh_cache)

    exclusion_zones: tuple = ()
    debug_scene_overlays_enabled = _should_embed_debug_scene_overlays(config)
    if rule_set is not None and poi_ctx is not None and config.constraint_mode != "off":
        from .poi_rules import build_exclusion_zones as _build_exclusion_zones

        exclusion_zones = _build_exclusion_zones(poi_ctx, rule_set)
        if debug_scene_overlays_enabled:
            _add_poi_markers_and_zones(scene, extract_poi_points_by_type(poi_ctx, suffix="xz"), exclusion_zones)
    elif poi_ctx is not None:
        if debug_scene_overlays_enabled:
            _add_poi_markers_and_zones(scene, extract_poi_points_by_type(poi_ctx, suffix="xz"), ())

    outputs = _export_scene(scene=scene, out_dir=out_dir, export_format=export_format)
    serialized_osm_geometry = (
        _serialize_osm_geometry(placement_ctx)
        if config.layout_mode == "osm" and placement_ctx is not None
        else None
    )
    production_steps = _build_production_steps(
        out_dir=out_dir,
        config=config,
        resolved_program=resolved_program,
        placement_ctx=placement_ctx,
        poi_ctx=poi_ctx,
        spatial_ctx=spatial_ctx,
        placements=placements,
        zoning_grid=zoning_grid,
        building_footprints=building_footprints,
        generated_lots=generated_lots,
        building_plans=building_plans,
        mesh_cache=mesh_cache,
        exclusion_zones=exclusion_zones,
        palette=palette,
        osm_geometry=serialized_osm_geometry,
        overall_texture_tracker=scene_texture_tracker,
    )
    production_steps_dir = (out_dir / "production_steps").resolve()
    production_steps_manifest = (production_steps_dir / "production_steps.json").resolve()
    outputs["production_steps_dir"] = str(production_steps_dir)
    if production_steps_manifest.exists():
        outputs["production_steps_manifest"] = str(production_steps_manifest)

    elapsed_ms_total = (time.perf_counter() - start_perf) * 1000.0
    unique_asset_count = len({placement.asset_id for placement in placements})
    diversity_ratio = float(unique_asset_count / len(placements)) if placements else 0.0
    dropped_slot_rate = compute_dropped_slot_rate(instance_count=len(placements), dropped_slots=int(dropped_slots))
    overlap_rate = compute_overlap_rate([placement.bbox_xz for placement in placements])
    retrieval_top3_category_hit = evaluate_topk_category_hits(retrieval_predictions, topk=3)
    latency_ms_per_instance = compute_latency_ms_per_instance(
        latency_ms_total=elapsed_ms_total,
        instance_count=len(placements),
    )

    furniture_placements = [placement for placement in placements if placement.placement_group == "street_furniture"]
    furniture_dicts = [placement.to_dict() for placement in furniture_placements]
    spacing_uniformity = compute_spacing_uniformity(furniture_dicts)
    style_consistency = compute_style_consistency(furniture_dicts)
    balance_score = compute_balance_score(furniture_dicts)
    slot_side_by_id = {
        str(slot.slot_id): str(getattr(slot, "side", "") or "")
        for slot in slot_plans
        if str(getattr(slot, "slot_id", "") or "")
    }
    street_furniture_side_counts = {"left": 0, "right": 0}
    street_furniture_core_side_counts = {"left": 0, "right": 0}
    for placement in furniture_placements:
        side = str(slot_side_by_id.get(str(placement.slot_id), "") or "")
        if side not in {"left", "right"}:
            side = "left" if float(placement.position_xyz[2]) >= 0.0 else "right"
        street_furniture_side_counts[side] = street_furniture_side_counts.get(side, 0) + 1
        if str(placement.category) in {category for category, side_pref in SIDE_PREF.items() if str(side_pref) == "both"}:
            street_furniture_core_side_counts[side] = street_furniture_core_side_counts.get(side, 0) + 1
    compatible_furnishing_sides = {
        str(slot.side)
        for slot in slot_plans
        if str(slot.category) in {category for category, side_pref in SIDE_PREF.items() if str(side_pref) == "both"}
        and str(slot.side) in {"left", "right"}
    }
    if not {"left", "right"} <= compatible_furnishing_sides:
        missing_side = "left" if "left" not in compatible_furnishing_sides else "right"
        street_furniture_balance_ok = False
        street_furniture_balance_reason = f"no compatible {missing_side} furnishing band"
    elif street_furniture_core_side_counts["left"] > 0 and street_furniture_core_side_counts["right"] > 0:
        street_furniture_balance_ok = True
        street_furniture_balance_reason = ""
    elif sum(street_furniture_core_side_counts.values()) <= 0:
        street_furniture_balance_ok = False
        street_furniture_balance_reason = "no placed bilateral street furniture"
    else:
        missing_side = "left" if street_furniture_core_side_counts["left"] <= 0 else "right"
        street_furniture_balance_ok = False
        street_furniture_balance_reason = f"no placed core street furniture on {missing_side} side"
    per_category_unique = {
        category: len({placement.asset_id for placement in placements if placement.category == category})
        for category in sorted({placement.category for placement in placements})
        if any(placement.category == category for placement in placements)
    }
    selection_source_counts: Dict[str, int] = {}
    asset_generator_type_counts: Dict[str, int] = {}
    asset_source_counts: Dict[str, int] = {}
    asset_source_unique_assets: Dict[str, set[str]] = {}
    asset_source_categories: Dict[str, set[str]] = {}
    asset_source_generator_types: Dict[str, set[str]] = {}
    parametric_instance_count = 0
    for placement in placements:
        selection_source_counts[placement.selection_source] = selection_source_counts.get(placement.selection_source, 0) + 1
        generator_key = (
            asset_generator_type(asset_by_id[placement.asset_id])
            if placement.asset_id in asset_by_id
            else "procedural_fallback" if placement.selection_source == "procedural_fallback" else "unknown"
        )
        source_key = _placement_asset_source_key(
            asset_by_id.get(placement.asset_id),
            selection_source=placement.selection_source,
        )
        asset_generator_type_counts[generator_key] = asset_generator_type_counts.get(generator_key, 0) + 1
        asset_source_counts[source_key] = asset_source_counts.get(source_key, 0) + 1
        asset_source_unique_assets.setdefault(source_key, set()).add(placement.asset_id)
        asset_source_categories.setdefault(source_key, set()).add(str(placement.category))
        asset_source_generator_types.setdefault(source_key, set()).add(str(generator_key))
        if generator_key == "parametric":
            parametric_instance_count += 1
    asset_library_scene_instances = sum(
        1
        for placement in placements
        if bool(mesh_cache.get(placement.asset_id)) and bool(mesh_cache[placement.asset_id].is_scene)
    )

    violations_total = sum(1 for placement in furniture_placements if placement.violated_rules)
    compliance_rate_total = 1.0 - (violations_total / len(furniture_placements)) if furniture_placements else 1.0
    avg_constraint_penalty = (
        sum(placement.constraint_penalty for placement in furniture_placements) / len(furniture_placements)
        if furniture_placements
        else 0.0
    )
    avg_feasibility_score = (
        sum(placement.feasibility_score for placement in furniture_placements) / len(furniture_placements)
        if furniture_placements
        else 1.0
    )
    rule_violation_counts: Dict[str, int] = {}
    for placement in furniture_placements:
        for rule_name in placement.violated_rules:
            rule_violation_counts[rule_name] = rule_violation_counts.get(rule_name, 0) + 1

    rule_satisfaction_rate = compute_rule_satisfaction_rate(solver_result.rule_evaluations)
    entrance_report = evaluate_all_entrances(
        entrance_points_xz=entrance_points_xz,
        registry=entrance_registry,
        carriageway_boundary=carriageway_boundary,
    )
    presentation_report = compute_presentation_report(
        placements,
        asset_by_id=asset_by_id,
        config=config,
        poi_context=poi_ctx,
        composition_report=composition_pass_report,
    )
    mean_entrance_openness = float(entrance_report.mean_openness)
    mean_noise_shielding = float(entrance_report.mean_shielding)
    topology_validity = compute_topology_validity(solver_result.topology_validity)
    cross_section_feasibility = compute_cross_section_feasibility(solver_result.cross_section_feasibility)
    editability = compute_editability(solver_result.edits)
    conflict_explainability = compute_explainability(solver_result.conflicts)
    rule_evaluation_counts: Dict[str, int] = {}
    for evaluation in solver_result.rule_evaluations:
        rule_evaluation_counts[evaluation.status] = rule_evaluation_counts.get(evaluation.status, 0) + 1

    program_fallback_reason = " | ".join(dict.fromkeys(reason for reason in program_fallback_reasons if reason))
    layout_path = (out_dir / "scene_layout.json").resolve()
    selected_highway_type = ""
    if projected is not None and getattr(projected, "roads", None):
        selected_highway_type = str(getattr(projected.roads[0], "highway_type", "") or "").strip().lower()
    from .placement_zones import summarize_road_selection

    road_selection_summary = summarize_road_selection(
        strategy=str(getattr(config, "road_selection", "walkable_neighborhood")),
        selected_highway_type=selected_highway_type,
    )
    asset_scale_summary = summarize_asset_scales([placement.to_dict() for placement in placements])
    asymmetry_raw = getattr(config, "land_use_asymmetry_strength", 0.0)
    bias_raw = getattr(config, "left_right_bias", 0.0)
    setback_min_raw = getattr(config, "building_front_setback_min_m", 1.0)
    setback_max_raw = getattr(config, "building_front_setback_max_m", 2.0)
    zoning_granularity_raw = getattr(config, "zoning_granularity", "fine")
    streetwall_continuity_raw = getattr(config, "streetwall_continuity", 0.95)
    infill_policy_raw = getattr(config, "infill_policy", "aggressive")
    summary_payload = {
        "instance_count": len(placements),
        "dropped_slots": int(dropped_slots),
        "dropped_slot_rate": float(dropped_slot_rate),
        "unique_asset_count": int(unique_asset_count),
        "diversity_ratio": float(diversity_ratio),
        "overlap_rate": float(overlap_rate),
        "retrieval_top3_category_hit": float(retrieval_top3_category_hit),
        "policy_used": policy_used,
        "latency_ms_total": float(elapsed_ms_total),
        "latency_ms_per_instance": float(latency_ms_per_instance),
        "per_category_unique": per_category_unique,
        "selection_source_counts": selection_source_counts,
        "asset_generator_type_counts": asset_generator_type_counts,
        "asset_source_counts": asset_source_counts,
        "asset_source_unique_counts": {
            source_key: int(len(asset_ids))
            for source_key, asset_ids in asset_source_unique_assets.items()
        },
        "asset_scale_mode": str(getattr(config, "asset_scale_mode", "canonical_v1")),
        "asset_scale_summary": asset_scale_summary,
        "asset_usage_by_source": [
            {
                "source": str(source_key),
                "instance_count": int(asset_source_counts.get(source_key, 0)),
                "unique_asset_count": int(len(asset_source_unique_assets.get(source_key, set()))),
                "categories": sorted(category for category in asset_source_categories.get(source_key, set()) if category),
                "generator_types": sorted(
                    generator_type
                    for generator_type in asset_source_generator_types.get(source_key, set())
                    if generator_type
                ),
                "asset_ids": sorted(asset_source_unique_assets.get(source_key, set())),
            }
            for source_key in sorted(
                asset_source_counts,
                key=lambda key: (-int(asset_source_counts.get(key, 0)), str(key)),
            )
        ],
        "parametric_instance_count": int(parametric_instance_count),
        "asset_library_scene_instances": int(asset_library_scene_instances),
        "production_step_count": int(len(production_steps)),
        "production_step_ids": [record.step_id for record in production_steps],
        "final_production_step_id": production_steps[-1].step_id if production_steps else "",
        "scene_texture_mode": str(getattr(config, "scene_texture_mode", "topdown_tiles_v1")),
        "scene_texture_pack": scene_texture_pack_name(str(getattr(config, "scene_texture_mode", "topdown_tiles_v1"))),
        "scene_texture_fallback_used": bool(scene_texture_tracker.fallback_used),
        "scene_texture_missing_assets": sorted(scene_texture_tracker.missing_assets),
        "layout_mode": config.layout_mode,
        "constraint_mode": config.constraint_mode,
        "aoi_bbox": list(config.aoi_bbox) if config.aoi_bbox else None,
        "compliance_rate_total": float(compliance_rate_total),
        "violations_total": int(violations_total),
        "rule_violation_counts": rule_violation_counts,
        "avg_constraint_penalty": float(avg_constraint_penalty),
        "avg_feasibility_score": float(avg_feasibility_score),
        "spacing_uniformity": float(spacing_uniformity),
        "style_consistency": float(style_consistency),
        "balance_score": float(balance_score),
        "design_rule_profile": str(config.design_rule_profile),
        "objective_profile": str(getattr(config, "objective_profile", "balanced")),
        "ped_demand_level": str(getattr(config, "ped_demand_level", "medium")),
        "bike_demand_level": str(getattr(config, "bike_demand_level", "low")),
        "transit_demand_level": str(getattr(config, "transit_demand_level", "medium")),
        "vehicle_demand_level": str(getattr(config, "vehicle_demand_level", "medium")),
        "program_generator_requested": str(config.program_generator),
        "program_generator_used": str(program_used),
        "layout_solver_requested": str(config.layout_solver),
        "layout_solver_used": str(solver_result.backend_used),
        "selected_highway_type": road_selection_summary["selected_highway_type"],
        "road_selection_requested": road_selection_summary["road_selection_requested"],
        "road_selection_used": road_selection_summary["road_selection_used"],
        "road_selection_fallback_reason": road_selection_summary["road_selection_fallback_reason"],
        "solver_backend_requested": str(solver_result.backend_requested),
        "solver_backend_used": str(solver_result.backend_used),
        "cross_section_type": str(resolved_program.cross_section_type),
        "road_width_m": float(resolved_program.road_width_m),
        "sidewalk_width_m": float(resolved_program.sidewalk_width_m),
        "length_m": float(config.length_m),
        "carriageway_width_m": float(resolved_program.road_width_m),
        "left_clear_path_width_m": float(resolved_program.left_clear_path_width_m),
        "right_clear_path_width_m": float(resolved_program.right_clear_path_width_m),
        "left_furnishing_width_m": float(resolved_program.left_furnishing_width_m),
        "right_furnishing_width_m": float(resolved_program.right_furnishing_width_m),
        "row_width_m": float(resolved_program.row_width_m),
        "width_expanded": bool(resolved_program.width_expanded),
        "width_reallocation_reason": str(resolved_program.width_reallocation_reason),
        "poi_fit_feasible": bool(resolved_program.poi_fit_feasible),
        "poi_fit_report": dict(resolved_program.poi_fit_report),
        "rule_satisfaction_rate": float(rule_satisfaction_rate),
        "topology_validity": float(topology_validity),
        "cross_section_feasibility": float(cross_section_feasibility),
        "editability": float(editability),
        "conflict_explainability": float(conflict_explainability),
        "active_constraints": list(solver_result.active_constraints),
        "throughput_feasibility": dict(solver_result.throughput_feasibility),
        "objective_score_breakdown": dict(solver_result.objective_score_breakdown),
        "band_solution_count": int(len(solver_result.band_solutions)),
        "solver_edit_count": int(len(solver_result.edits)),
        "solver_conflict_count": int(len(solver_result.conflicts)),
        "rule_evaluation_counts": rule_evaluation_counts,
        "program_fallback_reason": program_fallback_reason,
        "solver_fallback_reason": str(solver_result.fallback_reason),
        "road_segment_graph_summary": solver_result.road_segment_graph_summary,
        "mean_entrance_openness": float(mean_entrance_openness),
        "mean_noise_shielding": float(mean_noise_shielding),
        "entrances_below_openness_threshold": int(entrance_report.entrances_below_openness_threshold),
        "min_entrance_openness": float(entrance_report.min_openness),
        "entrance_count": len(entrance_points_xz),
        "selected_road_osm_id": int(config.selected_road_osm_id) if config.selected_road_osm_id is not None else None,
        "selected_road_discovered_poi_count": (
            int(config.selected_road_discovered_poi_count)
            if config.selected_road_discovered_poi_count is not None
            else None
        ),
        "selected_road_discovered_poi_score": (
            float(config.selected_road_discovered_poi_score)
            if config.selected_road_discovered_poi_score is not None
            else None
        ),
        "selected_road_discovered_core_poi_count": (
            int(config.selected_road_discovered_core_poi_count)
            if config.selected_road_discovered_core_poi_count is not None
            else None
        ),
        "selected_road_effective_poi_count": int(sum(int(value) for value in effective_poi_counts.values())),
        "selected_road_effective_poi_score": float(poi_weighted_score(effective_poi_counts)),
        "selected_road_core_poi_count": int(core_poi_count(effective_poi_counts)),
        "selected_road_required_left_width_m": float(getattr(placement_ctx, "required_left_width_m", 0.0) or 0.0),
        "selected_road_required_right_width_m": float(getattr(placement_ctx, "required_right_width_m", 0.0) or 0.0),
        "selected_road_final_row_width_m": float(getattr(placement_ctx, "row_width_m", resolved_program.row_width_m) or 0.0),
        "observed_poi_counts": dict(resolved_program.observed_poi_counts),
        "style_preset": str(
            resolved_program.context_conditions.get("style_preset", getattr(config, "style_preset", "civic_clean_v1"))
        ),
        "beauty_mode": str(getattr(config, "beauty_mode", "presentation_v1")),
        "render_preset": str(getattr(config, "render_preset", "jury_default_v1")),
        "asset_curation_mode": str(getattr(config, "asset_curation_mode", "scene_ready_first")),
        "tree_assets_unavailable": bool(tree_assets_unavailable),
        "tree_inventory_raw_count": int(raw_tree_inventory_count),
        "tree_inventory_scene_ready_count": int(len(category_to_rows.get("tree", ()))),
        "parametric_tree_fallback_count": int(parametric_tree_count),
        "scene_debug_overlays_enabled": bool(debug_scene_overlays_enabled),
        "theme_segments": [segment.to_dict() for segment in theme_segments],
        "theme_segment_count": int(len(theme_segments)),
        "theme_diagnostics": {
            "theme_inference_mode": str(getattr(config, "theme_inference_mode", "deterministic_auto")),
            "theme_vocab_name": str(getattr(config, "theme_vocab_name", "fixed_v1")),
            "zone_programs": theme_zone_programs,
        },
        "placement_force_model": {
            "version": str(placement_field_config.get("version", "placement_field_v1")),
            "config_path": str(placement_field_path()),
            "cell_size_m": float(placement_field_config.get("cell_size_m", 4.0)),
            "constraint_mode": str(config.constraint_mode),
        },
        "anchor_resolution_summary": {
            **dict(anchor_resolution_summary),
            "total_required_slots": int(total_required_slots),
            "realized_required_slots": int(realized_required_slots),
        },
        "required_slot_realization_rate": (
            float(realized_required_slots / total_required_slots)
            if total_required_slots > 0
            else 1.0
        ),
        "unplaced_required_slot_count": int(anchor_resolution_summary["unplaced_required"]),
        "building_generation_mode": str(getattr(config, "surrounding_building_mode", "grid_growth")),
        "land_use_asymmetry_strength": float(0.0 if asymmetry_raw is None else asymmetry_raw),
        "left_right_bias": float(0.0 if bias_raw is None else bias_raw),
        "building_front_setback_min_m": float(1.0 if setback_min_raw is None else setback_min_raw),
        "building_front_setback_max_m": float(2.0 if setback_max_raw is None else setback_max_raw),
        "zoning_granularity": str("fine" if zoning_granularity_raw is None else zoning_granularity_raw),
        "streetwall_continuity": float(0.95 if streetwall_continuity_raw is None else streetwall_continuity_raw),
        "infill_policy": str("aggressive" if infill_policy_raw is None else infill_policy_raw),
        "building_balance_policy": str(building_summary.get("building_balance_policy", "")),
        "building_balance_ok": bool(building_summary.get("building_balance_ok", False)),
        "building_balance_reason": str(building_summary.get("building_balance_reason", "") or ""),
        "frontage_balance_gap": float(building_summary.get("frontage_balance_gap", 0.0) or 0.0),
        "buildable_frontage_by_side": dict(building_summary.get("buildable_frontage_by_side", {}) or {}),
        "zoning_preview_mode": str(zoning_preview_summary.get("zoning_preview_mode", "parcel_first") or "parcel_first"),
        "frontage_cell_count": int(zoning_preview_summary.get("frontage_cell_count", 0) or 0),
        "frontage_parcel_count": int(lot_generation_summary.get("frontage_parcel_count", len(generated_lots)) or 0),
        "infill_footprint_count": int(building_summary.get("infill_footprint_count", 0) or 0),
        "frontage_coverage_by_side": dict(building_summary.get("frontage_coverage_by_side", {}) or {}),
        "frontage_gap_stats_by_side": dict(building_summary.get("frontage_gap_stats_by_side", {}) or {}),
        "street_furniture_side_counts": dict(street_furniture_side_counts),
        "street_furniture_core_side_counts": dict(street_furniture_core_side_counts),
        "street_furniture_balance_ok": bool(street_furniture_balance_ok),
        "street_furniture_balance_reason": str(street_furniture_balance_reason),
        "building_summary": dict(building_summary),
        "land_use_summary": dict(land_use_summary),
        "lot_generation_summary": dict(lot_generation_summary),
        "building_retrieval_coverage": {
            "footprint_count": int(building_summary.get("footprint_count", 0)),
            "lot_count": int(building_summary.get("lot_count", 0)),
            "target_count": int(building_summary.get("target_count", 0)),
            "target_type": str(building_summary.get("target_type", "")),
            "placed_count": int(building_summary.get("placed_count", 0)),
            "asset_count": int(building_summary.get("asset_count", 0)),
            "fallback_count": int(building_summary.get("fallback_count", 0)),
            "real_footprint_count": int(building_summary.get("real_footprint_count", 0)),
            "infill_footprint_count": int(building_summary.get("infill_footprint_count", 0)),
        },
        "zoning_preview_summary": dict(zoning_preview_summary),
        "composition_report": {
            **dict(composition_pass_report),
            **dict(presentation_report),
        },
        "spatial_context": {
            "junction_points_xz": [list(p) for p in spatial_ctx.junction_points_xz],
            "entrance_points_xz": [list(p) for p in spatial_ctx.entrance_points_xz],
            "bus_stop_points_xz": [list(p) for p in spatial_ctx.bus_stop_points_xz],
            "fire_points_xz": [list(p) for p in spatial_ctx.fire_points_xz],
            "poi_points_by_type_xz": {
                poi_type: [list(point) for point in points]
                for poi_type, points in nonempty_poi_points(spatial_ctx.poi_points_by_type_xz).items()
            },
            "road_half_width_m": float(resolved_program.road_width_m / 2.0),
            "length_m": float(spatial_ctx.length_m),
        },
    }
    if serialized_osm_geometry is not None:
        summary_payload["osm_geometry"] = serialized_osm_geometry

    summary_payload["poi_exclusion_zones"] = [
        {
            "poi_type": z.poi_type,
            "position_xz": [round(z.position_xz[0], 3), round(z.position_xz[1], 3)],
            "radius_m": round(z.radius_m, 3),
            "rule_name": z.rule_name,
        }
        for z in exclusion_zones
    ]
    summary_payload["poi_conflict_assets"] = [
        {
            "instance_id": p.instance_id,
            "slot_id": p.slot_id,
            "category": p.category,
            "position_xz": [round(float(p.position_xyz[0]), 3), round(float(p.position_xyz[2]), 3)],
            "violated_rules": list(p.violated_rules),
            "constraint_penalty": round(float(p.constraint_penalty), 4),
        }
        for p in placements
        if p.violated_rules
    ]

    program_generation_payload = program_result.to_dict()
    program_generation_payload["theme_zone_programs"] = list(theme_zone_programs)
    layout_payload = {
        "query": config.query,
        "config": config.to_dict(),
        "program_generation": program_generation_payload,
        "street_program": resolved_program.to_dict(),
        "constraint_set": base_constraint_set.to_dict(),
        "solver": solver_result.to_dict(),
        "summary": summary_payload,
        "placements": [placement.to_dict() for placement in placements],
        "building_footprints": [footprint.to_dict() for footprint in building_footprints],
        "generated_lots": [lot.to_dict() for lot in generated_lots],
        "building_placements": [plan.to_dict() for plan in building_plans],
        "building_retrieval_predictions": building_retrieval_predictions,
        "zoning_grid": list(zoning_grid),
        "production_steps": [record.to_dict() for record in production_steps],
        "unplaced_slot_diagnostics": list(unplaced_slot_diagnostics),
        "outputs": outputs,
        "supervision_sample": {
            "inputs": {
                "config": config.to_dict(),
                "inventory_summary": inventory_summary.to_dict(),
                "constraint_set": base_constraint_set.to_dict(),
                "road_segment_graph_summary": solver_result.road_segment_graph_summary,
                "observed_poi_counts": dict(resolved_program.observed_poi_counts),
            },
            "labels": {
                "resolved_program": resolved_program.to_dict(),
                "band_solutions": [band.to_dict() for band in solver_result.band_solutions],
                "slot_plans": [slot.to_dict() for slot in solver_result.slot_plans],
                "objective_profile": str(resolved_program.objective_profile),
            },
        },
    }
    layout_payload["summary"].update(presentation_report)
    scene_graph = build_scene_graph(layout_payload, road_segment_graph=road_segment_graph)
    layout_payload["scene_graph"] = scene_graph
    layout_payload["summary"]["scene_graph_node_count"] = int(len(scene_graph.get("nodes", []) or []))
    layout_payload["summary"]["scene_graph_edge_count"] = int(len(scene_graph.get("edges", []) or []))
    layout_payload["summary"]["scene_graph_available_categories"] = list(
        scene_graph.get("filters", {}).get("categories", []) or []
    )
    render_views = render_presentation_views(layout_payload, out_dir=out_dir, config=config)
    layout_payload["summary"]["render_views"] = render_views
    for view in render_views:
        if str(view.get("path", "")).strip():
            outputs[f"presentation_{view.get('name', 'view')}"] = str(view["path"])

    layout_path.write_text(json.dumps(layout_payload, indent=2, ensure_ascii=True), encoding="utf-8")

    outputs["scene_layout"] = str(layout_path)
    outputs["policy_used"] = policy_used
    outputs["design_rule_profile"] = str(config.design_rule_profile)
    outputs["objective_profile"] = str(getattr(config, "objective_profile", "balanced"))
    outputs["program_cross_section_type"] = str(resolved_program.cross_section_type)
    outputs["program_generator_requested"] = str(config.program_generator)
    outputs["program_generator_used"] = str(program_used)
    outputs["layout_solver_requested"] = str(config.layout_solver)
    outputs["layout_solver_used"] = str(solver_result.backend_used)
    if solver_result.fallback_reason:
        outputs["solver_fallback_reason"] = str(solver_result.fallback_reason)
    if policy_fallback_reason:
        outputs["policy_fallback_reason"] = policy_fallback_reason
    if program_fallback_reason:
        outputs["program_fallback_reason"] = program_fallback_reason
    return StreetComposeResult(
        query=config.query,
        instance_count=len(placements),
        dropped_slots=int(dropped_slots),
        placements=placements,
        outputs=outputs,
        street_program=resolved_program,
        solver_result=solver_result,
    )
