"""Request contracts for the course-oriented project API."""

from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field


class BootstrapRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(min_length=1, max_length=120)
    bootstrap_token: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(min_length=1, max_length=120)
    course_code: str = Field(min_length=1, max_length=64)
    invite_code: str = Field(min_length=1, max_length=128)


class PersonalRegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(min_length=1, max_length=120)
    invite_code: str = Field(min_length=1, max_length=128)


class CourseCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    code: str = Field(min_length=1, max_length=64)


class ProjectCreateRequest(BaseModel):
    course_id: str
    name: str = Field(min_length=1, max_length=180)
    city: str = Field(default="广州", max_length=120)
    design_goal: str = Field(default="balanced_street", max_length=240)
    aoi_bbox: List[float] | None = Field(default=None, min_length=4, max_length=4)


class WorkspaceProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    city: str = Field(default="广州", max_length=120)
    design_goal: str = Field(default="balanced_street", max_length=240)
    aoi_bbox: List[float] | None = Field(default=None, min_length=4, max_length=4)


class RegistrationInviteCreateRequest(BaseModel):
    expires_in_hours: int = Field(default=72, ge=1, le=720)
    max_uses: int = Field(default=1, ge=1, le=1000)
    note: str = Field(default="", max_length=240)


class UserStatusUpdateRequest(BaseModel):
    is_active: bool


class WorkflowStepRequest(BaseModel):
    workflow_step: Literal["area", "data", "annotation", "design", "evaluation", "compare_export"]


class SceneAssetRefModel(BaseModel):
    manifestName: str = Field(min_length=1, max_length=240)
    assetId: str = Field(min_length=1, max_length=256)
    fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    category: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=240)


class SceneAssetPaletteModel(BaseModel):
    schemaVersion: Literal["roadgen3d.asset-palette.v1"] = "roadgen3d.asset-palette.v1"
    assets: List[SceneAssetRefModel] = Field(default_factory=list, max_length=200)


class GeoJsonImportRequest(BaseModel):
    geojson: Dict[str, Any]


class ReferenceAnnotationImportRequest(BaseModel):
    annotation: Dict[str, Any]


class OsmImportRequest(BaseModel):
    force_refetch: bool = False


class OsmRoadStudySelectionRequest(BaseModel):
    raw_artifact_id: str = Field(min_length=1, max_length=64)
    preview_id: str = Field(min_length=1, max_length=64)
    seed_logical_road_id: str = Field(min_length=1, max_length=160)
    hop_count: Literal[1, 2] = 1
    context_buffer_m: float = Field(default=100.0, ge=25.0, le=300.0)


class AnnotationReviewRequest(BaseModel):
    annotation: Dict[str, Any] | None = None
    geojson: Dict[str, Any] | None = None
    actions: List[Dict[str, Any]] = Field(default_factory=list, max_length=1_000)
    notes: str = Field(default="", max_length=2_000)


class SceneGenerateRequest(BaseModel):
    source_id: str
    prompt: str = Field(default="", max_length=2_000)
    generation_mode: Literal["baseline", "auto", "llm", "parametric"] = "baseline"
    parent_revision_id: str | None = None
    goal_weights: Dict[str, float] | None = None
    candidate_count: int = Field(default=1, ge=1, le=5)
    minimum_scores: Dict[str, float] | None = None


class SceneJobAdoptRequest(BaseModel):
    job_id: str = Field(min_length=1, max_length=64)
    source_id: str | None = Field(default=None, max_length=64)


class RevisionImportLayoutRequest(BaseModel):
    layout_path: str = Field(min_length=1, max_length=4_096)
    label: str = Field(default="Imported professional scene", max_length=180)
    source_id: str | None = Field(default=None, max_length=64)


class RevisionCreateRequest(BaseModel):
    layout: Dict[str, Any]
    glb_base64: str | None = Field(default=None, max_length=140_000_000)
    source_id: str | None = None
    parent_id: str | None = None
    branch_kind: Literal["baseline", "human_edit", "ai_edit"] = "baseline"
    label: str = Field(default="", max_length=180)
    commands: List[Dict[str, Any]] = Field(default_factory=list, max_length=100)
    provenance: Dict[str, Any] = Field(default_factory=dict)
    auto_evaluate: bool = True
    auto_evaluate_mode: Literal["structured", "full"] = "structured"
    evaluation_profile_id: str | None = None
    evaluation_weights: Dict[str, float] | None = None


class RevisionEditRequest(BaseModel):
    commands: List[Dict[str, Any]] = Field(min_length=1, max_length=100)
    branch_kind: Literal["human_edit", "ai_edit"] = "human_edit"
    label: str = Field(default="Edited scene", max_length=180)
    provenance: Dict[str, Any] = Field(default_factory=dict)
    auto_evaluate: bool = True
    auto_evaluate_mode: Literal["structured", "full"] = "structured"
    evaluation_profile_id: str | None = None
    evaluation_weights: Dict[str, float] | None = None


class RevisionForkRequest(BaseModel):
    branch_kind: Literal["human_edit", "ai_edit"] = "human_edit"
    label: str = Field(default="Forked scene", max_length=180)
    provenance: Dict[str, Any] = Field(default_factory=dict)


class EvaluationProfileCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    weights: Dict[str, float]


class EvaluationCreateRequest(BaseModel):
    revision_id: str
    profile_id: str
    weights: Dict[str, float] | None = None
    seed: int = 20260713
    auto_run: bool = True
    evaluation_mode: Literal["structured", "full"] = "structured"


class RevisionCompareRequest(BaseModel):
    revision_ids: List[str] = Field(min_length=2, max_length=3)
