"""Helpers for turning a confirmed design draft into a generated street scene."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Sequence

from ..json_safe import make_json_safe
from ..semantic_design_layers import (
    apply_street_furniture_profile_defaults,
    normalize_street_furniture_profile,
    street_furniture_profile_config_patch,
)
from ..capture_3d import capture_views_for_layout
from ..graph_template_scene_bridge import build_graph_template_scene_bridge
from ..metaurban_scene_bridge import build_metaurban_scene_bridge
from ..reference_annotation_scene_bridge import build_reference_annotation_scene_bridge
from ..street_layout import compose_street_scene
from ..types import StreetComposeConfig
from ..web_viewer_dev import build_web_viewer_url, cache_scene_layout_for_viewer
from .design_types import (
    DEFAULT_COMPOSE_CONFIG_PATCH_VALUES,
    DesignDraft,
    SceneContext,
    SceneGenerationOptions,
    SceneGenerationResult,
    sanitize_compose_config_patch,
    sanitize_scene_context,
)
from .scene_backends import (
    DEFAULT_GROUND_MATERIAL_MANIFEST_PATH,
    DEFAULT_SKY_MANIFEST_PATH,
    ManifestGroundMaterialBackend,
    ManifestObjectAssetBackend,
    ManifestSkyBackend,
)
from .scene_context_service import ResolvedSceneContext, resolve_scene_context


logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_METAURBAN_REFERENCE_PLAN_ID = "hkust_gz_gate"
DEFAULT_GRAPH_TEMPLATE_ID = "hkust_gz_gate"
DEFAULT_CLIP_MODEL_DIR = (ROOT / "models" / "clip-vit-base-patch32").resolve()
DEFAULT_OBJECT_MANIFEST_PATH = (ROOT / "data" / "street_furniture" / "street_furniture_manifest.jsonl").resolve()
DEFAULT_SCENE_GENERATION_OPTIONS = SceneGenerationOptions(
    manifest_path=DEFAULT_OBJECT_MANIFEST_PATH,
    artifacts_dir=(ROOT / "artifacts" / "real").resolve(),
    out_dir=(ROOT / "artifacts" / "real").resolve(),
    manifest_paths=(DEFAULT_OBJECT_MANIFEST_PATH,),
    object_manifest_v2_path=None,
    ground_material_manifest_path=DEFAULT_GROUND_MATERIAL_MANIFEST_PATH,
    sky_manifest_path=DEFAULT_SKY_MANIFEST_PATH,
    model_name="openai/clip-vit-base-patch32",
    model_dir=DEFAULT_CLIP_MODEL_DIR,
    local_files_only=True,
    device="cpu",
    export_format="glb",
    placement_policy="rule",
    policy_ckpt=None,
    program_ckpt=None,
    policy_temperature=0.12,
)

ProgressCallback = Callable[[Mapping[str, Any]], None]

_STYLE_BLEND_MARKERS = (
    "融合",
    "结合",
    "兼顾",
    "叠加",
    "加入",
    "增加",
    "blend",
    "mix",
    "combine",
    "integrate",
    "merge",
)
_STYLE_TRANSFER_MARKERS = (
    "转为",
    "转成",
    "改为",
    "改成",
    "变为",
    "变成",
    "切换为",
    "切换成",
    "转换为",
    "转换成",
    "switch to",
    "convert to",
    "change to",
    "transform to",
)
_STYLE_TRANSFER_TARGET_KEYWORDS: Mapping[str, tuple[str, ...]] = {
    "balanced_complete": (
        "balanced_complete",
        "balanced complete",
        "balanced street",
        "complete street",
        "平衡街道",
        "平衡完整",
        "完整街道",
    ),
    "pedestrian_friendly": (
        "pedestrian_friendly",
        "pedestrian friendly",
        "pedestrian priority",
        "walkable",
        "步行友好",
        "慢行友好",
        "行人优先",
        "步行优先",
    ),
    "commercial_vitality": (
        "commercial_vitality",
        "commercial vitality",
        "commerce",
        "retail",
        "商业活力",
        "商业友好",
        "商业街道",
    ),
    "transit_priority": (
        "transit_priority",
        "transit priority",
        "bus priority",
        "bus-oriented",
        "transit-oriented",
        "公交优先",
        "公交导向",
        "公交友好",
        "公交设施",
    ),
    "park_landscape": (
        "park_landscape",
        "park landscape",
        "park-like",
        "green landscape",
        "公园景观",
        "公园风格",
        "绿化景观",
        "绿地景观",
    ),
    "quiet_residential": (
        "quiet_residential",
        "quiet residential",
        "residential",
        "安静居住",
        "居住街道",
        "住宅街道",
        "住宅区",
    ),
}
_STYLE_BLEND_TARGET_SOURCE = "style_blend_target"
_STYLE_BLEND_REASON = "style_blend_target"
_STYLE_TRANSFER_TARGET_SOURCE = "style_transfer_target"
_STYLE_TRANSFER_REASON = "style_transfer_target"


def _emit_progress(
    progress_callback: ProgressCallback | None,
    *,
    stage: str,
    progress: int,
    message: str,
    **detail: Any,
) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback({
            "stage": stage,
            "progress": int(progress),
            "message": message,
            "detail": dict(detail),
        })
    except Exception:
        # Progress reporting is best-effort and must not fail scene generation.
        return


def build_compose_config_from_draft(
    draft: DesignDraft,
    *,
    patch_overrides: Mapping[str, Any] | None = None,
) -> StreetComposeConfig:
    """Merge a confirmed design draft onto stable scene-generation defaults."""

    patch = sanitize_compose_config_patch(draft.compose_config_patch)
    patch.update(sanitize_compose_config_patch(patch_overrides))
    patch = apply_street_furniture_profile_defaults(patch)
    normalized_query = str(
        patch.get("query")
        or draft.normalized_scene_query
        or "walkable complete street"
    ).strip()
    return StreetComposeConfig(
        query=normalized_query,
        length_m=float(patch.get("length_m", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["length_m"])),
        road_width_m=float(patch.get("road_width_m", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["road_width_m"])),
        sidewalk_width_m=float(patch.get("sidewalk_width_m", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["sidewalk_width_m"])),
        lane_count=int(patch.get("lane_count", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["lane_count"])),
        density=float(patch.get("density", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["density"])),
        building_density=float(patch.get("building_density", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["building_density"])),
        building_max_per_100m=float(patch.get("building_max_per_100m", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["building_max_per_100m"])),
        seed=int(patch.get("seed", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES.get("seed", 42))),
        topk_per_category=20,
        max_trials_per_slot=30,
        design_rule_profile=str(patch.get("design_rule_profile", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["design_rule_profile"])),
        target_street_type=str(patch.get("target_street_type", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["target_street_type"])),
        objective_profile=str(patch.get("objective_profile", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["objective_profile"])),
        city_context=str(patch.get("city_context", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["city_context"])),
        style_preset=str(patch.get("style_preset", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["style_preset"])),
        beauty_mode=str(patch.get("beauty_mode", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["beauty_mode"])),
        render_preset=str(patch.get("render_preset", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["render_preset"])),
        topdown_render_mode=str(patch.get("topdown_render_mode", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["topdown_render_mode"])),
        scene_texture_mode=str(patch.get("scene_texture_mode", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["scene_texture_mode"])),
        asset_curation_mode=str(patch.get("asset_curation_mode", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["asset_curation_mode"])),
        asset_scale_mode=str(patch.get("asset_scale_mode", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["asset_scale_mode"])),
        curated_street_assets_profile=str(patch.get("curated_street_assets_profile", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["curated_street_assets_profile"])),
        furniture_balance_policy=str(patch.get("furniture_balance_policy", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["furniture_balance_policy"])),
        street_furniture_distribution_policy=str(patch.get("street_furniture_distribution_policy", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["street_furniture_distribution_policy"])),
        program_generator=str(patch.get("program_generator", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["program_generator"])),
        layout_solver=str(patch.get("layout_solver", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["layout_solver"])),
        ped_demand_level=str(patch.get("ped_demand_level", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["ped_demand_level"])),
        bike_demand_level=str(patch.get("bike_demand_level", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["bike_demand_level"])),
        transit_demand_level=str(patch.get("transit_demand_level", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["transit_demand_level"])),
        vehicle_demand_level=str(patch.get("vehicle_demand_level", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["vehicle_demand_level"])),
        allow_solver_fallback=bool(patch.get("allow_solver_fallback", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["allow_solver_fallback"])),
        segment_length_m=float(patch.get("segment_length_m", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["segment_length_m"])),
        osm_semantic_mode=str(patch.get("osm_semantic_mode", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["osm_semantic_mode"])),
        skeleton_design_profile=str(patch.get("skeleton_design_profile", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["skeleton_design_profile"])),
        skeleton_design_profile_source=str(patch.get("skeleton_design_profile_source", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["skeleton_design_profile_source"])),
        skeleton_design_profile_confidence=float(patch.get("skeleton_design_profile_confidence", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["skeleton_design_profile_confidence"])),
        skeleton_design_profile_reasons=tuple(patch.get("skeleton_design_profile_reasons", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["skeleton_design_profile_reasons"])),
        street_furniture_profile=str(patch.get("street_furniture_profile", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["street_furniture_profile"])),
        street_furniture_profile_source=str(patch.get("street_furniture_profile_source", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["street_furniture_profile_source"])),
        street_furniture_profile_confidence=float(patch.get("street_furniture_profile_confidence", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["street_furniture_profile_confidence"])),
        street_furniture_profile_reasons=tuple(patch.get("street_furniture_profile_reasons", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["street_furniture_profile_reasons"])),
        osm_multiblock_max_roads=int(patch.get("osm_multiblock_max_roads", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["osm_multiblock_max_roads"])),
        osm_multiblock_max_extent_m=float(patch.get("osm_multiblock_max_extent_m", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["osm_multiblock_max_extent_m"])),
        osm_short_road_policy=str(patch.get("osm_short_road_policy", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["osm_short_road_policy"])),
        osm_short_road_min_length_m=float(patch.get("osm_short_road_min_length_m", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["osm_short_road_min_length_m"])),
        osm_context_fit_mode=str(patch.get("osm_context_fit_mode", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["osm_context_fit_mode"])),
        bus_stop_eligible_road_names=tuple(patch.get("bus_stop_eligible_road_names", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["bus_stop_eligible_road_names"])),
        max_bus_stops_per_scene=int(patch.get("max_bus_stops_per_scene", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["max_bus_stops_per_scene"])),
        allow_demo_bus_stop_when_osm_absent=bool(patch.get("allow_demo_bus_stop_when_osm_absent", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["allow_demo_bus_stop_when_osm_absent"])),
        max_styles_per_category=int(patch.get("max_styles_per_category", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["max_styles_per_category"])),
        amenity_coverage_mode=str(patch.get("amenity_coverage_mode", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["amenity_coverage_mode"])),
        minimum_category_presence=tuple(patch.get("minimum_category_presence", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["minimum_category_presence"])),
        optional_category_presence=tuple(patch.get("optional_category_presence", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["optional_category_presence"])),
    )


def normalize_scene_generation_options(
    overrides: Mapping[str, Any] | None = None,
) -> SceneGenerationOptions:
    """Coerce request overrides into scene-generation options."""

    if not overrides:
        return DEFAULT_SCENE_GENERATION_OPTIONS
    payload = dict(overrides)

    def _resolve_optional_path(value: object, fallback: Path | None) -> Path | None:
        if value in (None, ""):
            return fallback
        return Path(str(value)).expanduser().resolve()

    def _resolve_manifest_paths(payload: Mapping[str, Any]) -> tuple[Path, ...]:
        raw_paths = payload.get("manifest_paths")
        if raw_paths in (None, ""):
            raw_single = payload.get("manifest_path")
            if raw_single not in (None, ""):
                raw_paths = [raw_single]
            else:
                return tuple(DEFAULT_SCENE_GENERATION_OPTIONS.manifest_paths)
        if isinstance(raw_paths, str):
            items = [item.strip() for item in raw_paths.replace(";", ",").split(",")]
        elif isinstance(raw_paths, Sequence):
            items = [str(item).strip() for item in raw_paths]
        else:
            items = []
        resolved = tuple(Path(item).expanduser().resolve() for item in items if item)
        return resolved or tuple(DEFAULT_SCENE_GENERATION_OPTIONS.manifest_paths)

    def _resolve_optional_int(value: object) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _resolve_bool(value: object, fallback: bool) -> bool:
        if value in (None, ""):
            return bool(fallback)
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "no", "off"}
        return bool(value)

    def _resolve_resolution(value: object, fallback: tuple[int, int]) -> tuple[int, int]:
        if isinstance(value, str):
            parts = value.lower().replace("x", ",").split(",")
        elif isinstance(value, Sequence):
            parts = list(value)
        else:
            parts = list(fallback)
        if len(parts) < 2:
            return fallback
        try:
            width = max(64, min(4096, int(float(parts[0]))))
            height = max(64, min(4096, int(float(parts[1]))))
        except (TypeError, ValueError):
            return fallback
        return (width, height)

    manifest_paths = _resolve_manifest_paths(payload)
    return SceneGenerationOptions(
        manifest_path=manifest_paths[0],
        artifacts_dir=Path(str(payload.get("artifacts_dir", DEFAULT_SCENE_GENERATION_OPTIONS.artifacts_dir))).expanduser().resolve(),
        out_dir=Path(str(payload.get("out_dir", DEFAULT_SCENE_GENERATION_OPTIONS.out_dir))).expanduser().resolve(),
        manifest_paths=manifest_paths,
        preset_id=str(payload.get("preset_id", DEFAULT_SCENE_GENERATION_OPTIONS.preset_id) or "").strip(),
        random_seed=_resolve_optional_int(payload.get("random_seed", DEFAULT_SCENE_GENERATION_OPTIONS.random_seed)),
        design_variant_id=str(payload.get("design_variant_id", DEFAULT_SCENE_GENERATION_OPTIONS.design_variant_id) or "").strip(),
        design_variant_name=str(payload.get("design_variant_name", DEFAULT_SCENE_GENERATION_OPTIONS.design_variant_name) or "").strip(),
        object_manifest_v2_path=_resolve_optional_path(
            payload.get("object_manifest_v2_path"),
            DEFAULT_SCENE_GENERATION_OPTIONS.object_manifest_v2_path,
        ),
        ground_material_manifest_path=_resolve_optional_path(
            payload.get("ground_material_manifest_path"),
            DEFAULT_SCENE_GENERATION_OPTIONS.ground_material_manifest_path,
        ),
        sky_manifest_path=_resolve_optional_path(
            payload.get("sky_manifest_path"),
            DEFAULT_SCENE_GENERATION_OPTIONS.sky_manifest_path,
        ),
        model_name=str(payload.get("model_name", DEFAULT_SCENE_GENERATION_OPTIONS.model_name)),
        model_dir=(
            Path(str(payload["model_dir"])).expanduser().resolve()
            if payload.get("model_dir")
            else DEFAULT_SCENE_GENERATION_OPTIONS.model_dir
        ),
        local_files_only=bool(payload.get("local_files_only", DEFAULT_SCENE_GENERATION_OPTIONS.local_files_only)),
        device=str(payload.get("device", DEFAULT_SCENE_GENERATION_OPTIONS.device)),
        export_format=str(payload.get("export_format", DEFAULT_SCENE_GENERATION_OPTIONS.export_format)),
        placement_policy=str(payload.get("placement_policy", DEFAULT_SCENE_GENERATION_OPTIONS.placement_policy)),
        policy_ckpt=(
            Path(str(payload["policy_ckpt"])).expanduser().resolve()
            if payload.get("policy_ckpt")
            else DEFAULT_SCENE_GENERATION_OPTIONS.policy_ckpt
        ),
        program_ckpt=(
            Path(str(payload["program_ckpt"])).expanduser().resolve()
            if payload.get("program_ckpt")
            else DEFAULT_SCENE_GENERATION_OPTIONS.program_ckpt
        ),
        policy_temperature=float(payload.get("policy_temperature", DEFAULT_SCENE_GENERATION_OPTIONS.policy_temperature)),
        build_production_artifacts=_resolve_bool(
            payload.get("build_production_artifacts"),
            DEFAULT_SCENE_GENERATION_OPTIONS.build_production_artifacts,
        ),
        render_presentation_artifacts=_resolve_bool(
            payload.get("render_presentation_artifacts"),
            DEFAULT_SCENE_GENERATION_OPTIONS.render_presentation_artifacts,
        ),
        capture_3d_views=_resolve_bool(
            payload.get("capture_3d_views"),
            DEFAULT_SCENE_GENERATION_OPTIONS.capture_3d_views,
        ),
        capture_profile=str(payload.get("capture_profile", DEFAULT_SCENE_GENERATION_OPTIONS.capture_profile) or "review_expanded"),
        capture_resolution=_resolve_resolution(
            payload.get("capture_resolution", DEFAULT_SCENE_GENERATION_OPTIONS.capture_resolution),
            DEFAULT_SCENE_GENERATION_OPTIONS.capture_resolution,
        ),
        capture_failure_policy=str(payload.get("capture_failure_policy", DEFAULT_SCENE_GENERATION_OPTIONS.capture_failure_policy) or "warn"),
        retain_glb_policy=str(payload.get("retain_glb_policy", DEFAULT_SCENE_GENERATION_OPTIONS.retain_glb_policy) or "top_k"),
        capture_defer_glb_retention=_resolve_bool(
            payload.get("capture_defer_glb_retention"),
            DEFAULT_SCENE_GENERATION_OPTIONS.capture_defer_glb_retention,
        ),
        design_matrix_cell=(
            dict(payload.get("design_matrix_cell"))
            if isinstance(payload.get("design_matrix_cell"), Mapping)
            else {}
        ),
    )


def _build_runtime_compose_config(
    base_config: StreetComposeConfig,
    *,
    resolved_scene_context: ResolvedSceneContext,
) -> StreetComposeConfig:
    payload = dict(base_config.to_dict())
    if resolved_scene_context.scene_context.layout_mode in {"osm", "osm_multiblock"}:
        layout_mode = resolved_scene_context.scene_context.layout_mode
        payload.update({
            "layout_mode": layout_mode,
            "aoi_bbox": resolved_scene_context.effective_aoi_bbox,
            "osm_cache_dir": str(resolved_scene_context.osm_cache_dir),
            "road_selection": (
                "all"
                if layout_mode == "osm_multiblock"
                else str(resolved_scene_context.road_selection)
            ),
            "selected_road_osm_id": resolved_scene_context.selected_road_osm_id,
            "selected_road_discovered_poi_count": resolved_scene_context.selected_road_discovered_poi_count,
            "selected_road_discovered_poi_score": resolved_scene_context.selected_road_discovered_poi_score,
            "selected_road_discovered_core_poi_count": resolved_scene_context.selected_road_discovered_core_poi_count,
        })
    else:
        payload.update({
            "layout_mode": "template",
            "aoi_bbox": None,
        })
    return StreetComposeConfig(**payload)


def _augment_layout_summary(layout_path: str | Path, extra_summary: Mapping[str, Any]) -> None:
    path = Path(layout_path)
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    summary = dict(payload.get("summary", {}) or {})
    if extra_summary:
        summary.update(dict(make_json_safe(extra_summary)))
    payload["summary"] = summary
    # Inject audio profile
    try:
        from ..scene_audio import inject_audio_profile
        inject_audio_profile(payload)
    except Exception:
        pass
    path.write_text(json.dumps(make_json_safe(payload), ensure_ascii=True, indent=2), encoding="utf-8")


def _build_scene_generation_result(
    *,
    config: StreetComposeConfig,
    compose_result: Any,
    extra_summary: Mapping[str, Any] | None = None,
) -> SceneGenerationResult:
    scene_layout_path = str(compose_result.outputs.get("scene_layout", "") or "")
    viewer_url = ""
    summary: Dict[str, Any] = {
        "instance_count": int(getattr(compose_result, "instance_count", 0)),
        "dropped_slots": int(getattr(compose_result, "dropped_slots", 0)),
    }
    if scene_layout_path:
        _augment_layout_summary(scene_layout_path, extra_summary or {})
        cached_layout = cache_scene_layout_for_viewer(scene_layout_path)
        _augment_layout_summary(cached_layout, extra_summary or {})
        viewer_url = build_web_viewer_url(cached_layout)
        try:
            payload = json.loads(Path(cached_layout).read_text(encoding="utf-8"))
            summary = dict(payload.get("summary", {}) or summary)
        except Exception:
            pass
    return SceneGenerationResult(
        compose_config=dict(make_json_safe(config.to_dict())),
        summary=dict(make_json_safe(summary)),
        scene_layout_path=scene_layout_path,
        scene_glb_path=str(compose_result.outputs.get("scene_glb", "") or ""),
        scene_ply_path=str(compose_result.outputs.get("scene_ply", "") or ""),
        viewer_url=viewer_url,
    )


def _generation_options_summary(options: SceneGenerationOptions) -> Dict[str, Any]:
    summary = {
        "preset_id": str(options.preset_id or ""),
        "random_seed": options.random_seed,
        "design_variant_id": str(options.design_variant_id or ""),
        "design_variant_name": str(options.design_variant_name or ""),
    }
    if options.design_matrix_cell:
        summary["design_matrix_cell"] = dict(make_json_safe(options.design_matrix_cell))
    return summary


def _capture_scene_views_if_requested(
    compose_result: Any,
    *,
    options: SceneGenerationOptions,
    progress_callback: ProgressCallback | None = None,
) -> None:
    if not options.capture_3d_views:
        return
    layout_path = str(compose_result.outputs.get("scene_layout", "") or "").strip()
    if not layout_path:
        return
    _emit_progress(
        progress_callback,
        stage="capture_3d_views",
        progress=99,
        message="Capturing backend 3D review views.",
        layout_path=layout_path,
        capture_profile=options.capture_profile,
    )
    capture_result = capture_views_for_layout(
        layout_path=layout_path,
        scene_glb_path=str(compose_result.outputs.get("scene_glb", "") or ""),
        options=options.to_dict(),
        manifest_path=options.manifest_path,
    )
    if capture_result.capture_manifest_path:
        compose_result.outputs["capture_manifest"] = capture_result.capture_manifest_path
    compose_result.outputs["scene_glb"] = capture_result.scene_glb_path
    _emit_progress(
        progress_callback,
        stage="capture_3d_views",
        progress=99,
        message=(
            "Captured backend 3D review views."
            if capture_result.status == "succeeded"
            else "Backend 3D capture failed; generation kept the GLB for debugging."
        ),
        layout_path=layout_path,
        capture_manifest_path=capture_result.capture_manifest_path,
        capture_status=capture_result.status,
        capture_error=capture_result.error,
        capture_view_count=capture_result.view_count,
        glb_deleted=capture_result.glb_deleted,
    )


def _build_scene_backends(options: SceneGenerationOptions):
    object_backend = ManifestObjectAssetBackend(
        manifest_path=options.manifest_path,
        manifest_paths=options.manifest_paths,
        manifest_v2_path=options.object_manifest_v2_path,
    )
    ground_backend = ManifestGroundMaterialBackend(
        manifest_path=options.ground_material_manifest_path,
    )
    sky_backend = ManifestSkyBackend(
        manifest_path=options.sky_manifest_path,
    )
    return object_backend, ground_backend, sky_backend


def _wants_llm_parameter_derivation(
    generation_options: Mapping[str, Any] | SceneGenerationOptions | None,
) -> bool:
    """Check whether the draft should be enriched by the LLM before generation.

    Both custom and preset modes benefit from RAG + LLM adjustment.
    Presets provide base parameters, while LLM fine-tunes based on prompt.
    """
    if not isinstance(generation_options, Mapping):
        return False
    preset_id = str(generation_options.get("preset_id", "") or "").strip().lower()
    # Always enable LLM derivation for both custom and presets
    return preset_id not in {"none", "disabled", "skip_llm"}


def _load_preset_rag_config(preset_id: str) -> Dict[str, Any]:
    """Load RAG configuration for a specific preset."""
    import json

    preset_rag_path = ROOT / "assets" / "presets" / "preset_rag_config.json"
    if not preset_rag_path.exists():
        return {}

    try:
        with open(preset_rag_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config.get(preset_id, {})
    except Exception:
        return {}


def _graph_summary_for_llm_derivation(
    base_config: StreetComposeConfig,
    *,
    scene_context: SceneContext,
    options: SceneGenerationOptions,
) -> Dict[str, Any]:
    if scene_context.layout_mode == "graph_template":
        template_id = str(scene_context.graph_template_id or DEFAULT_GRAPH_TEMPLATE_ID).strip().lower()
        bridge_kwargs: Dict[str, Any] = {"template_id": template_id}
        if scene_context.template_patch:
            bridge_kwargs["template_patch"] = scene_context.template_patch
        bridge = build_graph_template_scene_bridge(base_config, **bridge_kwargs)
        return dict(bridge.summary_metadata)
    if scene_context.layout_mode == "metaurban":
        plan_id = str(scene_context.reference_plan_id or DEFAULT_METAURBAN_REFERENCE_PLAN_ID).strip().lower()
        bridge = build_metaurban_scene_bridge(base_config, plan_id=plan_id)
        return dict(bridge.summary_metadata)
    if scene_context.layout_mode == "reference_annotation":
        annotation_payload = _load_reference_annotation_payload(scene_context.reference_annotation_path)
        bridge = build_reference_annotation_scene_bridge(annotation_payload, compose_config=base_config)
        return dict(bridge.summary_metadata)
    resolved = resolve_scene_context(
        scene_context,
        config=base_config,
        artifacts_dir=options.artifacts_dir,
    )
    return dict(resolved.to_summary_metadata())


def _style_marker_index(text: str, markers: Sequence[str]) -> int:
    positions = [text.find(marker) for marker in markers if marker in text]
    return min((pos for pos in positions if pos >= 0), default=-1)


def _style_transfer_marker_index(text: str) -> int:
    positions = [_style_marker_index(text, _STYLE_TRANSFER_MARKERS)]
    from_to_match = text.find(" from ")
    if from_to_match >= 0 and " to " in text[from_to_match + 6:]:
        positions.append(text.find(" to ", from_to_match + 6))
    return min((pos for pos in positions if pos >= 0), default=-1)


def _detect_style_intent(query: object) -> tuple[str, str]:
    text = str(query or "").strip().lower().replace("-", "_")
    if not text:
        return "", ""

    blend_index = _style_marker_index(text, _STYLE_BLEND_MARKERS)
    transfer_index = _style_transfer_marker_index(text)
    if blend_index >= 0 and (transfer_index < 0 or blend_index <= transfer_index):
        mode = "blend"
        marker_index = blend_index
    elif transfer_index >= 0:
        mode = "transfer"
        marker_index = transfer_index
    else:
        return "", ""

    matches: list[tuple[int, int, str]] = []
    for profile, keywords in _STYLE_TRANSFER_TARGET_KEYWORDS.items():
        for keyword in keywords:
            keyword_text = keyword.lower().replace("-", "_")
            position = text.find(keyword_text)
            if position >= 0:
                after_marker = 0 if position >= marker_index else 1
                matches.append((after_marker, abs(position - marker_index), profile))
    if not matches:
        return mode, ""
    matches.sort()
    return mode, normalize_street_furniture_profile(matches[0][2])


def _merge_presence_categories(*values: object) -> tuple[str, ...]:
    merged: list[str] = []
    for value in values:
        if isinstance(value, str):
            items = [item.strip().lower() for item in value.replace(";", ",").split(",")]
        elif isinstance(value, Sequence):
            items = [str(item).strip().lower() for item in value]
        else:
            items = []
        for item in items:
            if item and item not in merged:
                merged.append(item)
    return tuple(merged)


def _mid_density(*values: object, fallback: float = 0.65) -> float:
    numeric_values: list[float] = []
    for value in values:
        try:
            numeric_values.append(float(value))
        except (TypeError, ValueError):
            continue
    if not numeric_values:
        return fallback
    return round(max(0.1, min(1.5, sum(numeric_values) / len(numeric_values))), 3)


def _build_style_blend_patch(
    *,
    base_profile: str,
    target_profile: str,
    explicit_patch: Mapping[str, Any],
) -> Dict[str, Any]:
    if not target_profile:
        return {}
    if not base_profile:
        return _style_transfer_patch_for_profile(target_profile)

    base_defaults = street_furniture_profile_config_patch(base_profile)
    target_defaults = street_furniture_profile_config_patch(target_profile)
    if base_profile == "pedestrian_friendly" and target_profile == "transit_priority":
        patch = {
            "density": 0.7,
            "ped_demand_level": "high",
            "bike_demand_level": "medium",
            "transit_demand_level": "high",
            "vehicle_demand_level": "medium",
            "minimum_category_presence": _merge_presence_categories(
                explicit_patch.get("minimum_category_presence"),
                base_defaults.get("minimum_category_presence"),
                ("bus_stop",),
            ),
            "optional_category_presence": _merge_presence_categories(
                explicit_patch.get("optional_category_presence"),
                base_defaults.get("optional_category_presence"),
                target_defaults.get("optional_category_presence"),
            ),
            "max_bus_stops_per_scene": 2,
            "allow_demo_bus_stop_when_osm_absent": True,
            "street_furniture_profile_reasons": (_STYLE_BLEND_REASON,),
        }
        minimum = set(patch["minimum_category_presence"])
        patch["optional_category_presence"] = tuple(
            item for item in patch["optional_category_presence"] if item not in minimum
        )
        return sanitize_compose_config_patch(patch)

    patch = {
        "density": _mid_density(
            explicit_patch.get("density"),
            base_defaults.get("density"),
            target_defaults.get("density"),
        ),
        "ped_demand_level": explicit_patch.get("ped_demand_level", base_defaults.get("ped_demand_level", "medium")),
        "bike_demand_level": explicit_patch.get("bike_demand_level", base_defaults.get("bike_demand_level", "medium")),
        "transit_demand_level": target_defaults.get("transit_demand_level", "medium"),
        "vehicle_demand_level": explicit_patch.get("vehicle_demand_level", base_defaults.get("vehicle_demand_level", "medium")),
        "minimum_category_presence": _merge_presence_categories(
            explicit_patch.get("minimum_category_presence"),
            base_defaults.get("minimum_category_presence"),
            target_defaults.get("minimum_category_presence"),
        ),
        "optional_category_presence": _merge_presence_categories(
            explicit_patch.get("optional_category_presence"),
            base_defaults.get("optional_category_presence"),
            target_defaults.get("optional_category_presence"),
        ),
        "street_furniture_profile_reasons": (_STYLE_BLEND_REASON,),
    }
    if target_profile == "transit_priority":
        patch["minimum_category_presence"] = _merge_presence_categories(
            patch["minimum_category_presence"],
            ("bus_stop",),
        )
        patch["max_bus_stops_per_scene"] = 2
        patch["allow_demo_bus_stop_when_osm_absent"] = True
    minimum = set(patch["minimum_category_presence"])
    patch["optional_category_presence"] = tuple(
        item for item in patch["optional_category_presence"] if item not in minimum
    )
    return sanitize_compose_config_patch(patch)


def _style_transfer_patch_for_profile(target_profile: str) -> Dict[str, Any]:
    if not target_profile:
        return {}
    patch = street_furniture_profile_config_patch(target_profile)
    patch.update({
        "street_furniture_profile_source": "manual",
        "street_furniture_profile_confidence": 1.0,
        "street_furniture_profile_reasons": (_STYLE_TRANSFER_REASON,),
    })
    return sanitize_compose_config_patch(patch)


def _style_intent_for_draft(draft: DesignDraft) -> Dict[str, Any]:
    explicit_patch = sanitize_compose_config_patch(draft.compose_config_patch)
    mode, target_profile = _detect_style_intent(draft.normalized_scene_query)
    base_profile = normalize_street_furniture_profile(explicit_patch.get("street_furniture_profile"))
    if not target_profile:
        return {
            "mode": "",
            "base_profile": base_profile,
            "target_profile": "",
            "patch": {},
            "source": "",
        }
    patch = (
        _build_style_blend_patch(
            base_profile=base_profile,
            target_profile=target_profile,
            explicit_patch=explicit_patch,
        )
        if mode == "blend"
        else _style_transfer_patch_for_profile(target_profile)
    )
    return {
        "mode": mode,
        "base_profile": base_profile,
        "target_profile": target_profile,
        "patch": patch,
        "source": _STYLE_BLEND_TARGET_SOURCE if mode == "blend" else _STYLE_TRANSFER_TARGET_SOURCE,
    }


def _style_intent_overridden_explicit_fields(
    explicit_patch: Mapping[str, Any],
    target_patch: Mapping[str, Any],
) -> list[str]:
    return sorted(
        field_name
        for field_name, target_value in target_patch.items()
        if field_name in explicit_patch and explicit_patch[field_name] != target_value
    )


def _style_intent_preserved_explicit_fields(
    explicit_patch: Mapping[str, Any],
    target_patch: Mapping[str, Any],
) -> list[str]:
    overridden = set(_style_intent_overridden_explicit_fields(explicit_patch, target_patch))
    return sorted(field_name for field_name in explicit_patch if field_name not in overridden)


def _merge_llm_patch_with_explicit_inputs(
    draft: DesignDraft,
    llm_patch: Mapping[str, Any],
) -> tuple[Dict[str, Any], Dict[str, str], list[str], list[str]]:
    """Merge an LLM patch with explicit user inputs.

    Explicit inputs always win over LLM suggestions.  The returned parameter
    sources make that precedence visible to the UI and to diagnostics.
    """

    explicit_patch = sanitize_compose_config_patch(draft.compose_config_patch)
    normalized_llm_patch = sanitize_compose_config_patch(llm_patch)
    style_intent = _style_intent_for_draft(draft)
    style_intent_patch = dict(style_intent.get("patch") or {})
    style_intent_source = str(style_intent.get("source") or "")
    merged_patch = dict(normalized_llm_patch)
    controlled_patch = {**explicit_patch, **style_intent_patch}
    explicit_fields = set(explicit_patch)
    style_intent_fields = set(style_intent_patch)
    llm_fields = set(normalized_llm_patch)
    overridden_llm_fields: list[str] = []

    for field_name, explicit_value in controlled_patch.items():
        if field_name in normalized_llm_patch and normalized_llm_patch[field_name] != explicit_value:
            overridden_llm_fields.append(field_name)
        merged_patch[field_name] = explicit_value

    if draft.normalized_scene_query and "query" not in merged_patch:
        merged_patch["query"] = str(draft.normalized_scene_query).strip()

    defaulted_fields: list[str] = []
    for field_name, default_value in DEFAULT_COMPOSE_CONFIG_PATCH_VALUES.items():
        if field_name in merged_patch:
            continue
        merged_patch[field_name] = default_value
        defaulted_fields.append(field_name)

    parameter_sources: Dict[str, str] = {}
    for field_name in merged_patch:
        if field_name in style_intent_fields:
            parameter_sources[field_name] = style_intent_source or _STYLE_TRANSFER_TARGET_SOURCE
        elif field_name in explicit_fields:
            parameter_sources[field_name] = "explicit_input"
        elif field_name == "query" and draft.normalized_scene_query:
            parameter_sources[field_name] = "prompt_input"
        elif field_name in llm_fields:
            parameter_sources[field_name] = "llm_derived"
        else:
            parameter_sources[field_name] = "default_after_llm"

    return merged_patch, parameter_sources, defaulted_fields, sorted(overridden_llm_fields)


def _derive_draft_with_llm(
    draft: DesignDraft,
    *,
    base_config: StreetComposeConfig,
    scene_context: SceneContext,
    options: SceneGenerationOptions,
    progress_callback: ProgressCallback | None = None,
) -> DesignDraft:
    _emit_progress(
        progress_callback,
        stage="context_resolving",
        progress=12,
        message="LLM-driven parameter derivation: resolving graph context.",
        llm_derivation_start=True,
        normalized_scene_query=draft.normalized_scene_query,
        layout_mode=scene_context.layout_mode,
        graph_template_id=scene_context.graph_template_id,
        reference_plan_id=scene_context.reference_plan_id,
    )
    try:
        from ..llm.design_workflow import DesignAssistantService
        from ..llm.prompts import build_graph_aware_design_messages

        # Determine preset_id for RAG configuration
        preset_id = str(options.preset_id or "custom").strip().lower()

        graph_summary = _graph_summary_for_llm_derivation(
            base_config,
            scene_context=scene_context,
            options=options,
        )
        assistant = DesignAssistantService()

        # Load preset RAG configuration
        preset_rag_config = _load_preset_rag_config(preset_id)
        knowledge_source = preset_rag_config.get("knowledge_source", "graph_rag")
        rag_queries = preset_rag_config.get("rag_queries", [draft.normalized_scene_query or "walkable complete street"])

        # For presets, also use preset-specific RAG queries
        if preset_id not in {"custom", "__custom__", "llm", "llm-driven"}:
            rag_queries = [draft.normalized_scene_query or "walkable complete street"] + rag_queries

        # RAG evidence retrieval
        evidence = []
        try:
            evidence = assistant._retrieve_evidence(
                queries=rag_queries,
                topk=5,
                knowledge_source=knowledge_source,
            )
            retrieve_scenario_parameters = getattr(assistant, "_retrieve_scenario_parameter_evidence", None)
            if callable(retrieve_scenario_parameters):
                structured_evidence = retrieve_scenario_parameters(
                    queries=rag_queries,
                    topk=24,
                )
                if structured_evidence:
                    merged_evidence = {item.chunk_id: item for item in [*evidence, *structured_evidence]}
                    evidence = list(merged_evidence.values())
            citations_by_field = {
                f"{preset_id}_design": tuple(e.chunk_id for e in evidence[:2]),
                "general": tuple(e.chunk_id for e in evidence[2:4]),
            }
        except Exception as rag_exc:
            import logging
            logging.getLogger(__name__).warning("RAG retrieval failed: %s", rag_exc)
            citations_by_field = {}
            evidence = []

        messages = build_graph_aware_design_messages(
            graph_summary=graph_summary,
            user_prompt=draft.normalized_scene_query or "walkable complete street",
            current_patch=draft.compose_config_patch,  # Preset base params passed here
            rag_evidence=evidence,
            rag_queries=rag_queries,
            knowledge_source=knowledge_source,
        )
        llm_response = assistant._get_llm_client().chat_json(messages)
        raw_patch = sanitize_compose_config_patch(llm_response.get("compose_config_patch", {}))
        explicit_patch = sanitize_compose_config_patch(draft.compose_config_patch)
        style_intent = _style_intent_for_draft(draft)
        style_intent_patch = dict(style_intent.get("patch") or {})
        style_intent_mode = str(style_intent.get("mode") or "")
        style_intent_base_profile = str(style_intent.get("base_profile") or "")
        style_intent_target_profile = str(style_intent.get("target_profile") or "")
        style_intent_overridden_explicit_fields = _style_intent_overridden_explicit_fields(
            explicit_patch,
            style_intent_patch,
        )
        style_intent_preserved_explicit_fields = _style_intent_preserved_explicit_fields(
            explicit_patch,
            style_intent_patch,
        )
        llm_patch, parameter_sources, defaulted_fields, overridden_llm_fields = _merge_llm_patch_with_explicit_inputs(
            draft,
            raw_patch,
        )
        design_summary = str(llm_response.get("design_summary", "") or "").strip()
        _emit_progress(
            progress_callback,
            stage="context_resolving",
            progress=18,
            message="LLM derived config parameters.",
            llm_derivation_status="succeeded",
            normalized_scene_query=draft.normalized_scene_query,
            design_summary=design_summary or draft.design_summary,
            graph_summary=graph_summary,
            config_patch=llm_patch,
            llm_raw_fields=sorted(raw_patch),
            defaulted_fields=sorted(defaulted_fields),
            overridden_llm_fields=sorted(overridden_llm_fields),
            style_blend_mode=style_intent_mode,
            style_blend_base_profile=style_intent_base_profile,
            style_blend_target_profile=style_intent_target_profile,
            style_blend_patch=style_intent_patch if style_intent_mode == "blend" else {},
            style_blend_preserved_explicit_fields=style_intent_preserved_explicit_fields,
            style_blend_promoted_fields=sorted(style_intent_patch),
            style_blend_overridden_explicit_fields=style_intent_overridden_explicit_fields,
            style_transfer_target_profile=style_intent_target_profile if style_intent_mode == "transfer" else "",
            style_transfer_target_patch=style_intent_patch if style_intent_mode == "transfer" else {},
            style_transfer_overridden_explicit_fields=style_intent_overridden_explicit_fields if style_intent_mode == "transfer" else [],
            parameter_sources_by_field=parameter_sources,
            # RAG evidence fields for frontend display
            citations_by_field=citations_by_field,
            rag_queries=list(rag_queries),
            rag_evidence=[item.to_dict() for item in evidence],
            knowledge_source=knowledge_source,
            evidence_count=len(evidence),
            preset_id=preset_id,
            **llm_patch,
        )
        return DesignDraft(
            normalized_scene_query=draft.normalized_scene_query,
            compose_config_patch=llm_patch,
            citations_by_field=citations_by_field,
            design_summary=design_summary or draft.design_summary,
            risk_notes=draft.risk_notes,
            parameter_sources_by_field=parameter_sources,
            template_patch=draft.template_patch,
        )
    except Exception as exc:
        import traceback

        trace = traceback.format_exc()
        logger.error("LLM parameter derivation failed: %s\n%s", exc, trace)
        _emit_progress(
            progress_callback,
            stage="context_resolving",
            progress=15,
            message=f"LLM parameter derivation failed ({type(exc).__name__}).",
            llm_derivation_status="failed",
            llm_error_type=type(exc).__name__,
            llm_error=str(exc),
            llm_traceback=trace[:1000],
        )
        raise RuntimeError(f"LLM parameter derivation failed: {exc}") from exc


def _build_metaurban_out_dir(base_out_dir: Path, plan_id: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (Path(base_out_dir).expanduser().resolve() / "metaurban" / str(plan_id) / timestamp).resolve()


def _build_graph_template_out_dir(base_out_dir: Path, template_id: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (Path(base_out_dir).expanduser().resolve() / "graph_template" / str(template_id) / timestamp).resolve()


def _build_reference_annotation_out_dir(base_out_dir: Path, annotation_id: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (Path(base_out_dir).expanduser().resolve() / "reference_annotation" / str(annotation_id) / timestamp).resolve()


def _load_reference_annotation_payload(path_value: str | Path | None) -> Dict[str, Any]:
    raw_path = str(path_value or "").strip()
    if not raw_path:
        raise RuntimeError("reference_annotation layout mode requires reference_annotation_path.")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    if not path.exists():
        raise RuntimeError(f"Reference annotation JSON not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid reference annotation JSON: {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError(f"Reference annotation JSON must be an object: {path}")
    return dict(payload)


def _generate_metaurban_scene_from_draft(
    base_config: StreetComposeConfig,
    *,
    options: SceneGenerationOptions,
    scene_context: SceneContext,
    progress_callback: ProgressCallback | None = None,
) -> SceneGenerationResult:
    plan_id = str(scene_context.reference_plan_id or DEFAULT_METAURBAN_REFERENCE_PLAN_ID).strip().lower()
    _emit_progress(
        progress_callback,
        stage="context_resolving",
        progress=15,
        message="Building MetaUrban layout bridge.",
        reference_plan_id=plan_id,
    )
    try:
        bridge = build_metaurban_scene_bridge(
            base_config,
            plan_id=plan_id,
        )
    except KeyError as exc:
        raise RuntimeError(str(exc)) from exc
    config = replace(base_config, layout_mode="metaurban")
    metaurban_out_dir = _build_metaurban_out_dir(options.out_dir, plan_id)
    _emit_progress(
        progress_callback,
        stage="asset_loading",
        progress=20,
        message="Preparing scene asset backends.",
        layout_mode="metaurban",
    )
    object_backend, ground_backend, sky_backend = _build_scene_backends(options)
    result = compose_street_scene(
        config=config,
        manifest_path=options.manifest_path,
        artifacts_dir=options.artifacts_dir,
        model_name=options.model_name,
        model_dir=options.model_dir,
        local_files_only=bool(options.local_files_only),
        device=options.device,
        export_format=options.export_format,
        out_dir=metaurban_out_dir,
        placement_policy=options.placement_policy,
        policy_ckpt=options.policy_ckpt,
        program_ckpt=options.program_ckpt,
        policy_temperature=float(options.policy_temperature),
        object_asset_backend=object_backend,
        ground_material_backend=ground_backend,
        sky_backend=sky_backend,
        road_segment_graph_override=bridge.road_segment_graph,
        projected_features_override=bridge.projected_features,
        placement_context_override=bridge.placement_context,
        build_production_artifacts=options.build_production_artifacts,
        render_presentation_artifacts=options.render_presentation_artifacts,
        progress_callback=progress_callback,
    )
    _capture_scene_views_if_requested(result, options=options, progress_callback=progress_callback)
    return _build_scene_generation_result(
        config=config,
        compose_result=result,
        extra_summary={**bridge.summary_metadata, **_generation_options_summary(options)},
    )


def _generate_reference_annotation_scene_from_draft(
    base_config: StreetComposeConfig,
    *,
    options: SceneGenerationOptions,
    scene_context: SceneContext,
    progress_callback: ProgressCallback | None = None,
) -> SceneGenerationResult:
    annotation_payload = _load_reference_annotation_payload(scene_context.reference_annotation_path)
    annotation_id = str(
        scene_context.scenario_id
        or annotation_payload.get("plan_id")
        or Path(str(scene_context.reference_annotation_path or "reference_annotation")).stem
    ).strip() or "reference_annotation"
    _emit_progress(
        progress_callback,
        stage="context_resolving",
        progress=15,
        message="Building reference-annotation layout bridge.",
        reference_annotation_path=str(scene_context.reference_annotation_path or ""),
        reference_annotation_id=annotation_id,
    )
    bridge = build_reference_annotation_scene_bridge(annotation_payload, compose_config=base_config)
    config = replace(base_config, layout_mode="reference_annotation")
    out_dir = _build_reference_annotation_out_dir(options.out_dir, annotation_id)
    _emit_progress(
        progress_callback,
        stage="asset_loading",
        progress=20,
        message="Preparing scene asset backends.",
        layout_mode="reference_annotation",
    )
    object_backend, ground_backend, sky_backend = _build_scene_backends(options)
    result = compose_street_scene(
        config=config,
        manifest_path=options.manifest_path,
        artifacts_dir=options.artifacts_dir,
        model_name=options.model_name,
        model_dir=options.model_dir,
        local_files_only=bool(options.local_files_only),
        device=options.device,
        export_format=options.export_format,
        out_dir=out_dir,
        placement_policy=options.placement_policy,
        policy_ckpt=options.policy_ckpt,
        program_ckpt=options.program_ckpt,
        policy_temperature=float(options.policy_temperature),
        object_asset_backend=object_backend,
        ground_material_backend=ground_backend,
        sky_backend=sky_backend,
        road_segment_graph_override=bridge.road_segment_graph,
        projected_features_override=bridge.projected_features,
        placement_context_override=bridge.placement_context,
        build_production_artifacts=options.build_production_artifacts,
        render_presentation_artifacts=options.render_presentation_artifacts,
        progress_callback=progress_callback,
    )
    _capture_scene_views_if_requested(result, options=options, progress_callback=progress_callback)
    context_summary = {
        "reference_annotation_path": str(scene_context.reference_annotation_path or ""),
        "scenario_id": scene_context.scenario_id,
        "scenario_title": scene_context.scenario_title,
        "scenario_design_variant": (
            dict(scene_context.scenario_design_variant)
            if isinstance(scene_context.scenario_design_variant, Mapping)
            else None
        ),
    }
    return _build_scene_generation_result(
        config=config,
        compose_result=result,
        extra_summary={**bridge.summary_metadata, **context_summary, **_generation_options_summary(options)},
    )


def _generate_graph_template_scene_from_draft(
    base_config: StreetComposeConfig,
    *,
    options: SceneGenerationOptions,
    scene_context: SceneContext,
    progress_callback: ProgressCallback | None = None,
) -> SceneGenerationResult:
    template_id = str(scene_context.graph_template_id or DEFAULT_GRAPH_TEMPLATE_ID).strip().lower()
    _emit_progress(
        progress_callback,
        stage="context_resolving",
        progress=15,
        message="Building graph-template layout bridge.",
        graph_template_id=template_id,
    )
    try:
        bridge_kwargs: Dict[str, Any] = {"template_id": template_id}
        if scene_context.template_patch:
            bridge_kwargs["template_patch"] = scene_context.template_patch
        bridge = build_graph_template_scene_bridge(base_config, **bridge_kwargs)
    except KeyError as exc:
        raise RuntimeError(str(exc)) from exc
    config = replace(base_config, layout_mode="graph_template")
    graph_template_out_dir = _build_graph_template_out_dir(options.out_dir, template_id)
    _emit_progress(
        progress_callback,
        stage="asset_loading",
        progress=20,
        message="Preparing scene asset backends.",
        layout_mode="graph_template",
    )
    object_backend, ground_backend, sky_backend = _build_scene_backends(options)
    result = compose_street_scene(
        config=config,
        manifest_path=options.manifest_path,
        artifacts_dir=options.artifacts_dir,
        model_name=options.model_name,
        model_dir=options.model_dir,
        local_files_only=bool(options.local_files_only),
        device=options.device,
        export_format=options.export_format,
        out_dir=graph_template_out_dir,
        placement_policy=options.placement_policy,
        policy_ckpt=options.policy_ckpt,
        program_ckpt=options.program_ckpt,
        policy_temperature=float(options.policy_temperature),
        object_asset_backend=object_backend,
        ground_material_backend=ground_backend,
        sky_backend=sky_backend,
        road_segment_graph_override=bridge.road_segment_graph,
        projected_features_override=bridge.projected_features,
        placement_context_override=bridge.placement_context,
        build_production_artifacts=options.build_production_artifacts,
        render_presentation_artifacts=options.render_presentation_artifacts,
        progress_callback=progress_callback,
    )
    _capture_scene_views_if_requested(result, options=options, progress_callback=progress_callback)
    scenario_variant = scene_context.scenario_design_variant
    context_summary = {
        "graph_template_id": template_id,
        "base_graph_template_id": template_id if scene_context.scenario_id else None,
        "scenario_id": scene_context.scenario_id,
        "scenario_title": scene_context.scenario_title,
        "scenario_design_variant": dict(scenario_variant) if isinstance(scenario_variant, Mapping) else None,
    }
    return _build_scene_generation_result(
        config=config,
        compose_result=result,
        extra_summary={**bridge.summary_metadata, **context_summary, **_generation_options_summary(options)},
    )


def generate_scene_from_draft(
    draft: DesignDraft,
    *,
    patch_overrides: Mapping[str, Any] | None = None,
    generation_options: Mapping[str, Any] | SceneGenerationOptions | None = None,
    scene_context: Mapping[str, Any] | SceneContext | None = None,
    progress_callback: ProgressCallback | None = None,
) -> SceneGenerationResult:
    """Run the existing scene pipeline using a confirmed design draft."""

    # Apply random seed if provided in generation_options (Seed Control)
    import random
    seed = None
    if isinstance(generation_options, Mapping):
        seed = generation_options.get("random_seed")
    elif isinstance(generation_options, SceneGenerationOptions):
        seed = generation_options.random_seed
    if seed is not None:
        try:
            seed = int(seed)
            random.seed(seed)
            # Ensure numpy is also seeded if available (common in layout solvers)
            try:
                import numpy as np
                np.random.seed(seed)
            except ImportError:
                pass
        except (ValueError, TypeError):
            pass

    options = (
        generation_options
        if isinstance(generation_options, SceneGenerationOptions)
        else normalize_scene_generation_options(generation_options)
    )
    _emit_progress(
        progress_callback,
        stage="context_resolving",
        progress=10,
        message="Normalizing generation request.",
    )

    draft_to_use = draft
    normalized_scene_context = sanitize_scene_context(scene_context)
    base_config = build_compose_config_from_draft(draft_to_use, patch_overrides=patch_overrides)
    if _wants_llm_parameter_derivation(generation_options):
        draft_to_use = _derive_draft_with_llm(
            draft,
            base_config=base_config,
            scene_context=normalized_scene_context,
            options=options,
            progress_callback=progress_callback,
        )
        base_config = build_compose_config_from_draft(draft_to_use, patch_overrides=patch_overrides)
    if (
        normalized_scene_context.layout_mode == "graph_template"
        and not normalized_scene_context.template_patch
        and isinstance(draft_to_use.template_patch, Mapping)
    ):
        normalized_scene_context = replace(
            normalized_scene_context,
            template_patch=dict(draft_to_use.template_patch),
        )
    if normalized_scene_context.layout_mode == "graph_template":
        return _generate_graph_template_scene_from_draft(
            base_config,
            options=options,
            scene_context=normalized_scene_context,
            progress_callback=progress_callback,
        )
    if normalized_scene_context.layout_mode == "metaurban":
        return _generate_metaurban_scene_from_draft(
            base_config,
            options=options,
            scene_context=normalized_scene_context,
            progress_callback=progress_callback,
        )
    if normalized_scene_context.layout_mode == "reference_annotation":
        return _generate_reference_annotation_scene_from_draft(
            base_config,
            options=options,
            scene_context=normalized_scene_context,
            progress_callback=progress_callback,
        )
    resolved_scene_context = resolve_scene_context(
        normalized_scene_context,
        config=base_config,
        artifacts_dir=options.artifacts_dir,
    )
    _emit_progress(
        progress_callback,
        stage="context_resolving",
        progress=20,
        message="Resolved scene context.",
        layout_mode=normalized_scene_context.layout_mode,
    )
    config = _build_runtime_compose_config(
        base_config,
        resolved_scene_context=resolved_scene_context,
    )
    _emit_progress(
        progress_callback,
        stage="asset_loading",
        progress=22,
        message="Preparing scene asset backends.",
        layout_mode=config.layout_mode,
    )
    object_backend, ground_backend, sky_backend = _build_scene_backends(options)
    result = compose_street_scene(
        config=config,
        manifest_path=options.manifest_path,
        artifacts_dir=options.artifacts_dir,
        model_name=options.model_name,
        model_dir=options.model_dir,
        local_files_only=bool(options.local_files_only),
        device=options.device,
        export_format=options.export_format,
        out_dir=options.out_dir,
        placement_policy=options.placement_policy,
        policy_ckpt=options.policy_ckpt,
        program_ckpt=options.program_ckpt,
        policy_temperature=float(options.policy_temperature),
        object_asset_backend=object_backend,
        ground_material_backend=ground_backend,
        sky_backend=sky_backend,
        build_production_artifacts=options.build_production_artifacts,
        render_presentation_artifacts=options.render_presentation_artifacts,
        progress_callback=progress_callback,
    )
    _capture_scene_views_if_requested(result, options=options, progress_callback=progress_callback)
    return _build_scene_generation_result(
        config=config,
        compose_result=result,
        extra_summary={**resolved_scene_context.to_summary_metadata(), **_generation_options_summary(options)},
    )


def generate_scene_from_graph_context(
    *,
    compose_config_patch: Mapping[str, Any],
    road_segment_graph_override: Any,
    projected_features_override: Any,
    placement_context_override: Any,
    generation_options: Mapping[str, Any] | SceneGenerationOptions | None = None,
    extra_summary: Mapping[str, Any] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> SceneGenerationResult:
    """Run the scene pipeline using pre-built graph overrides directly.

    This is the main entry-point for the auto-pipeline iteration loop.  It
    bypasses ``DesignDraft`` and ``SceneContext`` resolution because the graph
    overrides are already materialised.
    """
    options = (
        generation_options
        if isinstance(generation_options, SceneGenerationOptions)
        else normalize_scene_generation_options(generation_options)
    )
    # Build a minimal DesignDraft → config
    draft = DesignDraft(
        normalized_scene_query=str(compose_config_patch.get("query", "auto pipeline")),
        compose_config_patch=sanitize_compose_config_patch(compose_config_patch),
        citations_by_field={},
        design_summary="Auto-pipeline graph-based scene generation",
    )
    base_config = build_compose_config_from_draft(draft)
    config = replace(base_config, layout_mode="graph_template")

    _emit_progress(
        progress_callback,
        stage="asset_loading",
        progress=20,
        message="Preparing graph-context scene asset backends.",
    )
    object_backend, ground_backend, sky_backend = _build_scene_backends(options)

    iter_out_dir = options.out_dir
    iter_out_dir.mkdir(parents=True, exist_ok=True)

    result = compose_street_scene(
        config=config,
        manifest_path=options.manifest_path,
        artifacts_dir=options.artifacts_dir,
        model_name=options.model_name,
        model_dir=options.model_dir,
        local_files_only=bool(options.local_files_only),
        device=options.device,
        export_format=options.export_format,
        out_dir=iter_out_dir,
        placement_policy=options.placement_policy,
        policy_ckpt=options.policy_ckpt,
        program_ckpt=options.program_ckpt,
        policy_temperature=float(options.policy_temperature),
        object_asset_backend=object_backend,
        ground_material_backend=ground_backend,
        sky_backend=sky_backend,
        road_segment_graph_override=road_segment_graph_override,
        projected_features_override=projected_features_override,
        placement_context_override=placement_context_override,
        build_production_artifacts=options.build_production_artifacts,
        render_presentation_artifacts=options.render_presentation_artifacts,
        progress_callback=progress_callback,
    )
    _capture_scene_views_if_requested(result, options=options, progress_callback=progress_callback)
    return _build_scene_generation_result(
        config=config,
        compose_result=result,
        extra_summary={**(extra_summary or {}), **_generation_options_summary(options)},
    )
