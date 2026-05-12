"""Branch-run and benchmark API routes."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query, Request

from roadgen3d.json_safe import make_json_safe
from web.api.schemas import BranchRunCreateRequestModel, BenchmarkBatchCreateRequestModel

router = APIRouter(tags=["branch-benchmarks"])


@router.post("/api/design/branch-runs")
def create_branch_run(request_body: BranchRunCreateRequestModel, request: Request) -> Dict[str, Any]:
    service = request.app.state.branch_run_service
    try:
        return make_json_safe(service.submit_run(
            prompt=request_body.prompt,
            topk=request_body.topk,
            rounds=request_body.rounds,
            graph_template_id=request_body.graph_template_id,
            knowledge_source=request_body.knowledge_source,
            scene_context=request_body.scene_context,
            generation_options=request_body.generation_options,
            evaluation_weights=request_body.evaluation_weights,
            preset_id=request_body.preset_id or str(request_body.generation_options.get("preset_id") or ""),
            preset_config_patch=request_body.preset_config_patch,
            benchmark_id=request_body.benchmark_id,
            batch_id=request_body.batch_id,
            persist_to_benchmark=request_body.persist_to_benchmark,
            target_samples=request_body.target_samples,
            search_mode=request_body.search_mode,
            early_stop_patience=request_body.early_stop_patience,
            retain_topk_artifacts=request_body.retain_topk_artifacts,
            score_with_rendered_views=request_body.score_with_rendered_views,
        ))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/design/branch-runs")
def list_branch_runs(request: Request, limit: int = Query(default=20, ge=1, le=100)) -> Dict[str, Any]:
    service = request.app.state.branch_run_service
    return make_json_safe({"items": service.list_runs(limit=int(limit))})


@router.get("/api/design/branch-runs/{run_id}")
def get_branch_run(run_id: str, request: Request) -> Dict[str, Any]:
    service = request.app.state.branch_run_service
    result = service.get_run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Branch run not found: {run_id}")
    return make_json_safe(result)


@router.get("/api/design/benchmark-samples")
def list_benchmark_samples(
    request: Request,
    preset_id: str | None = Query(default=None),
    batch_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    generation_method: str | None = Query(default=None),
    limit: int = Query(default=5000, ge=1, le=10000),
    refresh: bool = Query(default=True),
) -> Dict[str, Any]:
    store = request.app.state.benchmark_store
    if refresh:
        store.import_branch_manifests()
    return make_json_safe(store.query_samples(
        preset_id=preset_id,
        batch_id=batch_id,
        run_id=run_id,
        generation_method=generation_method,
        limit=int(limit),
    ))


@router.get("/api/design/benchmark-analysis")
def benchmark_analysis(
    request: Request,
    preset_id: str | None = Query(default=None),
    batch_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    generation_method: str | None = Query(default=None),
    limit: int = Query(default=5000, ge=1, le=10000),
    refresh: bool = Query(default=True),
) -> Dict[str, Any]:
    store = request.app.state.benchmark_store
    if refresh:
        store.import_branch_manifests()
    return make_json_safe(store.query_analysis(
        preset_id=preset_id,
        batch_id=batch_id,
        run_id=run_id,
        generation_method=generation_method,
        limit=int(limit),
    ))


@router.post("/api/design/benchmark-batches")
def create_benchmark_batch(request_body: BenchmarkBatchCreateRequestModel, request: Request) -> Dict[str, Any]:
    service = request.app.state.benchmark_batch_service
    try:
        return make_json_safe(service.submit_batch(
            preset_ids=request_body.preset_ids,
            target_samples=request_body.target_samples,
            graph_template_id=request_body.graph_template_id,
            knowledge_source=request_body.knowledge_source,
            early_stop_patience=request_body.early_stop_patience,
            retain_topk_artifacts=request_body.retain_topk_artifacts,
            score_with_rendered_views=request_body.score_with_rendered_views,
        ))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/design/benchmark-batches/{batch_id}")
def get_benchmark_batch(batch_id: str, request: Request) -> Dict[str, Any]:
    result = request.app.state.benchmark_batch_service.get_batch(batch_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Benchmark batch not found: {batch_id}")
    return make_json_safe(result)

