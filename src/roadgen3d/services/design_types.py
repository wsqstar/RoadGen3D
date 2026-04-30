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
    "length_m",
    "road_width_m",
    "sidewalk_width_m",
    "lane_count",
    "density",
    "building_density",
    "building_max_per_100m",
    "ped_demand_level",
    "bike_demand_level",
    "transit_demand_level",
    "vehicle_demand_level",
)
_PATCH_FIELD_SET = frozenset(ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS)
_FLOAT_FIELDS = frozenset({"length_m", "road_width_m", "sidewalk_width_m", "density", "building_density", "building_max_per_100m"})
_INT_FIELDS = frozenset({"lane_count"})
_STRING_FIELDS = _PATCH_FIELD_SET - _FLOAT_FIELDS - _INT_FIELDS
_EMPTY_TEXT_MARKERS = frozenset({"", "none", "null", "n/a", "na", "unspecified", "not specified"})

# Enum fields: value (lowercased) must be one of these sets, otherwise dropped.
_ENUM_VALID_VALUES: Dict[str, frozenset] = {
    "objective_profile": frozenset({"balanced", "greening", "commerce", "transit"}),
    "ped_demand_level": frozenset({"low", "medium", "high"}),
    "bike_demand_level": frozenset({"low", "medium", "high"}),
    "transit_demand_level": frozenset({"low", "medium", "high"}),
    "vehicle_demand_level": frozenset({"low", "medium", "high"}),
    "beauty_mode": frozenset({"presentation_v1"}),
}

DEFAULT_COMPOSE_CONFIG_PATCH_VALUES: Dict[str, Any] = {
    "design_rule_profile": "balanced_complete_street_v1",
    "target_street_type": "mixed_use",
    "objective_profile": "balanced",
    "city_context": "generic_city",
    "style_preset": "civic_clean_v1",
    "beauty_mode": "presentation_v1",
    "length_m": 80.0,
    "road_width_m": 7.0,
    "sidewalk_width_m": 2.4,
    "lane_count": 2,
    "density": 1.0,
    "building_density": 0.55,
    "building_max_per_100m": 10.0,
    "ped_demand_level": "medium",
    "bike_demand_level": "low",
    "transit_demand_level": "medium",
    "vehicle_demand_level": "medium",
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
        }


@dataclass(frozen=True)
class SceneContext:
    """Runtime-only scene setup that stays outside the LLM draft patch."""

    layout_mode: str = "template"
    aoi_bbox: Tuple[float, float, float, float] | None = None
    city_name_en: str | None = None
    reference_plan_id: str | None = None
    graph_template_id: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layout_mode": self.layout_mode,
            "aoi_bbox": list(self.aoi_bbox) if self.aoi_bbox is not None else None,
            "city_name_en": self.city_name_en,
            "reference_plan_id": self.reference_plan_id,
            "graph_template_id": self.graph_template_id,
        }


def sanitize_scene_context(payload: Mapping[str, Any] | SceneContext | None) -> SceneContext:
    """Normalize runtime scene context received from the API or UI."""

    if isinstance(payload, SceneContext):
        return payload
    raw = dict(payload or {})
    layout_mode = str(raw.get("layout_mode", "template") or "template").strip().lower()
    if layout_mode not in {"template", "osm", "metaurban", "graph_template"}:
        layout_mode = "template"
    city_name_en = _clean_text(raw.get("city_name_en")) or None
    reference_plan_id = _clean_text(raw.get("reference_plan_id")) or None
    graph_template_id = _clean_text(raw.get("graph_template_id")) or None
    return SceneContext(
        layout_mode=layout_mode,
        aoi_bbox=_coerce_bbox_tuple(raw.get("aoi_bbox")),
        city_name_en=city_name_en,
        reference_plan_id=reference_plan_id if layout_mode == "metaurban" else None,
        graph_template_id=graph_template_id if layout_mode == "graph_template" else None,
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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest_path": str(self.manifest_path),
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

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["result"] = self.result.to_dict() if self.result is not None else None
        payload["operations"] = list(self.operations)
        return dict(make_json_safe(payload))
