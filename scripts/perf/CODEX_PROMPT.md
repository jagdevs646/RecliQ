# Prompt for Codex: carry out the next phase of the scaling plan

Paste everything below the line into Codex, or tell it: "Read `scripts/perf/CODEX_PROMPT.md` and follow it."
Run it once per phase. It does the next unfinished phase, proves it, records it, commits it and stops.

---

You are working in the RecliQ repository (FastAPI + pandas backend in `backend/`, React + TypeScript
frontend in `frontend/`). Your task is to carry out the **next unfinished phase** of
`docs/performance-scaling-plan.md`, exactly as that plan describes it, prove it with the plan's own
"Done when" test, record the result in the plan, commit, and **stop** with a report.

## 1. Read first, in this order

1. `docs/performance-scaling-plan.md`, all of it. Section 9 is the plan (phases, items, "Done when").
   Section 10.1 lists the result defects (Phase 1b). Section 3.4 is the baseline to beat.
   Section 8.1 shows the measured effect of each quick win.
2. `scripts/perf/README.md`, then `scripts/perf/engine_patches.py`, `scripts/perf/quickwins.py`,
   `scripts/perf/bench.py`, `scripts/perf/io_checks.py`, `scripts/perf/repeatability.py`.
3. `backend/app/reconciliation_engine/engine.py` (`_run_generic_reconciliation`, `compare_rule_values`),
   `backend/app/reconciliation_engine/matching/indexed_matcher.py`,
   `backend/app/reconciliation_engine/ingestion/extractor.py`,
   `backend/app/services/reconciliation_service.py`.

The plan's line numbers refer to commit `4197f58`. Locate code by its content, not by line number.

## 2. Find the next phase

Phase 0 is done (status table under "Phase 0" in section 9), except "Size the live container", which
needs the owner. Do not attempt it. Work in this order and pick the first phase that is not done:

1. **Phase 1**: quick wins, no change to results.
2. **Phase 1b**: fix the result defects of section 10.1. Results change on purpose.
3. **Phase 1c**: split `_run_generic_reconciliation` into prepare, key pass, keyless passes and build
   results. No change to results.

**Stop and ask the owner before Phase 2, 3 or 4.** They depend on decisions D3 to D6 (section 10.2) and on
infrastructure (Phase 4.1, a worker), which only the owner can choose and pay for.

Before you change `engine.py`, run `git log --oneline 736ff6c..HEAD -- backend/app/reconciliation_engine`.
If anything other than this plan's own work changed the engine since then, stop and report it: the
patches in `engine_patches.py` are anchored to the engine's text, and the "identical results" proof
assumes an unchanged starting point.

## 3. Phase 1: how to do it

The items are 1.1 to 1.7 in the Phase 1 table. Do them **one item per commit**, in this order:
1.1 (patch 3), 1.2 (patch 2), 1.3 (patch 4), then 1.4, 1.5, 1.6, 1.7.

**Save the "before" results once, before touching the engine.** `quickwins.py` writes the full result to
`scripts/perf/out/qw_<scenario>_<rows>_<tag>.json`, and tag `0` means "the engine as it is", so keep a copy
under a tag of its own:

```bash
backend/.venv/Scripts/python scripts/perf/quickwins.py 20000 keyed 0
backend/.venv/Scripts/python scripts/perf/quickwins.py 20000 keyless 0
backend/.venv/Scripts/python scripts/perf/quickwins.py 20000 secondary_exact 0
backend/.venv/Scripts/python scripts/perf/quickwins.py 4000 secondary 0
```

Then copy each `qw_<scenario>_<rows>_0.json` to `qw_<scenario>_<rows>_base.json` in the same folder.
The `secondary` run at 4,000 rows takes about 4 minutes and `secondary_exact` at 20,000 about a minute
before the fixes; that is expected.

Also record the starting time on this machine:
`backend/.venv/Scripts/python scripts/perf/bench.py 100000 read keyed`.

**For items 1.1 to 1.3**, `engine_patches.py` is the reference implementation, proven to give identical
results (plan section 8.1). Port each one properly into the real modules. Put the candidate cache and the
bounded fallback of patch 4 into `IndexedCandidateMatcher` in `indexed_matcher.py`, not into a subclass
or monkey-patch. Keep the behaviour exactly the same, including the details the plan spells out:
- 1.1 compares values "identical as read" (same type and equal), not normalized text: `-500` and `500`
  normalize alike.
- 1.2 uses exactly the index's test: a blank never matches; similar text needs a shared word.
- 1.3 counts only up to 26 candidates before switching to the key's word index.

**For items 1.4 to 1.7**, follow the table:
- **1.4**: read `.xlsx` with python-calamine and keep openpyxl as the fallback when calamine fails.
  - Pin `python-calamine` in `backend/requirements.txt`, at the version in `scripts/perf/requirements.txt`
    or newer.
  - Bump `_READER_VERSION` in `ingestion/extractor.py`, or old parsed-sheet caches will be reused.
  - Prove the two readers agree:
    - Run `backend/.venv/Scripts/python scripts/perf/io_checks.py 100000 readers`. It compares both
      readers cell by cell on the benchmark workbook.
    - The repository has no committed workbooks (tests build theirs in code). So add a backend test that
      builds one workbook with the awkward cells the plan's risks section names, reads it through both
      paths, and asserts the frames are identical. The awkward cells:
      - dates and date-times
      - 15- to 20-digit references
      - text with leading zeros
      - blanks
      - a column mixing numbers and text
      - percentages and booleans
      - a merged cell
      - a formula with a cached value
  - Report any cell that differs, and do not ship 1.4 if one does.
- **1.5**: check for cancellation at most once a second.
- **1.6**: convert File 2 to dicts once and reuse it.
- **1.7**: build only the index maps the general engine reads. Confirm by search that the GST path, which
  calls `find_best_match`, still gets the maps it needs.

