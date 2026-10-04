"""pytest plugin: run the project's own test suite against the patched engine (see engine_patches.py).

engine.py is not modified. After the tests are collected, the functions the patches touch get
the patched code, in memory only. From the repository root (bash):

    PYTHONPATH=scripts/perf QW_FIXES=2,3,4 backend/.venv/Scripts/python -m pytest tests/backend -q -p no:cacheprovider -p patched_engine_plugin
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine_patches  # noqa: E402

_PATCHED_FUNCTIONS = ("compare_rule_values", "_run_generic_reconciliation")


def pytest_collection_finish(session) -> None:
    fixes = {part for part in os.environ.get("QW_FIXES", "").split(",") if part}
    if not fixes:
        return
    import app.reconciliation_engine.engine as real

    source = engine_patches.patched_source(Path(real.__file__), fixes)
    namespace: dict = {"__name__": real.__name__, "__file__": real.__file__}
    exec(compile(source, real.__file__, "exec"), namespace)
    for name in ("_passes_like_index", "_FastMatcher", "_qw_compact", "_qw_tokens"):
        setattr(real, name, namespace[name])
    for name in _PATCHED_FUNCTIONS:
        getattr(real, name).__code__ = namespace[name].__code__
    print(f"\n[qw] engine functions patched in memory with fixes {sorted(fixes)}")
