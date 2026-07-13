"""RQ entrypoint for persistent teaching-platform jobs."""

from __future__ import annotations

import os
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import Any

from roadgen3d.llm.design_workflow import DesignAssistantService

from .service import TeachingPlatformService


class LocalTeachingJobExecutor:
    """Small process-local executor used by ``make dev`` without Redis."""

    def __init__(self, service: TeachingPlatformService, design_service: Any) -> None:
        self.service = service
        self.design_service = design_service
        self.pool = ThreadPoolExecutor(
            max_workers=max(1, int(os.getenv("ROADGEN_LOCAL_JOB_WORKERS", "2"))),
            thread_name_prefix="roadgen3d-teaching",
        )
        self._lock = Lock()
        self._futures: dict[str, Future[dict[str, Any]]] = {}

    def submit(self, job_id: str) -> None:
        with self._lock:
            existing = self._futures.get(job_id)
            if existing is not None and not existing.done():
                return
            future = self.pool.submit(
                self.service.execute_job,
                job_id,
                evaluator=self.design_service.evaluate_scene_unified,
                generator=self.design_service.generate_scene,
            )
            self._futures[job_id] = future
            future.add_done_callback(lambda _future, resolved_id=job_id: self._discard(resolved_id))

    def recover(self) -> None:
        for job_id in self.service.recover_incomplete_jobs():
            self.submit(job_id)

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=False)

    def _discard(self, job_id: str) -> None:
        with self._lock:
            self._futures.pop(job_id, None)


def execute_job(job_id: str) -> dict:
    service = TeachingPlatformService()
    design = DesignAssistantService()
    return service.execute_job(job_id, evaluator=design.evaluate_scene_unified, generator=design.generate_scene)


def enqueue_job(job_id: str) -> None:
    from redis import Redis
    from rq import Queue
    from rq.exceptions import NoSuchJobError
    from rq.job import Job

    connection = Redis.from_url(os.getenv("ROADGEN_REDIS_URL", "redis://127.0.0.1:6379/0"))
    try:
        existing = Job.fetch(job_id, connection=connection)
        if existing.get_status(refresh=True) in {"queued", "started", "deferred", "scheduled"}:
            return
    except NoSuchJobError:
        pass
    Queue("roadgen3d", connection=connection, default_timeout=int(os.getenv("ROADGEN_JOB_TIMEOUT_SECONDS", "1800"))).enqueue(
        "roadgen3d.teaching.jobs.execute_job",
        job_id,
        job_id=job_id,
        result_ttl=86400,
        failure_ttl=604800,
    )
