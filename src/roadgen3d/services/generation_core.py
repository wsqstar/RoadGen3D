"""Core scene generation logic without LLM dependencies.

This module provides direct scene generation APIs for the web viewer,
bypassing the LLM/RAG workflow. The LLM upstream can still use
design_runtime.generate_scene_from_draft() for enriched designs.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from ..json_safe import make_json_safe
from ..graph_template_scene_bridge import build_graph_template_scene_bridge
from ..metaurban_procedural import MetaUrbanProceduralConfig
from ..metaurban_scene_bridge import build_metaurban_scene_bridge
from ..street_layout import compose_street_scene
from ..types import StreetComposeConfig
from ..web_viewer_dev import build_web_viewer_url, cache_scene_layout_for_viewer
from .design_types import SceneGenerationOptions
from .scene_backends import (
    DEFAULT_GROUND_MATERIAL_MANIFEST_PATH,
    DEFAULT_OBJECT_MANIFEST_V2_PATH,
    DEFAULT_SKY_MANIFEST_PATH,
    ManifestGroundMaterialBackend,
    ManifestObjectAssetBackend,
    ManifestSkyBackend,
)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_METAURBAN_REFERENCE_PLAN_ID = "hkust_gz_gate"
DEFAULT_GRAPH_TEMPLATE_ID = "hkust_gz_gate"
DEFAULT_CLIP_MODEL_DIR = (ROOT / "models" / "clip-vit-base-patch32").resolve()


@dataclass(frozen=True)
class MetaurbanDesignParams:
    """Parameters for direct MetaUrban design generation."""

    reference_plan_id: str = DEFAULT_METAURBAN_REFERENCE_PLAN_ID
    lane_count: int = 2
    lane_width_m: float = 3.5
    sidewalk_width_m: float = 2.5
    road_width_m: Optional[float] = None  # auto-calculated if None
    segment_length_m: float = 12.0
    start_heading_deg: float = 0.0
    # Block sequence override (optional)
    block_sequence: Optional[str] = None
    block_count: int = 6
    seed: int = 42


@dataclass(frozen=True)
class TemplateDesignParams:
    """Parameters for direct graph template design generation."""

    template_id: str = DEFAULT_GRAPH_TEMPLATE_ID
    lane_count: int = 2
    lane_width_m: float = 3.5
    sidewalk_width_m: float = 2.5
    road_width_m: float = 7.0
    length_m: float = 80.0
    seed: int = 42


@dataclass(frozen=True)
class OsmDesignParams:
    """Parameters for direct OSM-based design generation."""

    city_name_en: str = "generic_city"
    lane_count: int = 2
    lane_width_m: float = 3.5
    sidewalk_width_m: float = 2.5
    road_width_m: float = 7.0
    length_m: float = 80.0
    aoi_bbox: Optional[Tuple[float, float, float, float]] = None
    road_selection: str = "auto"
    seed: int = 42


@dataclass
class GenerationOptions:
    """Runtime options for scene generation."""

    manifest_path: Path = field(
        default_factory=lambda: (ROOT / "data" / "real" / "real_assets_manifest.jsonl").resolve()
    )
    artifacts_dir: Path = field(
        default_factory=lambda: (ROOT / "artifacts" / "real").resolve()
    )
    out_dir: Path = field(
        default_factory=lambda: (ROOT / "artifacts" / "real").resolve()
    )
    object_manifest_v2_path: Optional[Path] = field(
        default_factory=lambda: DEFAULT_OBJECT_MANIFEST_V2_PATH
    )
    ground_material_manifest_path: Optional[Path] = field(
        default_factory=lambda: DEFAULT_GROUND_MATERIAL_MANIFEST_PATH
    )
    sky_manifest_path: Optional[Path] = field(
        default_factory=lambda: DEFAULT_SKY_MANIFEST_PATH
    )
    model_name: str = "openai/clip-vit-base-patch32"
    model_dir: Optional[Path] = field(default_factory=lambda: DEFAULT_CLIP_MODEL_DIR)
    local_files_only: bool = True
    device: str = "cpu"
    export_format: str = "glb"  # "glb", "ply", "both"
    placement_policy: str = "rule"  # "rule" or "policy"
    policy_ckpt: Optional[Path] = None
    program_ckpt: Optional[Path] = None
    policy_temperature: float = 0.12

    def to_legacy_options(self) -> SceneGenerationOptions:
        """Convert to legacy SceneGenerationOptions for compatibility."""
        return SceneGenerationOptions(
            manifest_path=self.manifest_path,
            artifacts_dir=self.artifacts_dir,
            out_dir=self.out_dir,
            object_manifest_v2_path=self.object_manifest_v2_path,
            ground_material_manifest_path=self.ground_material_manifest_path,
            sky_manifest_path=self.sky_manifest_path,
            model_name=self.model_name,
            model_dir=self.model_dir,
            local_files_only=self.local_files_only,
            device=self.device,
            export_format=self.export_format,
            placement_policy=self.placement_policy,
            policy_ckpt=self.policy_ckpt,
            program_ckpt=self.program_ckpt,
            policy_temperature=self.policy_temperature,
        )


@dataclass
class SceneGenerationResult:
    """Result of scene generation."""

    job_id: str
    status: str  # "completed", "failed", "processing"
    compose_config: Dict[str, Any] = field(default_factory=dict)
    summary: Dict[str, Any] = field(default_factory=dict)
    scene_layout_path: str = ""
    scene_glb_path: str = ""
    scene_ply_path: str = ""
    viewer_url: str = ""
    error: str = ""
    created_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dict(make_json_safe(asdict(self)))


def _build_compose_config(
    params: MetaurbanDesignParams | TemplateDesignParams | OsmDesignParams,
    layout_mode: str = "template",
) -> StreetComposeConfig:
    """Build StreetComposeConfig from design parameters."""
    road_width = (
        params.road_width_m
        if params.road_width_m is not None
        else float(params.lane_count) * float(params.lane_width_m)
    )

    return StreetComposeConfig(
        query="direct_design_generation",
        length_m=float(getattr(params, "length_m", 80.0)),
        road_width_m=float(road_width),
        sidewalk_width_m=float(params.sidewalk_width_m),
        lane_count=int(params.lane_count),
        density=1.0,
        seed=int(getattr(params, "seed", 42)),
        topk_per_category=20,
        max_trials_per_slot=30,
        design_rule_profile="balanced_complete_street_v1",
        target_street_type="mixed_use",
        objective_profile="balanced",
        city_context="generic_city",
        style_preset="civic_clean_v1",
        beauty_mode="presentation_v1",
        ped_demand_level="medium",
        bike_demand_level="low",
        transit_demand_level="medium",
        vehicle_demand_level="medium",
        layout_mode=layout_mode,
    )


def _build_scene_backends(options: GenerationOptions):
    """Create asset backends from options."""
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


def _build_out_dir(base_out_dir: Path, prefix: str, identifier: str) -> Path:
    """Build timestamped output directory."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (Path(base_out_dir).expanduser().resolve() / prefix / identifier / timestamp).resolve()


