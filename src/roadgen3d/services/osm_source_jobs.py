"""Local asynchronous OSM acquisition jobs for the expert workbench."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Mapping
from uuid import uuid4

from roadgen3d.services.osm_road_study import (
    OsmRoadPreviewBundle,
    build_osm_road_preview,
    select_osm_road_study_area,
)
from roadgen3d.services.osm_scene_source import osm_scene_source_response


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class _OsmJob:
    id: str
    request: dict[str, Any]
    status: str = "queued"
    stage: str = "queued"
    progress: int = 0
    progress_mode: str = "determinate"
    message: str = "Waiting for an OSM worker."
    detail: dict[str, Any] = field(default_factory=dict)
    operations: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


class OsmSourceJobService:
    def __init__(self, *, cache_dir: str | Path, max_workers: int = 2) -> None:
        self.cache_dir = Path(cache_dir)
        self._pool = ThreadPoolExecutor(max_workers=max(1, int(max_workers)), thread_name_prefix="roadgen-osm")
        self._lock = Lock()
        self._jobs: dict[str, _OsmJob] = {}
        self._previews: dict[str, OsmRoadPreviewBundle] = {}

    def create_job(self, request: Mapping[str, Any]) -> dict[str, Any]:
        job = _OsmJob(id=uuid4().hex, request=dict(request))
        with self._lock:
            self._jobs[job.id] = job
        self._pool.submit(self._run, job.id)
        return self._serialize(job)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(str(job_id))
            return self._serialize(job) if job is not None else None

    def cancel_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(str(job_id))
            if job is None:
                return None
            if job.status not in {"succeeded", "failed", "cancelled"}:
                job.status = "cancelled"
                job.stage = "cancelled"
                job.message = "Cancelled by user."
                job.error = "Cancelled by user."
                job.updated_at = _now()
            return self._serialize(job)

    def retry_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(str(job_id))
            request = dict(job.request) if job is not None else None
        return self.create_job(request) if request is not None else None

    def select(
        self,
        preview_id: str,
        *,
        seed_logical_road_id: str,
        hop_count: int,
        context_buffer_m: float,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            bundle = self._previews.get(str(preview_id))
        if bundle is None:
            raise KeyError(f"OSM preview not found: {preview_id}")
        selected = select_osm_road_study_area(
            bundle,
            seed_logical_road_id=seed_logical_road_id,
            hop_count=hop_count,
            context_buffer_m=context_buffer_m,
            source_id=source_id,
        )
        payload = osm_scene_source_response({
            "bbox": tuple(selected["study"]["annotation_bbox"]),
            "raw_osm": bundle.raw_osm,
            "geojson": selected["filtered_geojson"],
            "normalized": selected["normalized"],
            "provenance": {
                "provider": "OpenStreetMap/Overpass",
                "attribution": "© OpenStreetMap contributors",
                "bbox": list(bundle.bbox),
                "raw_element_count": len(bundle.raw_osm.get("elements", [])),
            },
        })
        payload["osm_study"] = selected["study"]
        payload["warnings"] = list(selected["study"]["warnings"])
        return payload

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status == "cancelled":
                return
            job.status = "running"
            job.stage = "cache_lookup"
            job.progress = 2
            job.message = "Starting OSM acquisition."
            job.updated_at = _now()
            request = dict(job.request)
        try:
            preview_id = uuid4().hex
            bundle = build_osm_road_preview(
                aoi_bbox=request.get("aoi_bbox") or (),
                source_id=str(request.get("source_id") or "osm-scene"),
                cache_dir=self.cache_dir,
                force_refetch=bool(request.get("force_refetch")),
                preview_id=preview_id,
                progress_callback=lambda event: self._progress(job_id, event),
            )
            with self._lock:
                latest = self._jobs.get(job_id)
                if latest is None or latest.status == "cancelled":
                    return
                self._previews[preview_id] = bundle
                latest.status = "succeeded"
                latest.stage = "succeeded"
                latest.progress = 100
                latest.progress_mode = "determinate"
                latest.message = "OSM roads are ready for study-area selection."
                latest.result = dict(bundle.preview)
                latest.updated_at = _now()
                self._append_operation(latest)
        except Exception as exc:
            with self._lock:
                latest = self._jobs.get(job_id)
                if latest is None or latest.status == "cancelled":
                    return
                latest.status = "failed"
                latest.stage = "failed"
                latest.message = str(exc) or "OSM acquisition failed."
                latest.error = str(exc)
                latest.progress_mode = "determinate"
                latest.updated_at = _now()
                self._append_operation(latest)

    def _progress(self, job_id: str, event: Mapping[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status == "cancelled":
                return
            detail = dict(event.get("detail") or {})
            job.stage = str(event.get("stage") or job.stage)
            job.progress = max(job.progress, min(99, int(event.get("progress") or job.progress)))
            job.progress_mode = str(detail.get("progress_mode") or "determinate")
            job.message = str(event.get("message") or job.stage)
            job.detail = detail
            job.updated_at = _now()
            self._append_operation(job)

    @staticmethod
    def _append_operation(job: _OsmJob) -> None:
        job.operations = [*job.operations, {
            "timestamp": job.updated_at,
            "stage": job.stage,
            "progress": job.progress,
            "message": job.message,
            "detail": dict(job.detail),
        }][-50:]

    @staticmethod
    def _serialize(job: _OsmJob) -> dict[str, Any]:
        try:
            elapsed_seconds = max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(job.created_at)).total_seconds()))
        except ValueError:
            elapsed_seconds = 0
        return {
            "id": job.id,
            "kind": "osm_acquisition",
            "status": job.status,
            "stage": job.stage,
            "progress": job.progress,
            "progress_mode": job.progress_mode,
            "message": job.message,
            "detail": {**dict(job.detail), "elapsed_seconds": elapsed_seconds},
            "operations": [dict(item) for item in job.operations],
            "result": dict(job.result),
            "error": job.error,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }


__all__ = ["OsmSourceJobService"]