**After every item**, all of these must hold before you commit:
1. Run `quickwins.py <rows> <scenario> 0` for the four scenarios above. Then
   `quickwins.py compare <rows> <scenario> base 0` must report `"identical": true` for each. If an item
   cannot affect a scenario's path you may skip that scenario for that item, but say so; run all four for
   the final item.
2. Both backend test suites pass:
   - from the repository root: `backend/.venv/Scripts/python -m pytest tests/backend -q -p no:cacheprovider`
   - from `backend/`: `.venv/Scripts/python -m pytest tests -q -p no:cacheprovider`
3. `backend/.venv/Scripts/python scripts/perf/repeatability.py 2000 keyed keyless secondary_exact` still
   passes.

**Phase 1 is done** when the plan's "Done when" holds:
- identical output in all four comparisons
- both test suites pass
- `bench.py 100000 read keyed` takes under 65 s end to end from `.xlsx`, or under 40 s for reading plus
  matching and comparing, on the machine used for section 3.4

Timing rules (`scripts/perf/README.md`, "Getting numbers you can trust"):
- one benchmark at a time
- on mains power, with nothing else running
- no sleep during a run

If you cannot guarantee that, still run the benchmark, but label the numbers "not comparable" and leave
the final timing to the owner.

## 4. Phase 1b and Phase 1c (later runs of this prompt)

**Phase 1b.** Fix each defect in section 10.1 (D1, D2 and 3 to 10).
- **One defect per commit, test first.** Write a test that reproduces the defect and fails on the current
  code, using the example in the table, then fix it.
- **D1** (row order): do every exact match first, then the similar-key searches on what is left.
- **D2 and 3** (types): decide each compared column's type once, from a sample of both columns' values.
  The name only breaks ties, and the user can override it.
- **4 and 5**: keys are text, and whole-number floats become integers.
- **6**: stop at the first tolerance band that applies to the field.
- **7**: refuse to combine rows, and say so, when a value cannot be added.
- **8**: decimal comparison with a stated precision.
- **9**: a number format per file or column.
- **10** (formula injection) belongs to item 2.5 of Phase 2. Note it, and skip it in 1b unless the owner
  says otherwise.

Results change on purpose here. Afterwards:
- record new baseline tables in the plan
- check that `bench.py 100000 read keyed` is no slower than after Phase 1
- tell the owner to set the repository variable `PERF_BASELINE_VERSION` to a new value, so the nightly
  speed test records a new baseline

**Phase 1c.** Split `_run_generic_reconciliation` into the four typed functions the plan names, with no
change to results.
- Save `base` results first, as in Phase 1.
- Done when `quickwins.py compare` is identical on all four scenarios and both test suites pass.

## 5. Record the result in the plan

When the phase is done:
- **Add a "Status" table under the phase in section 9**, like Phase 0's. Give one row per item: what was
  done, the commit, the measured effect, and anything not done and why.
- **Add the measured numbers:**
  - Phase 1 and 1c: a short "after" table next to section 3.4, with the same commands, sizes and machine.
  - Phase 1b: the new baseline.
- **Correct the status line at the top of the plan.** It still says nothing has been built. Say which
  phases are done.

## 6. Ground rules for this repository

- **Windows.** The repository path contains spaces, so quote every path. Python is
  `backend/.venv/Scripts/python`. The frontend checks run with `node_modules\.bin\tsc --noEmit -p .` and
  `node_modules\.bin\vitest run` from `frontend/`; you only need them if you touch the frontend.
- **Never** open, print or copy the values in `backend/.env` or any `.env` file. Never commit them.
- **Generated files stay out of git.** Benchmark data goes to `scripts/perf/data/` and `scripts/perf/out/`,
  and results to `*.jsonl`; all of these are ignored. A 100,000-row run writes about 100 MB. Delete what
  you generated when you finish.
- **Other sessions commit to this repository too.**
  - Run `git status` before staging.
  - Stage only the files you changed, by name.
  - Never use `git add -A`, `git commit -a`, rebase, amend, force-push or `reset --hard`.
- **Commit messages:** a conventional prefix (`perf:`, `fix:`, `refactor:`, `docs:`, `test:`), a short
  title, and a plain-English body: what changed, why, and the measured effect.
- **Do not push.** The owner pushes after reviewing. Pushing to `main` deploys the live site (Render runs
  the backend, Vercel the frontend), and CI runs a gitleaks scan of the whole history. Do not add test
  constants that look like real keys or passwords.
- **Do not edit anything outside this repository.** In particular, leave the sibling folder
  `RecliQ Desktop` alone. That desktop app copies `backend/app` and keeps all its data under one
  guest-style workspace ID, so guest-only logic (for example the one-day guest cleanup in
  `app/services/guest_retention.py`, started only from `app/main.py`) must never move into code the
  desktop runs, such as the job sweeper in `app/jobs/recovery.py`.
- **Do not change behaviour the phase does not ask for.** That includes upload and row limits
  (`MAX_UPLOAD_MB`, `MAX_ROWS_PER_SHEET`, the per-run row limits in `services/policy_service.py`), the
  report layout and the API. If you find a bug outside the phase, write it down in your report instead of
  fixing it.
- **Stop and ask** if:
  - a comparison shows a difference you cannot explain
  - a test fails and the fix would change results in a phase that must not change them
  - the plan and the code disagree in a way that changes what you would build

## 7. Your report when you stop

Give:
- the phase and each item's commit hash
- the four comparison results
- the test counts for both suites
- the before and after timings, with the machine conditions
- anything skipped or left for the owner, and why
- the exact next step

Keep it short and factual; say plainly if something failed.
