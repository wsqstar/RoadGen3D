"""Helpers for turning a confirmed design draft into a generated street scene."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping

from ..json_safe import make_json_safe
from ..graph_template_scene_bridge import build_graph_template_scene_bridge
from ..metaurban_scene_bridge import build_metaurban_scene_bridge
from ..street_layout import compose_street_scene
from ..types import StreetComposeConfig
from ..web_viewer_dev import build_web_viewer_url, cache_scene_layout_for_viewer
from .design_types import (
    ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS,
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
    DEFAULT_OBJECT_MANIFEST_V2_PATH,
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
DEFAULT_SCENE_GENERATION_OPTIONS = SceneGenerationOptions(
    manifest_path=(ROOT / "data" / "real" / "real_assets_manifest.jsonl").resolve(),
    artifacts_dir=(ROOT / "artifacts" / "real").resolve(),
    out_dir=(ROOT / "artifacts" / "real").resolve(),
    object_manifest_v2_path=DEFAULT_OBJECT_MANIFEST_V2_PATH,
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
        seed=int(patch.get("seed", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES.get("seed", 42))),
        topk_per_category=20,
        max_trials_per_slot=30,
        design_rule_profile=str(patch.get("design_rule_profile", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["design_rule_profile"])),
        target_street_type=str(patch.get("target_street_type", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["target_street_type"])),
        objective_profile=str(patch.get("objective_profile", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["objective_profile"])),
        city_context=str(patch.get("city_context", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["city_context"])),
        style_preset=str(patch.get("style_preset", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["style_preset"])),
        beauty_mode=str(patch.get("beauty_mode", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["beauty_mode"])),
        ped_demand_level=str(patch.get("ped_demand_level", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["ped_demand_level"])),
        bike_demand_level=str(patch.get("bike_demand_level", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["bike_demand_level"])),
        transit_demand_level=str(patch.get("transit_demand_level", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["transit_demand_level"])),
        vehicle_demand_level=str(patch.get("vehicle_demand_level", DEFAULT_COMPOSE_CONFIG_PATCH_VALUES["vehicle_demand_level"])),
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

    return SceneGenerationOptions(
        manifest_path=Path(str(payload.get("manifest_path", DEFAULT_SCENE_GENERATION_OPTIONS.manifest_path))).expanduser().resolve(),
        artifacts_dir=Path(str(payload.get("artifacts_dir", DEFAULT_SCENE_GENERATION_OPTIONS.artifacts_dir))).expanduser().resolve(),
        out_dir=Path(str(payload.get("out_dir", DEFAULT_SCENE_GENERATION_OPTIONS.out_dir))).expanduser().resolve(),
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
    )


def _build_runtime_compose_config(
    base_config: StreetComposeConfig,
    *,
    resolved_scene_context: ResolvedSceneContext,
) -> StreetComposeConfig:
    payload = dict(base_config.to_dict())
    if resolved_scene_context.scene_context.layout_mode == "osm":
        payload.update({
            "layout_mode": "osm",
            "aoi_bbox": resolved_scene_context.effective_aoi_bbox,
            "osm_cache_dir": str(resolved_scene_context.osm_cache_dir),
            "road_selection": str(resolved_scene_context.road_selection),
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


def _build_scene_backends(options: SceneGenerationOptions):
    object_backend = ManifestObjectAssetBackend(
        manifest_path=options.manifest_path,
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
    if not isinstance(generation_options, Mapping):
        return False
    preset_id = str(generation_options.get("preset_id", "") or "").strip().lower()
    return preset_id in {"custom", "__custom__", "llm", "llm_driven", "llm-driven"}


def _graph_summary_for_llm_derivation(
    base_config: StreetComposeConfig,
    *,
    scene_context: SceneContext,
    options: SceneGenerationOptions,
) -> Dict[str, Any]:
    if scene_context.layout_mode == "graph_template":
        template_id = str(scene_context.graph_template_id or DEFAULT_GRAPH_TEMPLATE_ID).strip().lower()
        bridge = build_graph_template_scene_bridge(base_config, template_id=template_id)
        return dict(bridge.summary_metadata)
    if scene_context.layout_mode == "metaurban":
        plan_id = str(scene_context.reference_plan_id or DEFAULT_METAURBAN_REFERENCE_PLAN_ID).strip().lower()
        bridge = build_metaurban_scene_bridge(base_config, plan_id=plan_id)
        return dict(bridge.summary_metadata)
    resolved = resolve_scene_context(
        scene_context,
        config=base_config,
        artifacts_dir=options.artifacts_dir,
    )
    return dict(resolved.to_summary_metadata())


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

        graph_summary = _graph_summary_for_llm_derivation(
            base_config,
            scene_context=scene_context,
            options=options,
        )
        assistant = DesignAssistantService()
        
        # RAG evidence retrieval
        knowledge_source = "graph_rag"  # Default to graph RAG
        rag_queries = [draft.normalized_scene_query or "walkable complete street"]
        if base_config.target_street_type:
            rag_queries.append(f"street design {base_config.target_street_type}")
        
        evidence = []
        try:
            evidence = assistant._retrieve_evidence(
                queries=rag_queries,
                topk=5,
                knowledge_source=knowledge_source,
            )
            citations_by_field = {
                "street_design": tuple(e.chunk_id for e in evidence[:2]),
                "pedestrian": tuple(e.chunk_id for e in evidence[2:4]),
            }
        except Exception as rag_exc:
            import logging
            logging.getLogger(__name__).warning("RAG retrieval failed: %s", rag_exc)
            citations_by_field = {}
            evidence = []
        
        messages = build_graph_aware_design_messages(
            graph_summary=graph_summary,
            user_prompt=draft.normalized_scene_query or "walkable complete street",
            current_patch=draft.compose_config_patch,
        )
        llm_response = assistant._get_llm_client().chat_json(messages)
        raw_patch = sanitize_compose_config_patch(llm_response.get("compose_config_patch", {}))
        llm_fields = {key for key in raw_patch if key in ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS}
        llm_patch = dict(raw_patch)
        defaulted_fields: list[str] = []
        for field_name, default_value in DEFAULT_COMPOSE_CONFIG_PATCH_VALUES.items():
            if field_name not in llm_patch:
                llm_patch[field_name] = default_value
                defaulted_fields.append(field_name)
        if draft.normalized_scene_query and "query" not in llm_patch:
            llm_patch["query"] = draft.normalized_scene_query
            defaulted_fields.append("query")
        parameter_sources = {
            key: ("llm_derived" if key in llm_fields else "default_after_llm")
            for key in llm_patch
        }
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
            llm_raw_fields=sorted(llm_fields),
            defaulted_fields=sorted(defaulted_fields),
            parameter_sources_by_field=parameter_sources,
            # RAG evidence fields for frontend display
            citations_by_field=citations_by_field,
            knowledge_source=knowledge_source,
            evidence_count=len(evidence),
            **llm_patch,
        )
        return DesignDraft(
            normalized_scene_query=draft.normalized_scene_query,
            compose_config_patch=llm_patch,
            citations_by_field=citations_by_field,
            design_summary=design_summary or draft.design_summary,
            risk_notes=draft.risk_notes,
            parameter_sources_by_field=parameter_sources,
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
        progress_callback=progress_callback,
    )
    return _build_scene_generation_result(
        config=config,
        compose_result=result,
        extra_summary=bridge.summary_metadata,
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
        bridge = build_graph_template_scene_bridge(
            base_config,
            template_id=template_id,
        )
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
        progress_callback=progress_callback,
    )
    return _build_scene_generation_result(
        config=config,
        compose_result=result,
        extra_summary=bridge.summary_metadata,
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
        progress_callback=progress_callback,
    )
    return _build_scene_generation_result(
        config=config,
        compose_result=result,
        extra_summary=resolved_scene_context.to_summary_metadata(),
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
        progress_callback=progress_callback,
    )
    return _build_scene_generation_result(
        config=config,
        compose_result=result,
        extra_summary=extra_summary,
    )
