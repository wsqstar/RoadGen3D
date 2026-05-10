"""Shared datatypes for the LLM + RAG street design workflow."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple

from ..json_safe import make_json_safe


ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS: Tuple[str, ...] = (
    "query",
    "design_rule_profile",
    "target_street_type",
    "objective_profile",
    "city_context",
    "style_preset",
    "beauty_mode",
    "render_preset",
    "topdown_render_mode",
    "scene_texture_mode",
    "asset_curation_mode",
    "asset_scale_mode",
    "curated_street_assets_profile",
    "program_generator",
    "layout_solver",
    "length_m",
    "road_width_m",
    "sidewalk_width_m",
    "lane_count",
    "segment_length_m",
    "seed",
    "density",
    "building_density",
    "building_max_per_100m",
    "ped_demand_level",
    "bike_demand_level",
    "transit_demand_level",
    "vehicle_demand_level",
    "allow_solver_fallback",
    "osm_semantic_mode",
    "skeleton_design_profile",
    "skeleton_design_profile_source",
    "skeleton_design_profile_confidence",
    "skeleton_design_profile_reasons",
    "street_furniture_profile",
    "street_furniture_profile_source",
    "street_furniture_profile_confidence",
    "street_furniture_profile_reasons",
    "osm_multiblock_max_roads",
    "osm_multiblock_max_extent_m",
    "osm_short_road_policy",
    "osm_short_road_min_length_m",
    "osm_context_fit_mode",
    "bus_stop_eligible_road_names",
    "max_bus_stops_per_scene",
    "allow_demo_bus_stop_when_osm_absent",
    "max_styles_per_category",
    "amenity_coverage_mode",
    "minimum_category_presence",
    "optional_category_presence",
)
_PATCH_FIELD_SET = frozenset(ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS)
_FLOAT_FIELDS = frozenset({"length_m", "road_width_m", "sidewalk_width_m", "density", "building_density", "building_max_per_100m", "segment_length_m", "osm_multiblock_max_extent_m", "osm_short_road_min_length_m", "skeleton_design_profile_confidence", "street_furniture_profile_confidence"})
_INT_FIELDS = frozenset({"lane_count", "seed", "max_styles_per_category", "osm_multiblock_max_roads", "max_bus_stops_per_scene"})
_BOOL_FIELDS = frozenset({"allow_solver_fallback", "allow_demo_bus_stop_when_osm_absent"})
_LIST_FIELDS = frozenset({"minimum_category_presence", "optional_category_presence", "bus_stop_eligible_road_names", "skeleton_design_profile_reasons", "street_furniture_profile_reasons"})
_STRING_FIELDS = _PATCH_FIELD_SET - _FLOAT_FIELDS - _INT_FIELDS - _BOOL_FIELDS - _LIST_FIELDS
_EMPTY_TEXT_MARKERS = frozenset({"", "none", "null", "n/a", "na", "unspecified", "not specified"})

# Enum fields: value (lowercased) must be one of these sets, otherwise dropped.
_ENUM_VALID_VALUES: Dict[str, frozenset] = {
    "objective_profile": frozenset({"balanced", "greening", "commerce", "transit"}),
    "ped_demand_level": frozenset({"low", "medium", "high"}),
    "bike_demand_level": frozenset({"low", "medium", "high"}),
    "transit_demand_level": frozenset({"low", "medium", "high"}),
    "vehicle_demand_level": frozenset({"low", "medium", "high"}),
    "beauty_mode": frozenset({"presentation_v1"}),
    "render_preset": frozenset({"axonometric_board_v1"}),
    "topdown_render_mode": frozenset({"legacy_vector", "design_tiles_v1"}),
    "scene_texture_mode": frozenset({"topdown_tiles_v1", "solid_color_legacy"}),
    "asset_curation_mode": frozenset({"scene_ready_first", "curated_first", "parametric_first", "legacy"}),
    "asset_scale_mode": frozenset({"canonical_v1", "native_raw"}),
    "curated_street_assets_profile": frozenset({"fixed_hq_v1", "disabled"}),
    "amenity_coverage_mode": frozenset({"off", "try"}),
    "program_generator": frozenset({"heuristic_v1", "learned_v1"}),
    "layout_solver": frozenset({"banded", "milp_template_v1", "hybrid_milp_v1"}),
    "osm_semantic_mode": frozenset({"landuse_rules_v1"}),
    "skeleton_design_profile": frozenset({"child_friendly_school", "walkable_commercial", "vehicle_access_commercial", "transit_priority", "green_walkable", "quiet_residential"}),
    "street_furniture_profile": frozenset({"balanced_complete", "pedestrian_friendly", "commercial_vitality", "transit_priority", "park_landscape", "quiet_residential"}),
    "skeleton_design_profile_source": frozenset({"manual", "llm", "osm", "recommended", "fallback"}),
    "street_furniture_profile_source": frozenset({"manual", "llm", "osm", "recommended", "fallback"}),
    "osm_short_road_policy": frozenset({"semantic", "default_style"}),
    "osm_context_fit_mode": frozenset({"off", "report", "auto_design"}),
}

DEFAULT_COMPOSE_CONFIG_PATCH_VALUES: Dict[str, Any] = {
    "design_rule_profile": "balanced_complete_street_v1",
    "target_street_type": "mixed_use",
    "objective_profile": "balanced",
    "city_context": "generic_city",
    "style_preset": "civic_clean_v1",
    "beauty_mode": "presentation_v1",
    "render_preset": "axonometric_board_v1",
    "topdown_render_mode": "design_tiles_v1",
    "scene_texture_mode": "topdown_tiles_v1",
    "asset_curation_mode": "scene_ready_first",
    "asset_scale_mode": "canonical_v1",
    "curated_street_assets_profile": "fixed_hq_v1",
    "program_generator": "heuristic_v1",
    "layout_solver": "hybrid_milp_v1",
    "length_m": 80.0,
    "road_width_m": 7.0,
    "sidewalk_width_m": 2.4,
    "lane_count": 2,
    "seed": 42,
    "density": 1.0,
    "building_density": 0.55,
    "building_max_per_100m": 10.0,
    "ped_demand_level": "medium",
    "bike_demand_level": "low",
    "transit_demand_level": "medium",
    "vehicle_demand_level": "medium",
    "allow_solver_fallback": True,
    "segment_length_m": 12.0,
    "osm_semantic_mode": "landuse_rules_v1",
    "skeleton_design_profile": "",
    "skeleton_design_profile_source": "",
    "skeleton_design_profile_confidence": 0.0,
    "skeleton_design_profile_reasons": (),
    "street_furniture_profile": "",
    "street_furniture_profile_source": "",
    "street_furniture_profile_confidence": 0.0,
    "street_furniture_profile_reasons": (),
    "osm_multiblock_max_roads": 12,
    "osm_multiblock_max_extent_m": 350.0,
    "osm_short_road_policy": "semantic",
    "osm_short_road_min_length_m": 0.0,
    "osm_context_fit_mode": "auto_design",
    "bus_stop_eligible_road_names": (),
    "max_bus_stops_per_scene": 0,
    "allow_demo_bus_stop_when_osm_absent": False,
    "max_styles_per_category": 3,
    "amenity_coverage_mode": "try",
    "minimum_category_presence": ("trash", "bench", "lamp"),
    "optional_category_presence": ("mailbox", "hydrant"),
}


def _clean_text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in _EMPTY_TEXT_MARKERS else text


def sanitize_compose_config_patch(payload: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Coerce an arbitrary mapping into an allowlisted compose-config patch."""

    if payload is None:
        return {}
    patch: Dict[str, Any] = {}
    for key, value in dict(payload).items():
        if key not in _PATCH_FIELD_SET or value is None:
            continue
        if key in _FLOAT_FIELDS:
            try:
                patch[key] = float(value)
            except (TypeError, ValueError):
                continue
        elif key in _INT_FIELDS:
            try:
                patch[key] = int(value)
            except (TypeError, ValueError):
                continue
        elif key in _BOOL_FIELDS:
            if isinstance(value, bool):
                patch[key] = value
            elif isinstance(value, str):
                normalized_bool = value.strip().lower()
                if normalized_bool in {"1", "true", "yes", "on"}:
                    patch[key] = True
                elif normalized_bool in {"0", "false", "no", "off"}:
                    patch[key] = False
                else:
                    continue
            elif isinstance(value, (int, float)):
                patch[key] = bool(value)
            else:
                continue
        elif key in _LIST_FIELDS:
            if isinstance(value, str):
                items = [item.strip().lower() for item in value.replace(";", ",").split(",")]
            elif isinstance(value, Sequence):
                items = [str(item).strip().lower() for item in value]
            else:
                items = []
            patch[key] = tuple(dict.fromkeys(item for item in items if item))
        elif key in _STRING_FIELDS:
            text = _clean_text(value)
            if not text:
                continue
            if key in _ENUM_VALID_VALUES:
                if text.lower() not in _ENUM_VALID_VALUES[key]:
                    continue
            patch[key] = text
    return patch


