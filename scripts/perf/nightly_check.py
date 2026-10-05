"""Fail the nightly speed test when the engine got slower or bigger.

    python scripts/perf/nightly_check.py <rows> <scenario> <baseline.json>

Reads the latest ``bench.py`` result for the scenario from results.jsonl. The time
checked is the engine plus the Excel report plus the result data. The first run on a
runner has no baseline: it records its own time in <baseline.json> and passes. Later
runs fail above 1.5 times that time, and every run fails above 1,200 MB peak memory
(docs/performance-scaling-plan.md, Phase 0).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

TIME_FACTOR = 1.5
MEMORY_LIMIT_MB = 1200.0
RESULTS = Path(__file__).resolve().parent / "results.jsonl"


def main() -> None:
    rows, scenario, baseline_path = int(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    entries = [json.loads(line) for line in RESULTS.read_text(encoding="utf8").splitlines() if line.strip()]
    latest = [entry for entry in entries if entry.get("rows") == rows and entry.get("scenario") == scenario][-1]
    seconds = round(latest["engine_s"] + latest["excel_report_s"] + latest["result_json_s"], 2)
    memory = latest["peak_memory_mb"]

    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf8"))
    else:
        baseline = {"rows": rows, "scenario": scenario, "seconds": seconds, "commit": os.environ.get("GITHUB_SHA", "")}
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(baseline), encoding="utf8")
        print(f"No baseline on this runner yet: recorded {seconds} s as the baseline.")

    time_limit = round(baseline["seconds"] * TIME_FACTOR, 2)
    failures = []
    if seconds > time_limit:
        failures.append(f"time {seconds} s is above {time_limit} s ({TIME_FACTOR} x the baseline of {baseline['seconds']} s)")
    if memory < 0 or memory > MEMORY_LIMIT_MB:
        failures.append(f"peak memory {memory} MB is above {MEMORY_LIMIT_MB:.0f} MB" if memory >= 0 else "peak memory was not measured")

    lines = [
        f"### Speed test: {scenario}, {rows:,} rows per file",
        "",
        "| | This run | Limit |",
        "|---|---:|---:|",
        f"| Engine + Excel report + result data | {seconds} s | {time_limit} s |",
        f"| of which the engine | {latest['engine_s']} s | |",
        f"| Peak memory | {memory} MB | {MEMORY_LIMIT_MB:.0f} MB |",
        "",
        f"Baseline: {baseline['seconds']} s, recorded at commit `{baseline.get('commit', '')[:7]}`.",
        "",
        "**Failed:** " + "; ".join(failures) if failures else "**Passed.**",
    ]
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf8") as handle:
            handle.write("\n".join(lines) + "\n")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
