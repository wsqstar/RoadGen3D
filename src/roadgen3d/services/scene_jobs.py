"""In-memory scene job queue for the web workbench."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from threading import Condition, Lock, Thread
from typing import Any, Callable, Dict, List, Mapping, Sequence
from uuid import uuid4

from ..json_safe import make_json_safe
from .design_runtime import generate_scene_from_draft
from .design_types import (
    DesignDraft,
    SceneGenerationResult,
    SceneContext,
    SceneJobCreateResponse,
    SceneJobStatusResponse,
    SceneRecord,
)
from .generation_method import infer_generation_method


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class _SceneJobState:
    job_id: str
    draft: DesignDraft
    patch_overrides: Dict[str, Any]
    generation_options: Dict[str, Any]
    scene_context: SceneContext | None = None
    status: str = "queued"
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    stage: str = "queued"
    progress: int = 5
    operations: List[Dict[str, Any]] = field(default_factory=list)
    result: SceneGenerationResult | None = None
    evaluation: Dict[str, Any] = field(default_factory=lambda: {"status": "pending"})
    trace_artifact_path: str = ""


class SceneJobService:
    """Single-process background worker for scene generation jobs."""

    def __init__(
        self,
        *,
        generator: Callable[..., SceneGenerationResult] | None = None,
        evaluator: Callable[..., Mapping[str, Any]] | None = None,
    ) -> None:
        self.generator = generator or generate_scene_from_draft
        self.evaluator = evaluator
        self._jobs: Dict[str, _SceneJobState] = {}
        self._queue: Queue[str] = Queue()
        self._lock = Lock()
        self._condition = Condition(self._lock)
        self._worker: Thread | None = None

    def submit_job(
        self,
        *,
        draft: DesignDraft,
        patch_overrides: Mapping[str, Any] | None = None,
        generation_options: Mapping[str, Any] | None = None,
        scene_context: SceneContext | None = None,
    ) -> SceneJobCreateResponse:
        self._ensure_worker()
        job_id = uuid4().hex
        state = _SceneJobState(
            job_id=job_id,
            draft=draft,
            patch_overrides=dict(patch_overrides or {}),
            generation_options=dict(generation_options or {}),
            scene_context=scene_context,
            created_at=_utc_now(),
        )
        with self._condition:
            self._jobs[job_id] = state
            self._queue.put(job_id)
            self._condition.notify_all()
        return SceneJobCreateResponse(job_id=job_id, status=state.status, created_at=state.created_at)

    def get_job(self, job_id: str) -> SceneJobStatusResponse | None:
        with self._lock:
            state = self._jobs.get(str(job_id))
            if state is None:
                return None
            return self._to_status_response(state)

    def list_jobs(self, *, limit: int = 20) -> List[SceneJobStatusResponse]:
        with self._lock:
            ordered = sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)
            return [self._to_status_response(item) for item in ordered[: max(1, int(limit))]]

    def list_recent_scenes(self, *, limit: int = 20) -> List[SceneRecord]:
        with self._lock:
            ordered = sorted(
                (item for item in self._jobs.values() if item.status == "succeeded" and item.result is not None),
                key=lambda item: item.finished_at or item.created_at,
                reverse=True,
            )
            return [self._to_scene_record(item) for item in ordered[: max(1, int(limit))]]

    def wait_for_job(self, job_id: str, *, timeout_s: float | None = None) -> SceneJobStatusResponse | None:
        with self._condition:
            if job_id not in self._jobs:
                return None
            while True:
                state = self._jobs[job_id]
                if state.status in {"succeeded", "failed"}:
                    return self._to_status_response(state)
                if timeout_s is None:
                    self._condition.wait(timeout=0.5)
                else:
                    self._condition.wait(timeout=max(float(timeout_s), 0.1))

    def run_job_sync(
        self,
        *,
        draft: DesignDraft,
        patch_overrides: Mapping[str, Any] | None = None,
        generation_options: Mapping[str, Any] | None = None,
        scene_context: SceneContext | None = None,
    ) -> SceneGenerationResult:
        created = self.submit_job(
            draft=draft,
            patch_overrides=patch_overrides,
            generation_options=generation_options,
            scene_context=scene_context,
        )
        status = self.wait_for_job(created.job_id)
        if status is None:
            raise RuntimeError("Scene generation job disappeared before completion.")
        if status.status == "failed":
            raise RuntimeError(status.error or "Scene generation failed.")
        if status.result is None:
            raise RuntimeError("Scene generation completed without a result payload.")
        return status.result

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = Thread(target=self._worker_loop, name="roadgen3d-scene-job-worker", daemon=True)
        self._worker.start()

    def _worker_loop(self) -> None:
        while True:
            job_id = self._queue.get()
            with self._condition:
                state = self._jobs.get(job_id)
                if state is None:
                    self._condition.notify_all()
                    continue
                state.status = "running"
                state.started_at = _utc_now()
                self._apply_progress_locked(
                    state,
                    stage="context_resolving",
                    progress=10,
                    message="Resolving scene generation context.",
                )
                self._condition.notify_all()
            try:
                result = self.generator(
                    state.draft,
                    patch_overrides=state.patch_overrides,
                    generation_options=state.generation_options,
                    scene_context=state.scene_context,
                    progress_callback=lambda event: self._record_progress(job_id, event),
                )
            except Exception as exc:
                with self._condition:
                    latest = self._jobs.get(job_id)
                    if latest is not None:
                        latest.status = "failed"
                        latest.error = str(exc)
                        latest.finished_at = _utc_now()
                        self._apply_progress_locked(
                            latest,
                            stage="failed",
                            progress=latest.progress,
                            message=str(exc) or "Scene generation failed.",
                            detail={"error": str(exc)},
                        )
                    self._condition.notify_all()
                continue
            with self._condition:
                latest = self._jobs.get(job_id)
                if latest is not None:
                    latest.result = result
                    self._apply_progress_locked(
                        latest,
                        stage="evaluation",
                        progress=96,
                        message="Evaluating generated scene.",
                        detail={"scene_layout_path": result.scene_layout_path},
                    )
                self._condition.notify_all()
            evaluation = self._evaluate_result(result)
            with self._condition:
                latest = self._jobs.get(job_id)
                if latest is not None:
                    latest.status = "succeeded"
                    latest.result = result
                    latest.evaluation = evaluation
                    latest.finished_at = _utc_now()
                    self._apply_progress_locked(
                        latest,
                        stage="succeeded",
                        progress=100,
                        message="Scene generation completed.",
                    )
                    self._write_generation_trace_locked(latest)
                self._condition.notify_all()

    def _record_progress(self, job_id: str, event: Mapping[str, Any] | str) -> None:
        payload: Mapping[str, Any]
        if isinstance(event, Mapping):
            payload = event
        else:
            payload = {"message": str(event)}
        with self._condition:
            state = self._jobs.get(job_id)
            if state is None:
                return
            stage = str(payload.get("stage") or payload.get("status") or state.stage or "running")
            message = str(
                payload.get("message")
                or payload.get("name")
                or payload.get("status")
                or stage.replace("_", " ").title()
            )
            progress = payload.get("progress", state.progress)
            detail = payload.get("detail")
            if detail is None:
                detail = {
                    key: value
                    for key, value in payload.items()
                    if key not in {"stage", "status", "progress", "message", "name"}
                }
            self._apply_progress_locked(
                state,
                stage=stage,
                progress=progress,
                message=message,
                detail=detail if isinstance(detail, Mapping) else {"value": detail},
            )
            self._condition.notify_all()

    @staticmethod
    def _coerce_progress(value: Any, fallback: int) -> int:
        try:
            progress = int(round(float(value)))
        except (TypeError, ValueError):
            progress = int(fallback)
        return max(0, min(100, progress))

    @classmethod
    def _apply_progress_locked(
        cls,
        state: _SceneJobState,
        *,
        stage: str,
        progress: Any,
        message: str,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        coerced_progress = cls._coerce_progress(progress, state.progress)
        if stage not in {"failed"}:
            coerced_progress = max(int(state.progress), coerced_progress)
        state.stage = str(stage or state.stage or "running")
        state.progress = coerced_progress
        operation = {
            "timestamp": _utc_now(),
            "stage": state.stage,
            "progress": int(state.progress),
            "message": str(message or state.stage),
            "name": str(message or state.stage),
            "status": str(message or state.stage),
            "detail": dict(detail or {}),
        }
        state.operations.append(operation)
        if len(state.operations) > 50:
            del state.operations[:-50]

    def _evaluate_result(self, result: SceneGenerationResult) -> Dict[str, Any]:
        if self.evaluator is None:
            return {"status": "skipped", "error": "No evaluator configured for this scene job service."}
        layout_path = str(result.scene_layout_path or "").strip()
        if not layout_path:
            return {"status": "skipped", "error": "Scene generation result did not include a layout path."}
        try:
            payload = dict(self.evaluator(layout_path=layout_path))
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}
        return {"status": "succeeded", **payload}

    @classmethod
    def _build_generation_trace(cls, state: _SceneJobState) -> Dict[str, Any]:
        context_detail = _merged_progress_detail(
            state.operations,
            stages=("context_resolving",),
        )
        provenance = {
            "rag_evidence": list(context_detail.get("rag_evidence") or context_detail.get("ragEvidence") or []),
            "rag_queries": list(context_detail.get("rag_queries") or context_detail.get("ragQueries") or []),
            "citations_by_field": _normalize_mapping_of_lists(
                context_detail.get("citations_by_field")
                or context_detail.get("citationsByField")
                or state.draft.citations_by_field
            ),
            "parameter_sources_by_field": dict(
                context_detail.get("parameter_sources_by_field")
                or context_detail.get("parameterSourcesByField")
                or state.draft.parameter_sources_by_field
            ),
            "parameter_decisions_by_field": dict(
                context_detail.get("parameter_decisions_by_field")
                or context_detail.get("parameterDecisionsByField")
                or {}
            ),
            "scenario_parameter_patch": dict(
                context_detail.get("scenario_parameter_patch")
                or context_detail.get("scenarioParameterPatch")
                or {}
            ),
            "scenario_parameter_candidates": list(
                context_detail.get("scenario_parameter_candidates")
                or context_detail.get("scenarioParameterCandidates")
                or []
            ),
            "llm_citations_by_field": _normalize_mapping_of_lists(
                context_detail.get("llm_citations_by_field")
                or context_detail.get("llmCitationsByField")
                or {}
            ),
            "knowledge_source": str(context_detail.get("knowledge_source") or context_detail.get("knowledgeSource") or ""),
            "evidence_count": int(context_detail.get("evidence_count") or 0),
        }
        if not provenance["evidence_count"]:
            provenance["evidence_count"] = len(provenance["rag_evidence"])
        generation_method = infer_generation_method(
            candidate_source=str(context_detail.get("llm_derivation_status") or ""),
            knowledge_source=provenance["knowledge_source"],
            rag_evidence=provenance["rag_evidence"],
            parameter_sources_by_field=provenance["parameter_sources_by_field"],
        )
        provenance["generation_method"] = generation_method
        llm_config_patch = (
            context_detail.get("config_patch")
            or context_detail.get("configPatch")
            or context_detail.get("compose_config_patch")
            or state.draft.compose_config_patch
        )
        result = state.result
        result_payload = {
            "compose_config": dict(result.compose_config) if result is not None else {},
            "summary": dict(result.summary) if result is not None else {},
            "scene_layout_path": result.scene_layout_path if result is not None else "",
            "scene_glb_path": result.scene_glb_path if result is not None else "",
            "scene_ply_path": result.scene_ply_path if result is not None else "",
            "viewer_url": result.viewer_url if result is not None else "",
            "artifact_dir": _artifact_dir_for_result(result, state.generation_options),
            "generation_trace_path": state.trace_artifact_path,
            "generation_method": generation_method,
        }
        return dict(make_json_safe({
            "schema_version": "generation_trace_v1",
            "job_id": state.job_id,
            "generation_method": generation_method,
            "status": state.status,
            "created_at": state.created_at,
            "started_at": state.started_at,
            "finished_at": state.finished_at,
            "error": state.error,
            "provenance": provenance,
            "llm_recommendation": {
                "normalized_scene_query": state.draft.normalized_scene_query,
                "design_summary": str(context_detail.get("design_summary") or state.draft.design_summary or ""),
                "config_patch": dict(llm_config_patch or {}),
                "raw_fields": list(context_detail.get("llm_raw_fields") or []),
                "defaulted_fields": list(context_detail.get("defaulted_fields") or []),
                "overridden_fields": list(context_detail.get("overridden_llm_fields") or []),
                "parameter_decisions_by_field": dict(provenance.get("parameter_decisions_by_field") or {}),
                "llm_citations_by_field": dict(provenance.get("llm_citations_by_field") or {}),
                "risk_notes": list(state.draft.risk_notes),
                "derivation_status": str(context_detail.get("llm_derivation_status") or ""),
                "generation_method": generation_method,
            },
            "process": {
                "current_stage": state.stage,
                "progress": int(state.progress),
                "stage_tree": _build_stage_tree(state.operations, current_stage=state.stage, failed=state.status == "failed"),
                "operations": [dict(item) for item in state.operations],
            },
            "result": result_payload,
            "evaluation": dict(state.evaluation or {"status": "pending"}),
        }))

    @classmethod
    def _write_generation_trace_locked(cls, state: _SceneJobState) -> None:
        trace_path = _trace_artifact_path(state)
        if trace_path is None:
            return
        state.trace_artifact_path = str(trace_path)
        trace = cls._build_generation_trace(state)
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(
            json.dumps(make_json_safe(trace), ensure_ascii=True, indent=2, allow_nan=False),
            encoding="utf-8",
        )

    @staticmethod
    def _to_status_response(state: _SceneJobState) -> SceneJobStatusResponse:
        return SceneJobStatusResponse(
            job_id=state.job_id,
            status=state.status,
            created_at=state.created_at,
            started_at=state.started_at,
            finished_at=state.finished_at,
            error=state.error,
            stage=state.stage,
            progress=state.progress,
            operations=tuple(dict(item) for item in state.operations),
            result=state.result,
            trace=SceneJobService._build_generation_trace(state),
        )

    @staticmethod
    def _to_scene_record(state: _SceneJobState) -> SceneRecord:
        result = state.result
        return SceneRecord(
            job_id=state.job_id,
            status=state.status,
            created_at=state.created_at,
            finished_at=state.finished_at,
            scene_layout_path=result.scene_layout_path if result is not None else "",
            scene_glb_path=result.scene_glb_path if result is not None else "",
            scene_ply_path=result.scene_ply_path if result is not None else "",
            viewer_url=result.viewer_url if result is not None else "",
            summary=dict(result.summary) if result is not None else {},
        )


_STAGE_ORDER = (
    "queued",
    "context_resolving",
    "asset_loading",
    "layout_generation",
    "constraint_solving",
    "asset_composition",
    "mesh_generation",
    "glb_export",
    "scene_rendering",
    "finalizing",
    "evaluation",
    "succeeded",
    "failed",
)


def _merged_progress_detail(
    operations: Sequence[Mapping[str, Any]],
    *,
    stages: Sequence[str],
) -> Dict[str, Any]:
    selected = set(stages)
    merged: Dict[str, Any] = {}
    for operation in operations:
        if str(operation.get("stage", "")) not in selected:
            continue
        detail = operation.get("detail")
        if isinstance(detail, Mapping):
            merged.update(detail)
    return merged


def _normalize_mapping_of_lists(value: Any) -> Dict[str, List[str]]:
    if not isinstance(value, Mapping):
        return {}
    normalized: Dict[str, List[str]] = {}
    for key, item in value.items():
        if isinstance(item, (list, tuple, set)):
            normalized[str(key)] = [str(entry) for entry in item]
        elif item:
            normalized[str(key)] = [str(item)]
        else:
            normalized[str(key)] = []
    return normalized


def _build_stage_tree(
    operations: Sequence[Mapping[str, Any]],
    *,
    current_stage: str,
    failed: bool,
) -> List[Dict[str, Any]]:
    latest_by_stage: Dict[str, Mapping[str, Any]] = {}
    first_index: Dict[str, int] = {}
    for index, operation in enumerate(operations):
        stage = str(operation.get("stage") or "running")
        latest_by_stage[stage] = operation
        first_index.setdefault(stage, index)
    stages = sorted(
        latest_by_stage,
        key=lambda stage: (_STAGE_ORDER.index(stage) if stage in _STAGE_ORDER else len(_STAGE_ORDER), first_index[stage]),
    )
    current_order = _STAGE_ORDER.index(current_stage) if current_stage in _STAGE_ORDER else len(_STAGE_ORDER)
    nodes: List[Dict[str, Any]] = []
    for stage in stages:
        operation = latest_by_stage[stage]
        stage_order = _STAGE_ORDER.index(stage) if stage in _STAGE_ORDER else len(_STAGE_ORDER)
        if failed and stage == current_stage:
            status = "failed"
        elif stage in {"succeeded", "failed"}:
            status = "completed" if stage == "succeeded" else "failed"
        elif stage == current_stage:
            status = "active"
        elif stage_order < current_order:
            status = "completed"
        else:
            status = "pending"
        nodes.append({
            "id": stage,
            "stage": stage,
            "label": str(operation.get("message") or stage.replace("_", " ").title()),
            "status": status,
            "progress": int(operation.get("progress") or 0),
            "timestamp": str(operation.get("timestamp") or ""),
            "children": _stage_detail_children(stage, operation.get("detail")),
        })
    return nodes


def _stage_detail_children(stage: str, detail: Any) -> List[Dict[str, Any]]:
    if not isinstance(detail, Mapping):
        return []
    children: List[Dict[str, Any]] = []
    for key in (
        "rag_evidence",
        "config_patch",
        "street_program",
        "solver_summary",
        "placement_progress",
        "scene_layout_path",
        "layout_path",
    ):
        value = detail.get(key)
        if value is None or value == "":
            continue
        children.append({
            "id": f"{stage}:{key}",
            "label": key,
            "kind": "artifact",
            "summary": _compact_detail_summary(value),
        })
    return children[:6]


def _compact_detail_summary(value: Any) -> str:
    if isinstance(value, Mapping):
        return f"{len(value)} fields"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return f"{len(value)} items"
    return str(value)


def _artifact_dir_for_result(result: SceneGenerationResult | None, generation_options: Mapping[str, Any]) -> str:
    if result is not None and result.scene_layout_path:
        return str(Path(result.scene_layout_path).expanduser().resolve().parent)
    out_dir = generation_options.get("out_dir")
    return str(Path(out_dir).expanduser().resolve()) if out_dir else ""


def _trace_artifact_path(state: _SceneJobState) -> Path | None:
    if state.result is not None and state.result.scene_layout_path:
        return Path(state.result.scene_layout_path).expanduser().resolve().parent / "generation_trace.json"
    out_dir = state.generation_options.get("out_dir")
    if out_dir:
        return Path(out_dir).expanduser().resolve() / "generation_trace.json"
    return None