def sanitize_citations_by_field(
    payload: Mapping[str, Any] | None,
    *,
    allowed_fields: Sequence[str] | None = None,
) -> Dict[str, Tuple[str, ...]]:
    """Normalize field -> citation ids mapping."""

    if payload is None:
        return {}
    allowed = set(allowed_fields or ())
    result: Dict[str, Tuple[str, ...]] = {}
    for key, value in dict(payload).items():
        field_name = _clean_text(key)
        if not field_name:
            continue
        if allowed and field_name not in allowed:
            continue
        if isinstance(value, str):
            items = (value,)
        else:
            items = tuple(_clean_text(item) for item in (value or []))
        citations = tuple(dict.fromkeys(item for item in items if item))
        if citations:
            result[field_name] = citations
    return result


def _coerce_bbox_tuple(value: object) -> Tuple[float, float, float, float] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        bbox = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in bbox):
        return None
    min_lon, min_lat, max_lon, max_lat = bbox
    if min_lon >= max_lon or min_lat >= max_lat:
        return None
    return bbox


@dataclass(frozen=True)
class ChatMessage:
    """One conversational turn passed to the design assistant."""

    role: str
    content: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DesignIntent:
    """LLM-parsed user intent before RAG enrichment."""

    user_goals: Tuple[str, ...] = ()
    style_preferences: Tuple[str, ...] = ()
    safety_priorities: Tuple[str, ...] = ()
    follow_up_questions: Tuple[str, ...] = ()
    rag_queries: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        return {key: list(value) for key, value in payload.items()}


