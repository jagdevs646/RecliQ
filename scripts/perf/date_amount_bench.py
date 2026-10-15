"""Measure the Date & Amount engine on reproducible, exact-match datasets.

Run from the repository root with the backend virtual environment, e.g.:
    backend/.venv/Scripts/python scripts/perf/date_amount_bench.py 1000 10000 100000
"""
from __future__ import annotations

import gc
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd
try:
    import psutil
except ImportError:  # Memory metrics are optional; the reconciliation itself has no extra dependency.
    psutil = None

from app.reconciliation_engine.date_amount import reconcile_date_amount


CONFIG = {
    "date_source": "DATE",
    "date_destination": "DATE",
    "date_format_source": "auto",
    "date_format_destination": "auto",
    "amount_mode_source": "single",
    "amount_mode_destination": "single",
    "amount_source": "AMOUNT",
    "amount_destination": "AMOUNT",
    "positive_is_source": "debit",
    "positive_is_destination": "debit",
    "text_source": "ACCOUNT",
    "text_destination": "ACCOUNT",
    "amount_tolerance": "0",
    "date_tolerance_days": 0,
    "verify_transactions": True,
    "allow_group_matches": True,
    "max_group_size": 4,
}


def make_frame(rows: int) -> pd.DataFrame:
    start = date(2020, 1, 1)
    indices = range(rows)
    return pd.DataFrame({
        "DATE": [(start + timedelta(days=index % 365)).isoformat() for index in indices],
        "AMOUNT": [f"{(index // 365) * 1000 + (index % 365) + 1}.25" for index in indices],
        "ACCOUNT": [f"ACCT-{index % 23:02d}" for index in indices],
    })


def benchmark(rows: int) -> dict[str, object]:
    source = make_frame(rows)
    destination = source.copy(deep=True)
    process = psutil.Process() if psutil else None
    before = process.memory_info().rss if process else None
    started = time.perf_counter()
    result = reconcile_date_amount(source, destination, CONFIG)
    elapsed = time.perf_counter() - started
    memory = process.memory_info() if process else None
    summary = result["summary"]
    return {
        "rows_per_file": rows,
        "elapsed_seconds": round(elapsed, 3),
        "rows_per_second": round(rows * 2 / elapsed, 1) if elapsed else None,
        "rss_before_mb": round(before / 1024 / 1024, 1) if before is not None else None,
        "rss_after_mb": round(memory.rss / 1024 / 1024, 1) if memory else None,
        "peak_rss_mb": round(getattr(memory, "peak_wset", memory.rss) / 1024 / 1024, 1) if memory else None,
        "verified_match_groups": summary["verified_match_groups"],
        "matched_source_rows": summary["verified_matched_source_rows"],
        "exceptions": summary["only_in_file_1"] + summary["only_in_file_2"],
        "correct": summary["verified_matched_source_rows"] == rows
        and summary["verified_matched_destination_rows"] == rows
        and summary["only_in_file_1"] == 0
        and summary["only_in_file_2"] == 0,
    }


def main() -> int:
    sizes = [int(value) for value in sys.argv[1:]] or [1_000, 10_000, 100_000]
    for size in sizes:
        if size < 1:
            raise SystemExit("Dataset sizes must be positive integers.")
        print(json.dumps(benchmark(size), sort_keys=True))
        gc.collect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
