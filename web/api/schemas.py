"""Pydantic request models for the RoadGen3D API."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from roadgen3d.presets import SCENE_PRESETS


class ChatMessageModel(BaseModel):
    role: str
    content: str


class DraftRequestModel(BaseModel):
    messages: List[ChatMessageModel] = Field(default_factory=list)
    user_input: str
    current_patch: Dict[str, Any] = Field(default_factory=dict)
    topk: int = 6
    knowledge_source: str = "graph_rag"
    force: bool = False


class GenerateRequestModel(BaseModel):
    draft: Dict[str, Any]
    scene_context: Dict[str, Any] = Field(default_factory=dict)
    patch_overrides: Dict[str, Any] = Field(default_factory=dict)
    generation_options: Dict[str, Any] = Field(default_factory=dict)


class SceneJobCreateRequestModel(BaseModel):
    draft: Dict[str, Any]
    scene_context: Dict[str, Any] = Field(default_factory=dict)
    patch_overrides: Dict[str, Any] = Field(default_factory=dict)
    generation_options: Dict[str, Any] = Field(default_factory=dict)


class DesignMatrixInventoryRequestModel(BaseModel):
    graph_template_id: str = "hkust_gz_gate"
    custom_structure: Optional[Dict[str, Any]] = None
    custom_furniture: Optional[Dict[str, Any]] = None
    source_layout_path: Optional[str] = None
    recent_limit: int = Field(default=500, ge=1, le=2000)


class DesignMatrixGenerateRequestModel(DesignMatrixInventoryRequestModel):
    structure_key: str
    furniture_key: str
    force: bool = False


class ScenarioDesignRunCreateRequestModel(BaseModel):
    scenario_ids: List[str] = Field(default_factory=list)
    samples_per_scenario: int = Field(default=3, ge=1, le=10)
    base_seed: int = 20260506
    graph_template_id: str = "hkust_gz_gate"
    generation_options: Dict[str, Any] = Field(default_factory=dict)


class ScenarioDesignDraftVariantRequestModel(BaseModel):
    prompt: str = ""
    graph_template_id: str = "hkust_gz_gate"
    base_scenario_id: Optional[str] = None
    semantic_payload: Optional[Dict[str, Any]] = None
    use_llm: bool = True


class KnowledgeRebuildRequestModel(BaseModel):
    pdf_path: Optional[str] = None
    artifact_dir: Optional[str] = None


class KnowledgeSearchRequestModel(BaseModel):
    query: str
    topk: int = 6
    knowledge_source: str = "graph_rag"


class ReferenceAnnotationConvertRequestModel(BaseModel):
    annotation: Dict[str, Any]
    compose_config: Dict[str, Any] = Field(default_factory=dict)


class SceneSourceNormalizeRequestModel(BaseModel):
    source: Dict[str, Any]
    compose_config: Dict[str, Any] = Field(default_factory=dict)


class SceneSourceExtractRequestModel(BaseModel):
    source_id: str = Field(default="ai-source", min_length=1, max_length=96)
    image_data_url: str = Field(..., min_length=32, max_length=28_000_000)
    prompt: str = Field(default="", max_length=4_000)
    image: Dict[str, Any]
    compose_config: Dict[str, Any] = Field(default_factory=dict)


class OsmBuildingSourceRequestModel(BaseModel):
    source_id: str = Field(default="osm-buildings", min_length=1, max_length=96)
    aoi_bbox: List[float] = Field(..., min_length=4, max_length=4)


class OsmSceneSourceRequestModel(BaseModel):
    source_id: str = Field(default="osm-scene", min_length=1, max_length=96)
    aoi_bbox: List[float] = Field(..., min_length=4, max_length=4)
    force_refetch: bool = False


class OsmRoadStudySelectionRequestModel(BaseModel):
    seed_logical_road_id: str = Field(..., min_length=1, max_length=160)
    hop_count: Literal[1, 2] = 1
    context_buffer_m: float = Field(default=100.0, ge=25.0, le=300.0)
    source_id: Optional[str] = Field(default=None, min_length=1, max_length=96)


class ReferenceAnnotationDeriveRegionsRequestModel(BaseModel):
    annotation: Dict[str, Any]
    options: Dict[str, Any] = Field(default_factory=dict)


class TemplatePatchPreviewRequestModel(BaseModel):
    patch: Dict[str, Any]
    compose_config: Dict[str, Any] = Field(default_factory=dict)
    include_graph_payload: bool = True


class OsmSemanticPreviewRequestModel(BaseModel):
    aoi_bbox: List[float] = Field(..., min_length=4, max_length=4)
    osm_cache_dir: Optional[str] = None
    compose_config: Dict[str, Any] = Field(default_factory=dict)


class RenderedViewModel(BaseModel):
    view_id: str
    label: str
    image_data_url: str
    kind: str | None = None
    camera: List[float] | None = None
    target: List[float] | None = None
    priority: int | None = None
    width: int | None = None
    height: int | None = None
    source: str | None = None
    projection: str | None = None
    horizontal_fov_deg: float | None = None
    vertical_fov_deg: float | None = None
    content_origin: str | None = None


class EvaluationAggregationConfigModel(BaseModel):
    dimension_weights: Dict[str, float] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class EvaluationWalkabilityConfigModel(BaseModel):
    clear_width_min: float | None = None
    clear_width_ideal: float | None = None
    amenity_density_ideal: float | None = None
    amenity_count_density_ideal: float | None = None
    lamp_spacing_m: float | None = None
    transit_stop_spacing_m: float | None = None
    crossing_spacing_m: float | None = None
    entrance_density_ideal: float | None = None
    tree_shade_grid_resolution_m: float | None = None
    tree_sun_azimuth_deg: float | None = None
    tree_sun_elevation_deg: float | None = None
    tree_canopy_center_height_ratio: float | None = None
    tree_canopy_vertical_ratio: float | None = None

    model_config = ConfigDict(extra="forbid")


class EvaluationConfigModel(BaseModel):
    aggregation: EvaluationAggregationConfigModel | None = None
    walkability: EvaluationWalkabilityConfigModel | None = None

    model_config = ConfigDict(extra="forbid")


class EvaluateRequestModel(BaseModel):
    layout_path: str
    image_path: str | None = None
    rendered_views: List[RenderedViewModel] = Field(default_factory=list)
    preset_id: str | None = None
    persist_to_benchmark: bool = False
    evaluation_profile: str = "auto"
    evaluation_config: EvaluationConfigModel | None = None


class EvaluateCompareRequestModel(BaseModel):
    current_layout_path: str
    current_image_path: str | None = None
    previous_layout_path: str | None = None
    previous_image_path: str | None = None
    previous_score: float | None = None
    previous_evaluation: str | None = None


class ImproveRequestModel(BaseModel):
    current_evaluation: str
    comparison: Dict[str, Any] | None = None
    current_patch: Dict[str, Any] | None = None
    weakness_queries: List[str] | None = None


class SceneDiffRequestModel(BaseModel):
    layout_a: str
    layout_b: str


class BranchRunCreateRequestModel(BaseModel):
    prompt: str
    topk: int = 3
    rounds: int = 2
    target_samples: Optional[int] = Field(default=None, ge=1, le=100)
    search_mode: str = "llm_branch"
    early_stop_patience: Optional[int] = Field(default=None, ge=1, le=100)
    retain_topk_artifacts: Optional[int] = Field(default=None, ge=1, le=20)
    score_with_rendered_views: bool = False
    graph_template_id: str = "hkust_gz_gate"
    knowledge_source: str = "graph_rag"
    scene_context: Dict[str, Any] = Field(default_factory=dict)
    generation_options: Dict[str, Any] = Field(default_factory=dict)
    preset_id: str = ""
    preset_config_patch: Dict[str, Any] = Field(default_factory=dict)
    benchmark_id: str = ""
    batch_id: str = ""
    persist_to_benchmark: bool = False
    evaluation_weights: Dict[str, float] = Field(default_factory=lambda: {
        "walkability": 0.4,
        "safety": 0.3,
        "beauty": 0.3,
    })


class BenchmarkBatchCreateRequestModel(BaseModel):
    preset_ids: List[str] = Field(default_factory=lambda: [str(item.get("id")) for item in SCENE_PRESETS])
    target_samples: int = Field(default=100, ge=1, le=100)
    graph_template_id: str = "hkust_gz_gate"
    knowledge_source: str = "graph_rag"
    early_stop_patience: int = Field(default=20, ge=1, le=100)
    retain_topk_artifacts: int = Field(default=10, ge=1, le=20)
    score_with_rendered_views: bool = True


class SceneEditBaseModel(BaseModel):
    revision: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class SceneMoveInstanceCommandModel(BaseModel):
    command_id: str = Field(min_length=1, max_length=128)
    op: Literal["move_instance"]
    instance_id: str = Field(min_length=1, max_length=256)
    position_xyz: List[float] = Field(min_length=3, max_length=3)


class SceneLayoutEditRequestModel(BaseModel):
    layout_path: str = Field(min_length=1)
    base: SceneEditBaseModel
    commands: List[Dict[str, Any]] = Field(min_length=1, max_length=100)
    transform_policy: Literal["expert_grounded", "course_grounded"] = "expert_grounded"


class RebuildLayoutGlbRequestModel(BaseModel):
    layout_path: str
    manifest_path: Optional[str] = None
    force: bool = False


class CaptureViewsRequestModel(BaseModel):
    layout_path: str
    scene_glb_path: Optional[str] = None
    manifest_path: Optional[str] = None
    capture_3d_views: bool = True
    capture_profile: str = "review_expanded"
    capture_resolution: List[int] = Field(default_factory=lambda: [1280, 720])
    capture_failure_policy: str = "warn"
    retain_glb_policy: str = "top_k"
    viewer_url: str = ""


class AssetManifestSplitRequestModel(BaseModel):
    manifest_name: str = Field(..., min_length=1)
    asset_id: str = Field(..., min_length=1)
    method: str = "auto"
    projection_margin: float = Field(default=0.03, ge=0.0)
