"""Two checks on reading: is the fast .xlsx reader faithful, and what does one results page cost?

    python scripts/perf/io_checks.py <rows> [readers] [preview]

readers  reads the Books .xlsx with openpyxl (as today) and with calamine, and reports every
         column whose type or values differ between the two.
preview  loads the stored result data of the "keyed" run (run bench.py <rows> keyed first) the
         way the results page does for every page it shows, and reports time and memory.

Needs python-calamine and psutil (see requirements.txt next to this file).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bench  # noqa: E402  (sets sys.path and the environment)

import pandas as pd  # noqa: E402

RESULTS = HERE / "io_results.jsonl"


def readers(rows: int) -> None:
    path = bench.files_for(rows)["books"]
    frames, seconds = {}, {}
    for engine in ("openpyxl", "calamine"):
        started = time.perf_counter()
        frames[engine] = pd.read_excel(path, engine=engine)
        seconds[engine] = round(time.perf_counter() - started, 2)
    old, new = frames["openpyxl"], frames["calamine"]
    differences = []
    if list(old.columns) != list(new.columns) or len(old) != len(new):
        differences.append(f"shape or headers differ: {old.shape} vs {new.shape}")
    else:
        for column in old.columns:
            if str(old[column].dtype) != str(new[column].dtype):
                differences.append(f"{column}: type {old[column].dtype} vs {new[column].dtype}")
            same = (old[column] == new[column]) | (old[column].isna() & new[column].isna())
            if not same.all():
                row = int((~same).idxmax())
                differences.append(f"{column}: {int((~same).sum())} values differ, e.g. row {row + 2}: {old[column][row]!r} vs {new[column][row]!r}")
    bench.record({"rows": rows, "check": "readers", "seconds": seconds, "identical": not differences, "differences": differences}, RESULTS)


def preview(rows: int) -> None:
    path = bench.OUT / f"report_keyed_{rows}.json"
    if not path.exists():
        raise SystemExit(f"{path.name} not found: run bench.py {rows} keyed first")
    before = bench.peak_memory_mb()
    started = time.perf_counter()
    with path.open(encoding="utf8") as handle:
        data = json.load(handle)  # What _report_preview_data does for each page request.
    page = data["matched_records"][:25]
    seconds = round(time.perf_counter() - started, 2)
    bench.record({
        "rows": rows, "check": "preview", "file_mb": round(path.stat().st_size / 1e6, 1), "load_s": seconds,
        "rows_returned": len(page), "memory_before_mb": before,
    }, RESULTS)


def main() -> None:
    rows = int(sys.argv[1])
    checks = sys.argv[2:] or ["readers", "preview"]
    if "readers" in checks:
        readers(rows)
    if "preview" in checks:
        preview(rows)


if __name__ == "__main__":
    main()
