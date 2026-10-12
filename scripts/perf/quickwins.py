"""Time the in-place engine changes (see engine_patches.py) and check that results do not change.

    python scripts/perf/quickwins.py <rows> <scenario> [0|2|3|4 ...]     run and save the complete result
    python scripts/perf/quickwins.py compare <rows> <scenario> <fixes-a> <fixes-b>

"0" is the engine as it is. Each run writes out/qw_<scenario>_<rows>_<fixes>.json (the data behind
the results page and the report), so two runs can be compared value by value. Example:

    python scripts/perf/quickwins.py 20000 secondary_exact 0
    python scripts/perf/quickwins.py 20000 secondary_exact 2 3 4
    python scripts/perf/quickwins.py compare 20000 secondary_exact 0 234
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bench  # noqa: E402  (sets sys.path and the environment)
import engine_patches  # noqa: E402

ENGINE = bench.BACKEND / "app" / "reconciliation_engine" / "engine.py"
OUT = bench.OUT
LOG = HERE / "quickwins.jsonl"


def patched_engine(fixes: set[str]):
    tag = engine_patches.tag(fixes)
    target = OUT / "patched" / f"engine_fix{tag}.py"
    target.parent.mkdir(exist_ok=True)
    target.write_text(engine_patches.patched_source(ENGINE, fixes), encoding="utf8")
    spec = importlib.util.spec_from_file_location(f"engine_fix_{tag}", target)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module, tag


def dump_path(rows: int, scenario: str, tag: str) -> Path:
    return OUT / f"qw_{scenario}_{rows}_{tag}.json"


def first_differences(a, b, path="", found=None, limit=8):
    found = [] if found is None else found
    if len(found) >= limit:
        return found
    if type(a) is not type(b):
        found.append(f"{path}: type {type(a).__name__} vs {type(b).__name__}")
    elif isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                found.append(f"{path}.{key}: only on one side")
            else:
                first_differences(a[key], b[key], f"{path}.{key}", found, limit)
            if len(found) >= limit:
                break
    elif isinstance(a, list):
        if len(a) != len(b):
            found.append(f"{path}: {len(a)} vs {len(b)} items")
        for index, (x, y) in enumerate(zip(a, b)):
            first_differences(x, y, f"{path}[{index}]", found, limit)
            if len(found) >= limit:
                break
    elif a != b:
        found.append(f"{path}: {a!r} vs {b!r}")
    return found


def record(entry: dict) -> None:
    print(json.dumps(entry), flush=True)
    with LOG.open("a", encoding="utf8") as handle:
        handle.write(json.dumps(entry) + "\n")


def compare(rows: int, scenario: str, tag_a: str, tag_b: str) -> None:
    a = json.loads(dump_path(rows, scenario, tag_a).read_text(encoding="utf8"))
    b = json.loads(dump_path(rows, scenario, tag_b).read_text(encoding="utf8"))
    for data in (a, b):
        # Two things differ between any two runs of the unchanged engine: the timestamp, and the
        # order of the per-field summary (built from a Python set, whose order changes per process).
        data.get("metadata", {}).pop("generated_at", None)
        data["field_exception_summary"] = sorted(data.get("field_exception_summary", []), key=lambda item: str(item.get("Field")))
    differences = first_differences(a, b)
    record({"compare": [tag_a, tag_b], "rows": rows, "scenario": scenario, "identical": not differences, "differences": differences})


def main() -> None:
    if sys.argv[1] == "compare":
        compare(int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5])
        return
    rows, scenario = int(sys.argv[1]), sys.argv[2]
    fixes = set(sys.argv[3:]) or {"0"}
    paths = bench.csv_files_for(rows)
    books, bank, _ = bench.load(paths, "csv")
    engine, tag = patched_engine(fixes)
    started = time.perf_counter()
    result = engine.run_generic_reconciliation(books.copy(), bank.copy(), OUT / "unused.xlsx", write_report=False, **bench.SCENARIOS[scenario])
    elapsed = time.perf_counter() - started
    from app.utils.json_encoder import safe_json_dump
    with dump_path(rows, scenario, tag).open("w", encoding="utf8") as handle:
        safe_json_dump(result["universal_data"], handle)
    summary = result["summary"]
    record({
        "rows": rows, "scenario": scenario, "fixes": tag, "engine_s": round(elapsed, 2),
        "matched": summary["matched_records"], "differences": summary["report_rows"], "only_1": summary["only_in_file_1"],
        "only_2": summary["only_in_file_2"], "ambiguous": summary["ambiguous_matches"], "to_confirm": summary["exception_matches"],
        "within_tolerance": summary["within_tolerance_records"],
    })


if __name__ == "__main__":
    main()
