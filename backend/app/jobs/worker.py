"""RQ worker process: ``python -m app.jobs.worker``.

Recovers jobs orphaned by a previous crash, keeps a recovery sweeper running,
then processes the reconciliation queue until stopped.
"""
from __future__ import annotations

import logging
import os

from app.core.config import get_settings
from app.core.observability import configure_logging, init_error_tracking
from app.jobs.queue import redis_connection, rq_queue
from app.jobs.recovery import recover_orphaned_jobs, start_sweeper
from app.jobs.runner import worker_id

logger = logging.getLogger("recliq.jobs")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    init_error_tracking(settings.sentry_dsn, settings.environment, settings.release, settings.sentry_traces_sample_rate)
    if settings.job_queue_backend.lower() != "redis":
        raise SystemExit("JOB_QUEUE_BACKEND must be 'redis' to run a worker process.")

    from rq import SimpleWorker, Worker
    from rq.timeouts import TimerDeathPenalty

    connection = redis_connection()
    connection.ping()
    recover_orphaned_jobs()
    start_sweeper()

    if os.name == "nt":
        # Windows has neither fork nor SIGALRM: run jobs in-process with a
        # timer-based timeout (development only).
        class WindowsWorker(SimpleWorker):
            death_penalty_class = TimerDeathPenalty

        worker_class = WindowsWorker
    else:
        worker_class = Worker
    worker = worker_class([rq_queue(connection)], connection=connection, name=worker_id())
    logger.info("Worker started", extra={"queue": settings.job_queue_name, "worker": worker_id()})
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
