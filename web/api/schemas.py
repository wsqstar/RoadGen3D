"""Pydantic request models for the RoadGen3D API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

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


class EvaluateRequestModel(BaseModel):
    layout_path: str
    image_path: str | None = None
    rendered_views: List[RenderedViewModel] = Field(default_factory=list)
    preset_id: str | None = None
    persist_to_benchmark: bool = False
    evaluation_profile: str = "local_segment_v1"


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
