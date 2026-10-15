"""Run and compare Phase 3 engine outputs against the approved row baseline."""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bench  # noqa: E402
from result_comparison import compare_result_files  # noqa: E402


def _result_path(engine: str, rows: int, scenario: str) -> Path:
    return bench.OUT / f"phase3_{engine}_{scenario}_{rows}.json"


def run(rows: int, scenario: str, engine_mode: str, profile: bool = False) -> None:
    from app.reconciliation_engine.engine import run_generic_reconciliation
    from app.utils.json_encoder import safe_json_dump

    paths = bench.csv_files_for(rows)
    file_1, file_2, _ = bench.load(paths, "csv")
    file_1, file_2 = bench.scenario_frames(rows, scenario, file_1, file_2)
    stages: list[tuple[str, float]] = []
    process = None
    monitor_stop = threading.Event()
    peak_rss = [0]
    try:
        import psutil

        process = psutil.Process()
        peak_rss[0] = process.memory_info().rss

        def sample_rss() -> None:
            while not monitor_stop.wait(0.2):
                peak_rss[0] = max(peak_rss[0], process.memory_info().rss)

        monitor = threading.Thread(target=sample_rss, name="phase3-rss-sampler", daemon=True)
        monitor.start()
    except ImportError:  # pragma: no cover - memory is reported when psutil is installed.
        process = None
        monitor = None
    rss_before = peak_rss[0]
    started = time.perf_counter()
    profiler = None
    try:
        if profile:
            import cProfile

            profiler = cProfile.Profile()
            profiler.enable()
        result = run_generic_reconciliation(
            file_1.copy(), file_2.copy(), bench.OUT / "unused.xlsx", write_report=False,
            engine_mode=engine_mode,
            progress_callback=lambda _percent, message: stages.append((message, time.perf_counter())),
            **bench.SCENARIOS[scenario],
        )
    finally:
        if profiler is not None:
            profiler.disable()
        monitor_stop.set()
        if monitor is not None:
            monitor.join()
    engine_seconds = time.perf_counter() - started
    rss_after = process.memory_info().rss if process is not None else 0
    peak_sampled_rss = max(peak_rss[0], rss_after)
    stage_seconds: dict[str, float] = {}
    previous = started
    for message, at in stages:
        stage_seconds[message] = round(at - previous, 3)
        previous = at
    output = _result_path(engine_mode, rows, scenario)
    with output.open("w", encoding="utf-8") as handle:
        safe_json_dump(result["universal_data"], handle)
    profile_path = None
    if profiler is not None:
        import io
        import pstats

        profile_path = bench.OUT / f"profile_phase3_{engine_mode}_{scenario}_{rows}.txt"
        profile_text = io.StringIO()
        pstats.Stats(profiler, stream=profile_text).sort_stats("cumtime").print_stats(50)
        profile_path.write_text(profile_text.getvalue(), encoding="utf-8")
    print(json.dumps({
        "engine": engine_mode,
        "rows_per_file": rows,
        "scenario": scenario,
        "engine_seconds": round(engine_seconds, 3),
        "stages": stage_seconds,
        "rss_before_mb": round(rss_before / 1e6, 1) if process is not None else None,
        "rss_after_mb": round(rss_after / 1e6, 1) if process is not None else None,
        "peak_sampled_rss_mb": round(peak_sampled_rss / 1e6, 1) if process is not None else None,
        "output": str(output),
        "profile": str(profile_path) if profile_path else None,
        "matched": result["summary"]["matched_records"],
        "differences": result["summary"]["report_rows"],
        "only_file_1": result["summary"]["only_in_file_1"],
        "only_file_2": result["summary"]["only_in_file_2"],
    }))


def compare(rows: int, scenario: str, baseline: str, candidate: str) -> int:
    expected_path = bench.OUT / f"qw_{scenario}_{rows}_{baseline}.json"
    if not expected_path.exists():
        engine_baseline = bench.OUT / f"phase3_{baseline}_{scenario}_{rows}.json"
        if engine_baseline.exists():
            expected_path = engine_baseline
        elif baseline == "phase3base":
            row_baseline = bench.OUT / f"phase3_rows_{scenario}_{rows}.json"
            if row_baseline.exists():
                expected_path = row_baseline
    actual_path = _result_path(candidate, rows, scenario)
    difference_count, differences = compare_result_files(expected_path, actual_path)
    print(json.dumps({
        "scenario": scenario,
        "rows_per_file": rows,
        "baseline": baseline,
        "candidate": candidate,
        "identical": difference_count == 0,
        "difference_count": difference_count,
        "differences": [item.__dict__ for item in differences[:20]],
    }, ensure_ascii=False, indent=2))
    return 0 if difference_count == 0 else 1


def main() -> int:
    scenarios = sorted(bench.SCENARIOS)
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("rows", type=int)
    run_parser.add_argument("scenario", choices=scenarios)
    run_parser.add_argument("engine", choices=("rows", "columns"))
    run_parser.add_argument("--profile", action="store_true")
    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("rows", type=int)
    compare_parser.add_argument("scenario", choices=scenarios)
    compare_parser.add_argument("baseline", default="phase3base", nargs="?")
    compare_parser.add_argument("candidate", choices=("rows", "columns"))
    args = parser.parse_args()

    if args.command == "run":
        run(args.rows, args.scenario, args.engine, profile=args.profile)
        return 0
    return compare(args.rows, args.scenario, args.baseline, args.candidate)


if __name__ == "__main__":
    raise SystemExit(main())
