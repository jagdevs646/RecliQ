from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class SessionCleanupPolicy:
    max_age: timedelta = timedelta(hours=24)


def expired_session_directories(
    root: Path,
    *,
    now: datetime | None = None,
    policy: SessionCleanupPolicy = SessionCleanupPolicy(),
) -> list[Path]:
    """List expired UUID session folders for a future cleanup task.

    This is intentionally side-effect free. A scheduler can later call this
    function and remove the returned folders after applying its own locking
    and in-progress-job policy.
    """
    if not root.exists():
        return []
    current = now or datetime.now(timezone.utc)
    cutoff = current - policy.max_age
    return [
        path
        for path in root.iterdir()
        if path.is_dir() and datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) < cutoff
    ]
