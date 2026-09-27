"""Queue backends.

``redis``  RQ on Redis; jobs survive API restarts and run in separate worker
           processes (``python -m app.jobs.worker``). Use this in production.
``local``  An in-process thread pool for development. The database state
           still makes jobs recoverable: the sweeper re-queues them on start.
``sync``   Runs the job inside the request; for tests and debugging only.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from app.core.config import get_settings

logger = logging.getLogger("recliq.jobs")
_local_executor: ThreadPoolExecutor | None = None

RUNNER_PATH = "app.jobs.runner.run_job"


def rq_job_id(job_id: str) -> str:
    return f"recon-{job_id}"


def redis_connection():
    from redis import Redis

    return Redis.from_url(get_settings().redis_url)


def rq_queue(connection=None):
    from rq import Queue

    settings = get_settings()
    return Queue(settings.job_queue_name, connection=connection or redis_connection(), default_timeout=settings.job_timeout_seconds)


def _executor() -> ThreadPoolExecutor:
    global _local_executor
    if _local_executor is None:
        _local_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="recliq-job")
    return _local_executor


def enqueue_job(job_id: str) -> None:
    """Hand a queued job to the configured backend."""
    settings = get_settings()
    backend = settings.job_queue_backend.lower()
    from app.jobs.runner import run_job

    if backend == "redis":
        queue = rq_queue()
        queue.enqueue(
            RUNNER_PATH,
            job_id,
            job_id=rq_job_id(job_id),
            job_timeout=settings.job_timeout_seconds,
            result_ttl=24 * 3600,
            failure_ttl=7 * 24 * 3600,
            description=f"Reconciliation job {job_id}",
        )
    elif backend == "sync":
        run_job(job_id)
    else:
        _executor().submit(run_job, job_id)
    logger.info("Job queued", extra={"queued_job_id": job_id, "queue_backend": backend})


def is_enqueued(job_id: str) -> bool:
    """Whether the backend still holds this job (redis only; the in-process
    pool is emptied by a restart, so it never does after one)."""
    if get_settings().job_queue_backend.lower() != "redis":
        return False
    from rq.exceptions import NoSuchJobError
    from rq.job import Job

    try:
        status = Job.fetch(rq_job_id(job_id), connection=redis_connection()).get_status(refresh=True)
    except NoSuchJobError:
        return False
    return getattr(status, "value", status) in {"queued", "started", "deferred", "scheduled"}
