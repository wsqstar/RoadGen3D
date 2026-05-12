"""Knowledge-source API routes."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import (
    APIRouter,
    File as FastAPIFile,
    HTTPException,
    Query,
    Request,
    UploadFile,
)

from roadgen3d.json_safe import make_json_safe
from roadgen3d.knowledge.pdf_rag import PdfKnowledgeBaseBuilder
from roadgen3d.knowledge.source_registry import (
    KnowledgeSourceRecord,
    add_source,
    allocate_upload_paths,
    list_sources,
)
from web.api.schemas import KnowledgeRebuildRequestModel, KnowledgeSearchRequestModel

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.post("/rebuild")
def rebuild_knowledge(request_body: KnowledgeRebuildRequestModel, request: Request) -> Dict[str, Any]:
    service = request.app.state.design_service
    try:
        return make_json_safe(service.rebuild_knowledge(
            pdf_path=request_body.pdf_path,
            artifact_dir=request_body.artifact_dir,
        ))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/sources")
def list_knowledge_sources(request: Request) -> Dict[str, Any]:
    service = request.app.state.design_service
    built_ins = service.list_knowledge_sources()
    customs = [s.to_dict() for s in list_sources()]
    return make_json_safe({"items": built_ins + customs})


@router.post("/upload")
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


@router.post("/search")
def search_knowledge(request_body: KnowledgeSearchRequestModel, request: Request) -> Dict[str, Any]:
    service = request.app.state.design_service
    try:
        items = service.search_knowledge(
            query=request_body.query,
            topk=int(request_body.topk),
            knowledge_source=request_body.knowledge_source,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return make_json_safe({
        "knowledge_source": request_body.knowledge_source,
        "items": [item.to_dict() for item in items],
    })
