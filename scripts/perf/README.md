# Performance scripts

These scripts produced the measurements in [docs/performance-scaling-plan.md](../../docs/performance-scaling-plan.md).
They time the engine as it is, try three in-place changes on a copy of `engine.py`, and
prototype the column-based design. None of them modifies anything under `backend/`.

Run them from the repository root with the backend's virtual environment:

```bash
backend/.venv/Scripts/python scripts/perf/bench.py 100000 read keyed
```

| Script | What it does |
| --- | --- |
| `bench.py` | Generates Books and Bank inputs of the given size and times reading, the engine, the Excel report and the stored result data. Scenarios: `keyed`, `secondary`, `secondary_exact`, `keyless`, `keyed_numeric_narration`, `keyless_percent_narrative`; add `read` to time `.xlsx` against CSV and `--profile` for a profile. |
| `field_types.py` | Shows which comparison type the engine picks for each compared column, row by row. |
| `engine_patches.py` | The three in-place changes of Phase 1, as text patches applied to a copy of `engine.py`. |
| `quickwins.py` | Runs the engine with any of those changes, saves the complete result, and compares two runs value by value. |
| `result_comparison.py` | Compares saved universal result JSON files by record identity, ignoring record order while reporting field-level differences. |
| `patched_engine_plugin.py` | pytest plugin that runs the backend test suite with those changes applied in memory. |
| `proto_keyed.py` | The keyed reconciliation as one join and column comparisons, in pandas, Polars and DuckDB. With `io` it also times file formats. |
| `proto_keyless.py` | The keyless "amount + date window" pass as a join in DuckDB. |
| `proto_text_scores.py` | Scores the differing texts of matched pairs in one batch and checks every score against the engine's. |
| `io_checks.py` | Compares the calamine and openpyxl readers cell by cell, and measures what one results page costs today. |
| `repeatability.py` | Runs a scenario in two processes with different hash seeds and checks that the result data is byte-identical. |
| `nightly_check.py` | Used by `.github/workflows/perf-nightly.yml`: fails when the latest `bench.py` run is above 1.5 times the runner's baseline or above 1,200 MB peak memory. |

`bench.py` needs nothing beyond the backend's own packages (`psutil` adds peak memory to the
output). The prototypes need the packages in `requirements.txt` here, which are **not**
dependencies of RecliQ:

```bash
backend/.venv/Scripts/python -m pip install -r scripts/perf/requirements.txt
```

Generated files go to `scripts/perf/data/` and `scripts/perf/out/`, and results to `*.jsonl`
files next to the scripts. All of these are ignored by git. A million-row run writes about
1 GB there.

## Getting numbers you can trust

- Run one script at a time, on mains power, with the machine otherwise idle. On battery the
  same run took about 1.75 times longer on the laptop used for the plan.
- Do not let the computer sleep during a run: the timer keeps counting.
- Each run of the unchanged engine differs from the next in two places only: the
  `generated_at` timestamp, and the order of `field_exception_summary` (it is built from a
  Python set). `quickwins.py compare` ignores both.

## Checking that a change does not alter results

```bash
backend/.venv/Scripts/python scripts/perf/quickwins.py 20000 secondary_exact 0
backend/.venv/Scripts/python scripts/perf/quickwins.py 20000 secondary_exact 2 3 4
backend/.venv/Scripts/python scripts/perf/quickwins.py compare 20000 secondary_exact 0 234
```

and the whole backend test suite against the patched engine:

```bash
PYTHONPATH=scripts/perf QW_FIXES=2,3,4 backend/.venv/Scripts/python -m pytest tests/backend -q -p no:cacheprovider -p patched_engine_plugin
```
