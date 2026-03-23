"""Helpers for turning a confirmed design draft into a generated street scene."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping

from ..street_layout import compose_street_scene
from ..types import StreetComposeConfig
from ..web_viewer_dev import build_web_viewer_url, cache_scene_layout_for_viewer
from .design_types import DesignDraft, SceneGenerationOptions, SceneGenerationResult, sanitize_compose_config_patch


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SCENE_GENERATION_OPTIONS = SceneGenerationOptions(
    manifest_path=(ROOT / "data" / "real" / "real_assets_manifest.jsonl").resolve(),
    artifacts_dir=(ROOT / "artifacts" / "real").resolve(),
    out_dir=(ROOT / "artifacts" / "real").resolve(),
    model_name="openai/clip-vit-base-patch32",
    model_dir=None,
    local_files_only=False,
    device="cpu",
    export_format="both",
    placement_policy="rule",
    policy_ckpt=None,
    program_ckpt=None,
    policy_temperature=0.12,
)


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
        length_m=float(patch.get("length_m", 80.0)),
        road_width_m=float(patch.get("road_width_m", 7.0)),
        sidewalk_width_m=float(patch.get("sidewalk_width_m", 2.4)),
        lane_count=int(patch.get("lane_count", 2)),
        density=float(patch.get("density", 1.0)),
        seed=42,
        topk_per_category=20,
        max_trials_per_slot=30,
        design_rule_profile=str(patch.get("design_rule_profile", "balanced_complete_street_v1")),
        target_street_type=str(patch.get("target_street_type", "mixed_use")),
        objective_profile=str(patch.get("objective_profile", "balanced")),
        city_context=str(patch.get("city_context", "generic_city")),
        ped_demand_level=str(patch.get("ped_demand_level", "medium")),
        bike_demand_level=str(patch.get("bike_demand_level", "low")),
        transit_demand_level=str(patch.get("transit_demand_level", "medium")),
        vehicle_demand_level=str(patch.get("vehicle_demand_level", "medium")),
    )


def normalize_scene_generation_options(
    overrides: Mapping[str, Any] | None = None,
) -> SceneGenerationOptions:
    """Coerce request overrides into scene-generation options."""

    if not overrides:
        return DEFAULT_SCENE_GENERATION_OPTIONS
    payload = dict(overrides)
    return SceneGenerationOptions(
        manifest_path=Path(str(payload.get("manifest_path", DEFAULT_SCENE_GENERATION_OPTIONS.manifest_path))).expanduser().resolve(),
        artifacts_dir=Path(str(payload.get("artifacts_dir", DEFAULT_SCENE_GENERATION_OPTIONS.artifacts_dir))).expanduser().resolve(),
        out_dir=Path(str(payload.get("out_dir", DEFAULT_SCENE_GENERATION_OPTIONS.out_dir))).expanduser().resolve(),
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


def generate_scene_from_draft(
    draft: DesignDraft,
    *,
    patch_overrides: Mapping[str, Any] | None = None,
    generation_options: Mapping[str, Any] | SceneGenerationOptions | None = None,
) -> SceneGenerationResult:
    """Run the existing scene pipeline using a confirmed design draft."""

    options = (
        generation_options
        if isinstance(generation_options, SceneGenerationOptions)
        else normalize_scene_generation_options(generation_options)
    )
    config = build_compose_config_from_draft(draft, patch_overrides=patch_overrides)
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
    )
    scene_layout_path = str(result.outputs.get("scene_layout", "") or "")
    viewer_url = ""
    summary: Dict[str, Any] = {
        "instance_count": int(result.instance_count),
        "dropped_slots": int(result.dropped_slots),
    }
    if scene_layout_path:
        cached_layout = cache_scene_layout_for_viewer(scene_layout_path)
        viewer_url = build_web_viewer_url(cached_layout)
        try:
            payload = json.loads(Path(scene_layout_path).read_text(encoding="utf-8"))
            summary = dict(payload.get("summary", {}) or summary)
        except Exception:
            pass
    return SceneGenerationResult(
        compose_config=config.to_dict(),
        summary=summary,
        scene_layout_path=scene_layout_path,
        scene_glb_path=str(result.outputs.get("scene_glb", "") or ""),
        scene_ply_path=str(result.outputs.get("scene_ply", "") or ""),
        viewer_url=viewer_url,
    )
