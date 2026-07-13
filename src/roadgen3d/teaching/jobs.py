"""RQ entrypoint for persistent teaching-platform jobs."""

from __future__ import annotations

import os

from roadgen3d.llm.design_workflow import DesignAssistantService

from .service import TeachingPlatformService


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
