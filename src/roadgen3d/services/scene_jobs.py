"""In-memory scene job queue for the web workbench."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from queue import Queue
from threading import Condition, Lock, Thread
from typing import Any, Callable, Dict, List, Mapping
from uuid import uuid4

from .design_runtime import generate_scene_from_draft
from .design_types import (
    DesignDraft,
    SceneGenerationResult,
    SceneContext,
    SceneJobCreateResponse,
    SceneJobStatusResponse,
    SceneRecord,
)


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


class SceneJobService:
    """Single-process background worker for scene generation jobs."""

    def __init__(
        self,
        *,
        generator: Callable[..., SceneGenerationResult] | None = None,
    ) -> None:
        self.generator = generator or generate_scene_from_draft
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
                    latest.status = "succeeded"
                    latest.result = result
                    latest.finished_at = _utc_now()
                    self._apply_progress_locked(
                        latest,
                        stage="succeeded",
                        progress=100,
                        message="Scene generation completed.",
                    )
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
