"""Durable reconciliation job execution.

The database row is the source of truth for a job's state. The queue only
carries job IDs: a worker claims a queued job with a conditional update, keeps
a heartbeat while it runs, and a sweeper retries or fails jobs whose worker
stopped (for example after a restart or a crash).
"""
from app.jobs.queue import enqueue_job

__all__ = ["enqueue_job"]