@dataclass(frozen=True)
class RagEvidence:
    """One retrieved knowledge excerpt shown to the user."""

    chunk_id: str
    doc_id: str
    section_title: str
    page_start: int
    page_end: int
    text: str
    source_path: str
    score: float = 0.0
    relevance_reason: str = ""
    knowledge_source: str = "pdf_rag"
    parameter_hints: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DesignDraft:
    """Structured design proposal passed into scene generation."""

    normalized_scene_query: str
    compose_config_patch: Dict[str, Any]
    citations_by_field: Dict[str, Tuple[str, ...]]
    design_summary: str
    risk_notes: Tuple[str, ...] = ()
    parameter_sources_by_field: Dict[str, str] = field(default_factory=dict)
    template_patch: Dict[str, Any] | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "normalized_scene_query": self.normalized_scene_query,
            "compose_config_patch": dict(self.compose_config_patch),
            "citations_by_field": {
                key: list(value)
                for key, value in self.citations_by_field.items()
            },
            "design_summary": self.design_summary,
            "risk_notes": list(self.risk_notes),
            "parameter_sources_by_field": dict(self.parameter_sources_by_field),
            "template_patch": dict(self.template_patch) if isinstance(self.template_patch, Mapping) else None,
        }


@dataclass(frozen=True)
class SceneContext:
    """Runtime-only scene setup that stays outside the LLM draft patch."""

    layout_mode: str = "template"
    aoi_bbox: Tuple[float, float, float, float] | None = None
    city_name_en: str | None = None
    reference_plan_id: str | None = None
    graph_template_id: str | None = None
    reference_annotation_path: str | None = None
    template_patch: Dict[str, Any] | None = None
    scenario_id: str | None = None
    scenario_title: str | None = None
    scenario_design_variant: Dict[str, Any] | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layout_mode": self.layout_mode,
            "aoi_bbox": list(self.aoi_bbox) if self.aoi_bbox is not None else None,
            "city_name_en": self.city_name_en,
            "reference_plan_id": self.reference_plan_id,
            "graph_template_id": self.graph_template_id,
            "reference_annotation_path": self.reference_annotation_path,
            "template_patch": dict(self.template_patch) if isinstance(self.template_patch, Mapping) else None,
            "scenario_id": self.scenario_id,
            "scenario_title": self.scenario_title,
            "scenario_design_variant": (
                dict(self.scenario_design_variant)
                if isinstance(self.scenario_design_variant, Mapping)
                else None
            ),
        }