def _augment_layout_summary(layout_path: str | Path, extra_summary: Dict[str, Any]) -> None:
    """Add extra metadata to scene layout JSON."""
    path = Path(layout_path)
    if not path.exists() or not extra_summary:
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        summary = dict(payload.get("summary", {}) or {})
        summary.update(dict(make_json_safe(extra_summary)))
        payload["summary"] = summary
        path.write_text(json.dumps(make_json_safe(payload), ensure_ascii=True, indent=2), encoding="utf-8")
    except Exception:
        pass


def _build_result(
    job_id: str,
    config: StreetComposeConfig,
    compose_result: Any,
    extra_summary: Optional[Dict[str, Any]] = None,
) -> SceneGenerationResult:
    """Build SceneGenerationResult from compose result."""
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
        job_id=job_id,
        status="completed",
        compose_config=dict(make_json_safe(config.to_dict())),
        summary=dict(make_json_safe(summary)),
        scene_layout_path=scene_layout_path,
        scene_glb_path=str(compose_result.outputs.get("scene_glb", "") or ""),
        scene_ply_path=str(compose_result.outputs.get("scene_ply", "") or ""),
        viewer_url=viewer_url,
        created_at=datetime.now(timezone.utc).isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
    )


def generate_metaurban_scene(
    params: MetaurbanDesignParams,
    options: Optional[GenerationOptions] = None,
) -> SceneGenerationResult:
    """Generate a MetaUrban-style street scene directly.

    Args:
        params: MetaUrban design parameters
        options: Runtime options (manifests, output dirs, etc.)

    Returns:
        SceneGenerationResult with paths to generated files

    Raises:
        RuntimeError: If reference plan not found or generation fails
    """
    job_id = f"mu_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    options = options or GenerationOptions()

    try:
        # Build compose config
        config = _build_compose_config(params, layout_mode="metaurban")

        # Build MetaUrban bridge
        bridge = build_metaurban_scene_bridge(
            config,
            plan_id=params.reference_plan_id,
        )

        # Setup output directory
        out_dir = _build_out_dir(options.out_dir, "metaurban", params.reference_plan_id)

        # Create backends
        object_backend, ground_backend, sky_backend = _build_scene_backends(options)

        # Compose scene
        result = compose_street_scene(
            config=config,
            manifest_path=options.manifest_path,
            artifacts_dir=options.artifacts_dir,
            model_name=options.model_name,
            model_dir=options.model_dir,
            local_files_only=options.local_files_only,
            device=options.device,
            export_format=options.export_format,
            out_dir=out_dir,
            placement_policy=options.placement_policy,
            policy_ckpt=options.policy_ckpt,
            program_ckpt=options.program_ckpt,
            policy_temperature=options.policy_temperature,
            object_asset_backend=object_backend,
            ground_material_backend=ground_backend,
            sky_backend=sky_backend,
            road_segment_graph_override=bridge.road_segment_graph,
            projected_features_override=bridge.projected_features,
            placement_context_override=bridge.placement_context,
        )

        return _build_result(job_id, config, result, extra_summary=bridge.summary_metadata)

    except KeyError as exc:
        return SceneGenerationResult(
            job_id=job_id,
            status="failed",
            error=f"Reference plan not found: {params.reference_plan_id}",
            created_at=datetime.now(timezone.utc).isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as exc:
        return SceneGenerationResult(
            job_id=job_id,
            status="failed",
            error=str(exc),
            created_at=datetime.now(timezone.utc).isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )


def generate_template_scene(
    params: TemplateDesignParams,
    options: Optional[GenerationOptions] = None,
) -> SceneGenerationResult:
    """Generate a graph template-based street scene directly.

    Args:
        params: Graph template design parameters
        options: Runtime options

    Returns:
        SceneGenerationResult with paths to generated files
    """
    job_id = f"gt_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    options = options or GenerationOptions()

    try:
        config = _build_compose_config(params, layout_mode="graph_template")

        bridge = build_graph_template_scene_bridge(
            config,
            template_id=params.template_id,
        )

        out_dir = _build_out_dir(options.out_dir, "graph_template", params.template_id)
        object_backend, ground_backend, sky_backend = _build_scene_backends(options)

        result = compose_street_scene(
            config=config,
            manifest_path=options.manifest_path,
            artifacts_dir=options.artifacts_dir,
            model_name=options.model_name,
            model_dir=options.model_dir,
            local_files_only=options.local_files_only,
            device=options.device,
            export_format=options.export_format,
            out_dir=out_dir,
            placement_policy=options.placement_policy,
            policy_ckpt=options.policy_ckpt,
            program_ckpt=options.program_ckpt,
            policy_temperature=options.policy_temperature,
            object_asset_backend=object_backend,
            ground_material_backend=ground_backend,
            sky_backend=sky_backend,
            road_segment_graph_override=bridge.road_segment_graph,
            projected_features_override=bridge.projected_features,
            placement_context_override=bridge.placement_context,
        )

        return _build_result(job_id, config, result, extra_summary=bridge.summary_metadata)

    except KeyError as exc:
        return SceneGenerationResult(
            job_id=job_id,
            status="failed",
            error=f"Graph template not found: {params.template_id}",
            created_at=datetime.now(timezone.utc).isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as exc:
        return SceneGenerationResult(
            job_id=job_id,
            status="failed",
            error=str(exc),
            created_at=datetime.now(timezone.utc).isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )


def generate_osm_scene(
    params: OsmDesignParams,
    options: Optional[GenerationOptions] = None,
) -> SceneGenerationResult:
    """Generate an OSM-based street scene directly.

    This is a placeholder - full OSM implementation requires additional work.
    """
    job_id = f"osm_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    options = options or GenerationOptions()

    # TODO: Implement OSM-based generation
    return SceneGenerationResult(
        job_id=job_id,
        status="failed",
        error="OSM-based generation is not yet implemented in the new API",
        created_at=datetime.now(timezone.utc).isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
    )


__all__ = [
    "MetaurbanDesignParams",
    "TemplateDesignParams",
    "OsmDesignParams",
    "GenerationOptions",
    "SceneGenerationResult",
    "generate_metaurban_scene",
    "generate_template_scene",
    "generate_osm_scene",
]
