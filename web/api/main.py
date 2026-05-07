"""Canonical FastAPI entrypoint for the LLM + RAG design API."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, UploadFile, File as FastAPIFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.json_safe import make_json_safe  # noqa: E402
from roadgen3d.llm import LLMConfigurationError, LLMResponseError  # noqa: E402
from roadgen3d.graph_templates import (  # noqa: E402
    get_graph_template,
    list_graph_templates,
    load_graph_template_annotation_payload,
)
from roadgen3d.presets import SCENE_PRESETS  # noqa: E402
from roadgen3d.metaurban_procedural import get_metaurban_reference_plan, list_metaurban_reference_plans  # noqa: E402
from roadgen3d.api.junction_templates import router as junction_templates_router  # noqa: E402
from roadgen3d.reference_annotation import (  # noqa: E402
    build_reference_annotation_compose_config,
    build_reference_annotation_graph_payload,
)
from roadgen3d.reference_regions import derive_regions_from_annotation  # noqa: E402
from roadgen3d.template_patch import TemplatePatchError, apply_template_patch  # noqa: E402
from roadgen3d.llm.design_workflow import DesignAssistantService, parse_design_draft  # noqa: E402
from roadgen3d.services.branch_benchmarks import BranchBenchmarkBatchService, BranchBenchmarkStore  # noqa: E402
from roadgen3d.services.branch_runs import BranchRunService  # noqa: E402
from roadgen3d.services.design_types import sanitize_scene_context  # noqa: E402
from roadgen3d.services.scenario_designs import ScenarioDesignService  # noqa: E402
from roadgen3d.knowledge.source_registry import (  # noqa: E402
    add_source,
    allocate_upload_paths,
    list_sources,
)
from roadgen3d.knowledge.pdf_rag import PdfKnowledgeBaseBuilder  # noqa: E402
from roadgen3d.diff_engine import compute_scene_diff  # noqa: E402
from roadgen3d.diff_render import render_diff_overlay, render_delta_map  # noqa: E402
from roadgen3d.capture_3d import capture_views_for_layout  # noqa: E402
from roadgen3d.street_layout import rebuild_glb_from_layout  # noqa: E402


class ChatMessageModel(BaseModel):
    role: str
    content: str


class DraftRequestModel(BaseModel):
    messages: List[ChatMessageModel] = Field(default_factory=list)
    user_input: str
    current_patch: Dict[str, Any] = Field(default_factory=dict)
    topk: int = 6
    knowledge_source: str = "graph_rag"
    force: bool = False  # Skip clarification and force draft generation with AI-filled defaults


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


class ScenarioDesignRunCreateRequestModel(BaseModel):
    scenario_ids: List[str] = Field(default_factory=list)
    samples_per_scenario: int = Field(default=3, ge=1, le=10)
    base_seed: int = 20260506
    graph_template_id: str = "hkust_gz_gate"
    generation_options: Dict[str, Any] = Field(default_factory=dict)


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


def _resolve_layout_referenced_path(value: str, layout_path: Path) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = layout_path.parent / candidate
    return candidate.resolve()


def create_app(
    *,
    design_service: DesignAssistantService | Any | None = None,
    benchmark_store: BranchBenchmarkStore | None = None,
) -> FastAPI:
    app = FastAPI(title="RoadGen3D Design Assistant API", version="0.2.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.design_service = design_service or DesignAssistantService()
    app.state.benchmark_store = benchmark_store or BranchBenchmarkStore()
    app.state.branch_run_service = BranchRunService(
        design_service=app.state.design_service,
        benchmark_store=app.state.benchmark_store,
    )
    app.state.scenario_design_service = ScenarioDesignService(
        design_service=app.state.design_service,
    )
    app.state.benchmark_batch_service = BranchBenchmarkBatchService(
        branch_run_service=app.state.branch_run_service,
        benchmark_store=app.state.benchmark_store,
    )

    @app.get("/")
    def root() -> Dict[str, Any]:
        return make_json_safe({
            "ok": True,
            "service": "roadgen3d-design-assistant-api",
            "message": "RoadGen3D API is running. Open the Viewer at http://127.0.0.1:4173/.",
            "health_url": "/api/health",
            "docs_url": "/docs",
            "viewer_url": "http://127.0.0.1:4173/",
        })

    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        service = app.state.design_service
        return make_json_safe({
            "ok": True,
            "default_pdf_path": str(service.default_pdf_path),
            "default_artifact_dir": str(service.default_artifact_dir),
        })

    @app.get("/api/geo/china-cities")
    def list_china_cities() -> Dict[str, Any]:
        service = app.state.design_service
        return make_json_safe({"items": service.list_china_cities()})

    @app.get("/api/reference-plans")
    def list_reference_plans() -> Dict[str, Any]:
        items = []
        for plan in list_metaurban_reference_plans():
            payload = plan.to_dict()
            payload["image_url"] = f"/api/reference-plans/{plan.plan_id}/image"
            items.append(payload)
        return make_json_safe({"items": items})

    @app.get("/api/reference-plans/{plan_id}/image")
    def get_reference_plan_image(plan_id: str) -> FileResponse:
        try:
            plan = get_metaurban_reference_plan(plan_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not plan.image_path.exists():
            raise HTTPException(status_code=404, detail=f"Reference plan image not found: {plan.image_path}")
        return FileResponse(plan.image_path)

    @app.get("/api/graph-templates")
    def list_graph_template_items() -> Dict[str, Any]:
        items = []
        for template in list_graph_templates():
            payload = template.to_dict()
            payload["image_url"] = f"/api/graph-templates/{template.template_id}/image"
            items.append(payload)
        return make_json_safe({"items": items})

    @app.get("/api/presets")
    def list_presets() -> Dict[str, Any]:
        """Return all scene presets for frontend consumption."""
        return make_json_safe({"items": SCENE_PRESETS})

    @app.get("/api/graph-templates/{template_id}/image")
    def get_graph_template_image(template_id: str) -> FileResponse:
        try:
            template = get_graph_template(template_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not template.image_path.exists():
            raise HTTPException(status_code=404, detail=f"Graph template image not found: {template.image_path}")
        return FileResponse(template.image_path)

    @app.post("/api/graph-templates/{template_id}/template-patch/preview")
    def preview_graph_template_patch(template_id: str, request: TemplatePatchPreviewRequestModel) -> Dict[str, Any]:
        try:
            base_annotation = load_graph_template_annotation_payload(template_id)
            application = apply_template_patch(base_annotation, request.patch)
            payload: Dict[str, Any] = {
                "annotation": application.annotation,
                "summary": application.summary,
            }
            if request.include_graph_payload:
                compose_config = build_reference_annotation_compose_config(request.compose_config)
                payload["graph_payload"] = build_reference_annotation_graph_payload(
                    application.annotation,
                    config=compose_config,
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (TemplatePatchError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return make_json_safe(payload)

    @app.post("/api/reference-annotations/convert")
    def convert_reference_annotation(request: ReferenceAnnotationConvertRequestModel) -> Dict[str, Any]:
        try:
            compose_config = build_reference_annotation_compose_config(request.compose_config)
            payload = build_reference_annotation_graph_payload(
                request.annotation,
                config=compose_config,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return make_json_safe(payload)

    @app.post("/api/reference-annotations/derive-regions")
    def derive_reference_annotation_regions(request: ReferenceAnnotationDeriveRegionsRequestModel) -> Dict[str, Any]:
        try:
            payload = derive_regions_from_annotation(
                request.annotation,
                options=request.options,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return make_json_safe(payload)

    @app.post("/api/design/draft")
    def design_draft(request: DraftRequestModel) -> Dict[str, Any]:
        service = app.state.design_service
        try:
            result = service.draft_design(
                messages=[_dump_model(item) for item in request.messages],
                user_input=request.user_input,
                current_patch=request.current_patch,
                topk=int(request.topk),
                knowledge_source=request.knowledge_source,
                force=request.force,
            )
        except (LLMConfigurationError, LLMResponseError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return make_json_safe(result.to_dict())

    @app.post("/api/design/generate")
    def design_generate(request: GenerateRequestModel) -> Dict[str, Any]:
        service = app.state.design_service
        draft = _parse_draft_payload(request.draft)
        try:
            result = service.generate_scene(
                draft=draft,
                scene_context=sanitize_scene_context(request.scene_context),
                patch_overrides=request.patch_overrides,
                generation_options=request.generation_options,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return make_json_safe(result)

    @app.post("/api/scene/jobs")
    def create_scene_job(request: SceneJobCreateRequestModel) -> Dict[str, Any]:
        service = app.state.design_service
        draft = _parse_draft_payload(request.draft)
        try:
            result = service.create_scene_job(
                draft=draft,
                scene_context=sanitize_scene_context(request.scene_context),
                patch_overrides=request.patch_overrides,
                generation_options=request.generation_options,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return make_json_safe(result.to_dict())

    @app.get("/api/scene/jobs")
    def list_scene_jobs(limit: int = Query(default=20, ge=1, le=100)) -> Dict[str, Any]:
        service = app.state.design_service
        jobs = service.list_scene_jobs(limit=int(limit))
        return make_json_safe({"items": [item.to_dict() for item in jobs]})

    @app.get("/api/scene/jobs/{job_id}")
    def get_scene_job(job_id: str) -> Dict[str, Any]:
        service = app.state.design_service
        result = service.get_scene_job(job_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Scene job not found: {job_id}")
        return make_json_safe(result.to_dict())

    @app.get("/api/scenario-designs")
    def list_scenario_designs() -> Dict[str, Any]:
        try:
            return make_json_safe(app.state.scenario_design_service.list_scenarios())
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/scenario-designs/{scenario_id}/reference-annotation")
    def get_scenario_design_reference_annotation(
        scenario_id: str,
        graph_template_id: str = Query(default="hkust_gz_gate"),
    ) -> Dict[str, Any]:
        try:
            payload = app.state.scenario_design_service.reference_annotation_for_scenario(
                scenario_id,
                graph_template_id=graph_template_id,
            )
            return make_json_safe(payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/scenario-designs/runs")
    def create_scenario_design_run(request: ScenarioDesignRunCreateRequestModel) -> Dict[str, Any]:
        try:
            return make_json_safe(app.state.scenario_design_service.submit_run(
                scenario_ids=request.scenario_ids,
                samples_per_scenario=request.samples_per_scenario,
                base_seed=request.base_seed,
                graph_template_id=request.graph_template_id,
                generation_options=request.generation_options,
            ))
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/scenario-designs/runs/{run_id}")
    def get_scenario_design_run(run_id: str) -> Dict[str, Any]:
        result = app.state.scenario_design_service.get_run(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Scenario design run not found: {run_id}")
        return make_json_safe(result)

    @app.get("/api/scenario-designs/runs/{run_id}/report")
    def get_scenario_design_run_report(run_id: str) -> Dict[str, Any]:
        result = app.state.scenario_design_service.get_report(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Scenario design run not found: {run_id}")
        return make_json_safe(result)

    @app.get("/api/scenes/recent")
    def list_recent_scenes(limit: int = Query(default=12, ge=1, le=100)) -> Dict[str, Any]:
        service = app.state.design_service
        items = service.list_recent_scenes(limit=int(limit))
        return make_json_safe({"items": [item.to_dict() for item in items]})

    @app.post("/api/design/branch-runs")
    def create_branch_run(request: BranchRunCreateRequestModel) -> Dict[str, Any]:
        service = app.state.branch_run_service
        try:
            return make_json_safe(service.submit_run(
                prompt=request.prompt,
                topk=request.topk,
                rounds=request.rounds,
                graph_template_id=request.graph_template_id,
                knowledge_source=request.knowledge_source,
                scene_context=request.scene_context,
                generation_options=request.generation_options,
                evaluation_weights=request.evaluation_weights,
                preset_id=request.preset_id or str(request.generation_options.get("preset_id") or ""),
                preset_config_patch=request.preset_config_patch,
                benchmark_id=request.benchmark_id,
                batch_id=request.batch_id,
                persist_to_benchmark=request.persist_to_benchmark,
                target_samples=request.target_samples,
                search_mode=request.search_mode,
                early_stop_patience=request.early_stop_patience,
                retain_topk_artifacts=request.retain_topk_artifacts,
                score_with_rendered_views=request.score_with_rendered_views,
            ))
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/design/branch-runs")
    def list_branch_runs(limit: int = Query(default=20, ge=1, le=100)) -> Dict[str, Any]:
        service = app.state.branch_run_service
        return make_json_safe({"items": service.list_runs(limit=int(limit))})

    @app.get("/api/design/branch-runs/{run_id}")
    def get_branch_run(run_id: str) -> Dict[str, Any]:
        service = app.state.branch_run_service
        result = service.get_run(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Branch run not found: {run_id}")
        return make_json_safe(result)

    @app.get("/api/design/benchmark-samples")
    def list_benchmark_samples(
        preset_id: str | None = Query(default=None),
        batch_id: str | None = Query(default=None),
        run_id: str | None = Query(default=None),
        limit: int = Query(default=5000, ge=1, le=10000),
        refresh: bool = Query(default=True),
    ) -> Dict[str, Any]:
        store = app.state.benchmark_store
        if refresh:
            store.import_branch_manifests()
        return make_json_safe(store.query_samples(
            preset_id=preset_id,
            batch_id=batch_id,
            run_id=run_id,
            limit=int(limit),
        ))

    @app.get("/api/design/benchmark-analysis")
    def benchmark_analysis(
        preset_id: str | None = Query(default=None),
        batch_id: str | None = Query(default=None),
        run_id: str | None = Query(default=None),
        limit: int = Query(default=5000, ge=1, le=10000),
        refresh: bool = Query(default=True),
    ) -> Dict[str, Any]:
        store = app.state.benchmark_store
        if refresh:
            store.import_branch_manifests()
        return make_json_safe(store.query_analysis(
            preset_id=preset_id,
            batch_id=batch_id,
            run_id=run_id,
            limit=int(limit),
        ))

    @app.post("/api/design/benchmark-batches")
    def create_benchmark_batch(request: BenchmarkBatchCreateRequestModel) -> Dict[str, Any]:
        service = app.state.benchmark_batch_service
        try:
            return make_json_safe(service.submit_batch(
                preset_ids=request.preset_ids,
                target_samples=request.target_samples,
                graph_template_id=request.graph_template_id,
                knowledge_source=request.knowledge_source,
                early_stop_patience=request.early_stop_patience,
                retain_topk_artifacts=request.retain_topk_artifacts,
                score_with_rendered_views=request.score_with_rendered_views,
            ))
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/design/benchmark-batches/{batch_id}")
    def get_benchmark_batch(batch_id: str) -> Dict[str, Any]:
        result = app.state.benchmark_batch_service.get_batch(batch_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Benchmark batch not found: {batch_id}")
        return make_json_safe(result)

    @app.post("/api/design/rebuild-layout-glb")
    def rebuild_layout_glb(request: RebuildLayoutGlbRequestModel) -> Dict[str, Any]:
        raw_layout_path = request.layout_path.strip()
        if not raw_layout_path:
            raise HTTPException(status_code=400, detail="layout_path is required")
        layout_path = Path(raw_layout_path).expanduser().resolve()
        if not layout_path.exists() or not layout_path.is_file():
            raise HTTPException(status_code=404, detail=f"Layout file not found: {layout_path}")

        manifest_path = (
            Path(request.manifest_path).expanduser().resolve()
            if request.manifest_path
            else (ROOT / "data" / "real" / "real_assets_manifest.jsonl").resolve()
        )
        if not manifest_path.exists() or not manifest_path.is_file():
            raise HTTPException(status_code=404, detail=f"Asset manifest not found: {manifest_path}")

        try:
            payload = json.loads(layout_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid scene_layout.json: {exc}") from exc

        outputs = dict(payload.get("outputs", {}) or {})
        existing_glb_path = _resolve_layout_referenced_path(str(outputs.get("scene_glb", "") or ""), layout_path)
        if existing_glb_path is not None and existing_glb_path.exists() and not request.force:
            return make_json_safe({
                "layout_path": str(layout_path),
                "scene_glb_path": str(existing_glb_path),
                "manifest_path": str(manifest_path),
                "rebuilt": False,
            })

        try:
            rebuild_outputs = rebuild_glb_from_layout(
                layout_path=layout_path,
                manifest_path=manifest_path,
                out_dir=layout_path.parent / "rebuild",
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to rebuild GLB from layout: {exc}") from exc

        scene_glb_path = Path(str(rebuild_outputs.get("scene_glb", "") or "")).expanduser().resolve()
        if not scene_glb_path.exists():
            raise HTTPException(status_code=500, detail="GLB rebuild did not create scene_glb output")

        payload = json.loads(layout_path.read_text(encoding="utf-8"))
        outputs = dict(payload.get("outputs", {}) or {})
        outputs["scene_glb"] = str(scene_glb_path)
        outputs["scene_layout"] = str(layout_path)
        payload["outputs"] = outputs
        summary = dict(payload.get("summary", {}) or {})
        summary["scene_glb_rebuilt_from_layout"] = True
        summary["scene_glb_rebuilt_at"] = datetime.now(timezone.utc).isoformat()
        summary["scene_glb_rebuild_manifest_path"] = str(manifest_path)
        payload["summary"] = summary
        layout_path.write_text(
            json.dumps(make_json_safe(payload), ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

        return make_json_safe({
            "layout_path": str(layout_path),
            "scene_glb_path": str(scene_glb_path),
            "manifest_path": str(manifest_path),
            "rebuilt": True,
        })

    @app.post("/api/design/capture-views")
    def capture_design_views(request: CaptureViewsRequestModel) -> Dict[str, Any]:
        raw_layout_path = request.layout_path.strip()
        if not raw_layout_path:
            raise HTTPException(status_code=400, detail="layout_path is required")
        layout_path = Path(raw_layout_path).expanduser().resolve()
        if not layout_path.exists() or not layout_path.is_file():
            raise HTTPException(status_code=404, detail=f"Layout file not found: {layout_path}")

        manifest_path = (
            Path(request.manifest_path).expanduser().resolve()
            if request.manifest_path
            else (ROOT / "data" / "real" / "real_assets_manifest.jsonl").resolve()
        )
        if not manifest_path.exists() or not manifest_path.is_file():
            raise HTTPException(status_code=404, detail=f"Asset manifest not found: {manifest_path}")

        try:
            capture_result = capture_views_for_layout(
                layout_path=layout_path,
                scene_glb_path=request.scene_glb_path,
                manifest_path=manifest_path,
                options={
                    "capture_3d_views": request.capture_3d_views,
                    "capture_profile": request.capture_profile,
                    "capture_resolution": request.capture_resolution,
                    "capture_failure_policy": request.capture_failure_policy,
                    "retain_glb_policy": request.retain_glb_policy,
                    "viewer_url": request.viewer_url,
                },
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to capture 3D views: {exc}") from exc

        return make_json_safe(capture_result.to_dict())

    @app.post("/api/scenes/diff")
    def scene_diff(request: SceneDiffRequestModel) -> Dict[str, Any]:
        layout_a = Path(request.layout_a).expanduser().resolve()
        layout_b = Path(request.layout_b).expanduser().resolve()
        if not layout_a.exists() or not layout_b.exists():
            raise HTTPException(status_code=404, detail="One or both layout files not found.")
        try:
            payload_a = json.loads(layout_a.read_text(encoding="utf-8"))
            payload_b = json.loads(layout_b.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to parse layout: {exc}") from exc
        return make_json_safe(compute_scene_diff(payload_a, payload_b))

    @app.get("/api/scenes/diff/image")
    def scene_diff_image(
        layout_a: str = Query(...),
        layout_b: str = Query(...),
        mode: str = Query(default="overlay"),
    ) -> FileResponse:
        layout_a_path = Path(layout_a).expanduser().resolve()
        layout_b_path = Path(layout_b).expanduser().resolve()
        if not layout_a_path.exists() or not layout_b_path.exists():
            raise HTTPException(status_code=404, detail="One or both layout files not found.")
        if mode not in ("overlay", "delta"):
            raise HTTPException(status_code=400, detail="Invalid mode. Use overlay or delta.")

        stat_a = layout_a_path.stat()
        stat_b = layout_b_path.stat()
        cache_key = sha256(
            f"{layout_a_path}:{stat_a.st_mtime}:{stat_a.st_size}|"
            f"{layout_b_path}:{stat_b.st_mtime}:{stat_b.st_size}|"
            f"{mode}".encode("utf-8")
        ).hexdigest()[:16]
        cache_dir = ROOT / "artifacts" / "diff_images"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{cache_key}_{mode}.png"

        if cache_path.exists():
            return FileResponse(cache_path, media_type="image/png")

        try:
            if mode == "overlay":
                render_diff_overlay(layout_a_path, layout_b_path, cache_path)
            else:
                render_delta_map(layout_a_path, layout_b_path, cache_path)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Diff rendering failed: {exc}") from exc

        if not cache_path.exists():
            raise HTTPException(status_code=500, detail="Diff rendering produced no output.")
        return FileResponse(cache_path, media_type="image/png")

    @app.post("/api/knowledge/rebuild")
    def rebuild_knowledge(request: KnowledgeRebuildRequestModel) -> Dict[str, Any]:
        service = app.state.design_service
        try:
            return make_json_safe(service.rebuild_knowledge(
                pdf_path=request.pdf_path,
                artifact_dir=request.artifact_dir,
            ))
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/knowledge/sources")
    def list_knowledge_sources() -> Dict[str, Any]:
        service = app.state.design_service
        built_ins = service.list_knowledge_sources()
        customs = [s.to_dict() for s in list_sources()]
        return make_json_safe({"items": built_ins + customs})

    @app.post("/api/knowledge/upload")
    def upload_knowledge(
        label: str = Query(..., min_length=1, max_length=200),
        file: UploadFile = FastAPIFile(...),
    ) -> Dict[str, Any]:
        if not str(file.content_type or "").lower().endswith(("pdf", "octet-stream")):
            raise HTTPException(status_code=400, detail="Only PDF files are supported.")
        source_id, pdf_path, artifact_dir = allocate_upload_paths(label)
        try:
            pdf_path.write_bytes(file.file.read())
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to save uploaded file: {exc}") from exc
        try:
            builder = PdfKnowledgeBaseBuilder()
            builder.build(pdf_path, artifact_dir)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to build knowledge base: {exc}") from exc
        from roadgen3d.knowledge.source_registry import KnowledgeSourceRecord
        record = add_source(
            KnowledgeSourceRecord(
                source_id=source_id,
                label=label,
                source_type="pdf_rag",
                pdf_path=str(pdf_path),
                artifact_dir=str(artifact_dir),
            )
        )
        return make_json_safe({"source_id": record.source_id, "label": record.label, "type": record.source_type})

    @app.post("/api/knowledge/search")
    def search_knowledge(request: KnowledgeSearchRequestModel) -> Dict[str, Any]:
        service = app.state.design_service
        try:
            items = service.search_knowledge(
                query=request.query,
                topk=int(request.topk),
                knowledge_source=request.knowledge_source,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return make_json_safe({
            "knowledge_source": request.knowledge_source,
            "items": [item.to_dict() for item in items],
        })

    @app.post("/api/design/evaluate")
    def evaluate_scene(request: EvaluateRequestModel) -> Dict[str, Any]:
        service = app.state.design_service
        try:
            result = service.evaluate_scene(
                layout_path=request.layout_path,
                image_path=request.image_path,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return make_json_safe(result)

    @app.post("/api/design/evaluate/unified")
    def evaluate_scene_unified(request: EvaluateRequestModel) -> Dict[str, Any]:
        """Unified evaluation endpoint returning walkability/safety/beauty scores."""
        service = app.state.design_service
        try:
            result = service.evaluate_scene_unified(
                layout_path=request.layout_path,
                image_path=request.image_path,
                rendered_views=[
                    view.model_dump(exclude_none=True) if hasattr(view, "model_dump") else view.dict(exclude_none=True)
                    for view in request.rendered_views
                ],
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if request.persist_to_benchmark:
            app.state.benchmark_store.upsert_evaluation(
                layout_path=request.layout_path,
                evaluation=result,
                preset_id=request.preset_id or _infer_layout_preset_id(request.layout_path),
            )
        return make_json_safe(result)

    @app.post("/api/design/evaluate/compare")
    def evaluate_scene_compare(request: EvaluateCompareRequestModel) -> Dict[str, Any]:
        """Evaluate scene with history comparison."""
        service = app.state.design_service
        try:
            result = service.evaluate_scene_with_history(
                layout_path=request.current_layout_path,
                image_path=request.current_image_path,
                previous_layout_path=request.previous_layout_path,
                previous_image_path=request.previous_image_path,
                previous_score=request.previous_score or 0.0,
                previous_evaluation=request.previous_evaluation or "",
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return make_json_safe(result)

    @app.post("/api/design/improve")
    def propose_improvement(request: ImproveRequestModel) -> Dict[str, Any]:
        """Propose improvement based on evaluation and RAG evidence."""
        service = app.state.design_service
        try:
            result = service.propose_improvement(
                current_evaluation=request.current_evaluation,
                comparison=request.comparison or {},
                current_patch=request.current_patch or {},
                weakness_queries=request.weakness_queries or [],
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return make_json_safe(result)

    app.include_router(junction_templates_router)

    return app


app = create_app()


def _dump_model(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _parse_draft_payload(payload: Dict[str, Any]) -> Any:
    return parse_design_draft(
        payload,
        evidence=(),
        fallback_query=str(payload.get("normalized_scene_query", "") or ""),
        current_patch=payload.get("compose_config_patch", {}) or {},
    )


def _infer_layout_preset_id(layout_path: str) -> str:
    try:
        payload = json.loads(Path(layout_path).expanduser().read_text(encoding="utf-8"))
    except Exception:
        return "custom"
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    config = payload.get("config", {}) if isinstance(payload, dict) else {}
    for source in (summary, config):
        if isinstance(source, dict):
            preset_id = str(source.get("preset_id") or source.get("benchmark_preset_id") or "").strip()
            if preset_id:
                return preset_id
    return "custom"