def sanitize_scene_context(payload: Mapping[str, Any] | SceneContext | None) -> SceneContext:
    """Normalize runtime scene context received from the API or UI."""

    if isinstance(payload, SceneContext):
        return payload
    raw = dict(payload or {})
    layout_mode = str(raw.get("layout_mode", "template") or "template").strip().lower()
    if layout_mode not in {"template", "osm", "osm_multiblock", "metaurban", "graph_template", "reference_annotation"}:
        layout_mode = "template"
    city_name_en = _clean_text(raw.get("city_name_en")) or None
    reference_plan_id = _clean_text(raw.get("reference_plan_id")) or None
    graph_template_id = _clean_text(raw.get("graph_template_id")) or None
    reference_annotation_path = _clean_text(raw.get("reference_annotation_path")) or None
    scenario_id = _clean_text(raw.get("scenario_id")) or None
    scenario_title = _clean_text(raw.get("scenario_title")) or None
    raw_template_patch = raw.get("template_patch")
    template_patch = dict(raw_template_patch) if isinstance(raw_template_patch, Mapping) else None
    raw_scenario_design_variant = raw.get("scenario_design_variant")
    scenario_design_variant = (
        dict(raw_scenario_design_variant)
        if isinstance(raw_scenario_design_variant, Mapping)
        else None
    )
    return SceneContext(
        layout_mode=layout_mode,
        aoi_bbox=_coerce_bbox_tuple(raw.get("aoi_bbox")),
        city_name_en=city_name_en,
        reference_plan_id=reference_plan_id if layout_mode == "metaurban" else None,
        graph_template_id=graph_template_id if layout_mode == "graph_template" else None,
        reference_annotation_path=reference_annotation_path if layout_mode == "reference_annotation" else None,
        template_patch=template_patch if layout_mode == "graph_template" else None,
        scenario_id=scenario_id if layout_mode in {"graph_template", "reference_annotation"} else None,
        scenario_title=scenario_title if layout_mode in {"graph_template", "reference_annotation"} else None,
        scenario_design_variant=(
            scenario_design_variant
            if layout_mode in {"graph_template", "reference_annotation"}
            else None
        ),
    )


@dataclass(frozen=True)
class DesignDraftBundle:
    """Top-level response of the draft design workflow."""

    stage: str
    intent: DesignIntent
    evidence: Tuple[RagEvidence, ...]
    draft: DesignDraft | None
    warnings: Tuple[str, ...] = ()
    cache_hit: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage,
            "intent": self.intent.to_dict(),
            "evidence": [item.to_dict() for item in self.evidence],
            "draft": self.draft.to_dict() if self.draft is not None else None,
            "warnings": list(self.warnings),
            "cache_hit": bool(self.cache_hit),
        }


