from __future__ import annotations

import concurrent.futures
from typing import Callable, Any

_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8)


def run_in_background(func: Callable[..., Any], *args: Any, **kwargs: Any) -> concurrent.futures.Future:
    """
    Execute CPU-bound reconciliation tasks in thread pool to prevent blocking FastAPI event loop.
    """
    return _EXECUTOR.submit(func, *args, **kwargs)
