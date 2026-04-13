"""Canonical FastAPI entrypoint for the LLM + RAG workbench."""

from __future__ import annotations

import sys
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
from roadgen3d.graph_templates import get_graph_template, list_graph_templates  # noqa: E402
from roadgen3d.metaurban_procedural import get_metaurban_reference_plan, list_metaurban_reference_plans  # noqa: E402
from roadgen3d.reference_annotation import (  # noqa: E402
    build_reference_annotation_compose_config,
    build_reference_annotation_graph_payload,
)
from roadgen3d.llm.design_workflow import DesignAssistantService, parse_design_draft  # noqa: E402
from roadgen3d.services.design_types import sanitize_scene_context  # noqa: E402
from roadgen3d.knowledge.source_registry import (  # noqa: E402
    add_source,
    allocate_upload_paths,
    list_sources,
)
from roadgen3d.knowledge.pdf_rag import PdfKnowledgeBaseBuilder  # noqa: E402


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


class EvaluateRequestModel(BaseModel):
    layout_path: str
    image_path: str | None = None


def create_app(*, design_service: DesignAssistantService | Any | None = None) -> FastAPI:
    app = FastAPI(title="RoadGen3D Design Assistant API", version="0.2.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.design_service = design_service or DesignAssistantService()

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

    @app.get("/api/graph-templates/{template_id}/image")
    def get_graph_template_image(template_id: str) -> FileResponse:
        try:
            template = get_graph_template(template_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not template.image_path.exists():
            raise HTTPException(status_code=404, detail=f"Graph template image not found: {template.image_path}")
        return FileResponse(template.image_path)

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

    @app.get("/api/scenes/recent")
    def list_recent_scenes(limit: int = Query(default=12, ge=1, le=100)) -> Dict[str, Any]:
        service = app.state.design_service
        items = service.list_recent_scenes(limit=int(limit))
        return make_json_safe({"items": [item.to_dict() for item in items]})

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
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return make_json_safe(result)

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