@dataclass(frozen=True)
class SceneGenerationOptions:
    """Execution options for the scene generator."""

    manifest_path: Path
    artifacts_dir: Path
    out_dir: Path
    preset_id: str = ""
    random_seed: int | None = None
    object_manifest_v2_path: Path | None = None
    ground_material_manifest_path: Path | None = None
    sky_manifest_path: Path | None = None
    model_name: str = "openai/clip-vit-base-patch32"
    model_dir: Path | None = None
    local_files_only: bool = True
    device: str = "cpu"
    export_format: str = "glb"
    placement_policy: str = "rule"
    policy_ckpt: Path | None = None
    program_ckpt: Path | None = None
    policy_temperature: float = 0.12
    build_production_artifacts: bool = True
    render_presentation_artifacts: bool = True
    capture_3d_views: bool = True
    capture_profile: str = "review_expanded"
    capture_resolution: tuple[int, int] = (1280, 720)
    capture_failure_policy: str = "warn"
    retain_glb_policy: str = "top_k"
    capture_defer_glb_retention: bool = False
    manifest_paths: Tuple[Path, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "preset_id": self.preset_id,
            "random_seed": self.random_seed,
            "manifest_path": str(self.manifest_path),
            "manifest_paths": [str(path) for path in self.manifest_paths],
            "artifacts_dir": str(self.artifacts_dir),
            "out_dir": str(self.out_dir),
            "object_manifest_v2_path": str(self.object_manifest_v2_path) if self.object_manifest_v2_path is not None else None,
            "ground_material_manifest_path": (
                str(self.ground_material_manifest_path) if self.ground_material_manifest_path is not None else None
            ),
            "sky_manifest_path": str(self.sky_manifest_path) if self.sky_manifest_path is not None else None,
            "model_name": self.model_name,
            "model_dir": str(self.model_dir) if self.model_dir is not None else None,
            "local_files_only": bool(self.local_files_only),
            "device": self.device,
            "export_format": self.export_format,
            "placement_policy": self.placement_policy,
            "policy_ckpt": str(self.policy_ckpt) if self.policy_ckpt is not None else None,
            "program_ckpt": str(self.program_ckpt) if self.program_ckpt is not None else None,
            "policy_temperature": float(self.policy_temperature),
            "build_production_artifacts": bool(self.build_production_artifacts),
            "render_presentation_artifacts": bool(self.render_presentation_artifacts),
            "capture_3d_views": bool(self.capture_3d_views),
            "capture_profile": self.capture_profile,
            "capture_resolution": list(self.capture_resolution),
            "capture_failure_policy": self.capture_failure_policy,
            "retain_glb_policy": self.retain_glb_policy,
            "capture_defer_glb_retention": bool(self.capture_defer_glb_retention),
        }


@dataclass(frozen=True)
class SceneGenerationResult:
    """Serializable generation result for the UI and API."""

    compose_config: Dict[str, Any]
    summary: Dict[str, Any]
    scene_layout_path: str
    scene_glb_path: str = ""
    scene_ply_path: str = ""
    viewer_url: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dict(make_json_safe(asdict(self)))


@dataclass(frozen=True)
class SceneRecord:
    """One generated scene record retained by the in-memory job service."""

    job_id: str
    status: str
    created_at: str
    finished_at: str = ""
    scene_layout_path: str = ""
    scene_glb_path: str = ""
    scene_ply_path: str = ""
    viewer_url: str = ""
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dict(make_json_safe(asdict(self)))


@dataclass(frozen=True)
class SceneJobCreateResponse:
    """Response returned when a scene generation job is queued."""

    job_id: str
    status: str
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SceneJobStatusResponse:
    """Serializable job status for the Web API and workbench."""

    job_id: str
    status: str
    created_at: str
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    stage: str = "queued"
    progress: int = 0
    operations: Tuple[Dict[str, Any], ...] = ()
    result: SceneGenerationResult | None = None
    trace: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["result"] = self.result.to_dict() if self.result is not None else None
        payload["operations"] = list(self.operations)
        return dict(make_json_safe(payload))
