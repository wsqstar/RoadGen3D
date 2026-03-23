"""Shared datatypes for the LLM + RAG street design workflow."""

from __future__ import annotations

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
    "length_m",
    "road_width_m",
    "sidewalk_width_m",
    "lane_count",
    "density",
    "ped_demand_level",
    "bike_demand_level",
    "transit_demand_level",
    "vehicle_demand_level",
)
_PATCH_FIELD_SET = frozenset(ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS)
_FLOAT_FIELDS = frozenset({"length_m", "road_width_m", "sidewalk_width_m", "density"})
_INT_FIELDS = frozenset({"lane_count"})
_STRING_FIELDS = _PATCH_FIELD_SET - _FLOAT_FIELDS - _INT_FIELDS


def _clean_text(value: object) -> str:
    return str(value or "").strip()


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
            if text:
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
        }


@dataclass(frozen=True)
class DesignDraftBundle:
    """Top-level response of the draft design workflow."""

    intent: DesignIntent
    evidence: Tuple[RagEvidence, ...]
    draft: DesignDraft
    warnings: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent.to_dict(),
            "evidence": [item.to_dict() for item in self.evidence],
            "draft": self.draft.to_dict(),
            "warnings": list(self.warnings),
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
    local_files_only: bool = False
    device: str = "cpu"
    export_format: str = "both"
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
    result: SceneGenerationResult | None = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["result"] = self.result.to_dict() if self.result is not None else None
        return payload
