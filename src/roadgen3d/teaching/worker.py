"""Run an RQ worker with `python -m roadgen3d.teaching.worker`."""

from __future__ import annotations

import os

from redis import Redis
from rq import Queue, Worker

from .jobs import enqueue_job
from .service import TeachingPlatformService


def main() -> None:
    connection = Redis.from_url(os.getenv("ROADGEN_REDIS_URL", "redis://127.0.0.1:6379/0"))
    for job_id in TeachingPlatformService().recover_incomplete_jobs():
        enqueue_job(job_id)
    worker = Worker([Queue("roadgen3d", connection=connection)], connection=connection)
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
