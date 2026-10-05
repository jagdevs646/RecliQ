"""Check that two runs of the engine give byte-identical result data.

    python scripts/perf/repeatability.py <rows> [scenario ...]

Runs each scenario in two separate processes with different hash seeds (the order of a
Python set changes from one process to the next, never within one), writes the result
data of each run the way the application stores it, and compares the two files byte for
byte. The only field allowed to differ is the ``generated_at`` timestamp, which is
blanked before writing. Exits with status 1 if any scenario differs.
"""
from __future__ import annotations

import os
import subprocess
import sys

from bench import OUT, SCENARIOS, csv_files_for, load


def output_path(rows: int, scenario: str, seed: str):
    return OUT / f"repeat_{scenario}_{rows}_{seed}.json"


def write_result(rows: int, scenario: str, seed: str) -> None:
    from app.reconciliation_engine.engine import run_generic_reconciliation
    from app.utils.json_encoder import safe_json_dump

    books, bank, _ = load(csv_files_for(rows), "csv")
    result = run_generic_reconciliation(books, bank, OUT / "unused.xlsx", write_report=False, **SCENARIOS[scenario])
    universal = result["universal_data"]
    universal.get("metadata", {})["generated_at"] = ""
    with output_path(rows, scenario, seed).open("w", encoding="utf8") as handle:
        safe_json_dump(universal, handle)


def main() -> None:
    if sys.argv[1] == "--one":
        write_result(int(sys.argv[2]), sys.argv[3], sys.argv[4])
        return
    rows = int(sys.argv[1])
    scenarios = sys.argv[2:] or ["keyed"]
    csv_files_for(rows)  # Generate the files once, before the two runs.
    failed = False
    for scenario in scenarios:
        for seed in ("1", "2"):
            env = {**os.environ, "PYTHONHASHSEED": seed}
            subprocess.run([sys.executable, __file__, "--one", str(rows), scenario, seed], env=env, check=True)
        first, second = (output_path(rows, scenario, seed).read_bytes() for seed in ("1", "2"))
        if first == second:
            print(f"{scenario} at {rows} rows: identical ({len(first):,} bytes)")
            continue
        failed = True
        at = next((i for i, (a, b) in enumerate(zip(first, second)) if a != b), min(len(first), len(second)))
        print(f"{scenario} at {rows} rows: DIFFERENT (sizes {len(first):,} and {len(second):,}; first difference at byte {at:,})")
        print(f"  run 1: ...{first[max(0, at - 80):at + 80].decode('utf8', 'replace')}...")
        print(f"  run 2: ...{second[max(0, at - 80):at + 80].decode('utf8', 'replace')}...")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
