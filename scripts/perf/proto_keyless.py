"""Prototype: the keyless "amount + date within ±3 days" pass, done as a join.

Reproduces the "keyless" benchmark scenario (no key; pass = same amount, dates at most
3 days apart; a pair is matched only when it is mutually unique) and then the field
comparison (amount and narration), to check the outcome counts against the engine.

    python scripts/perf/proto_keyless.py <rows> [range] [cents]

  range  join on "amount within ±0.005 and dates within the window" (the engine's exact test)
  cents  join on equal whole cents, then the date window (same result for 2-decimal amounts)

Needs duckdb (see requirements.txt next to this file). See docs/performance-scaling-plan.md.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bench  # noqa: E402  (sets sys.path and the environment)

import duckdb  # noqa: E402
import pandas as pd  # noqa: E402
from app.reconciliation_engine.matching import compare_values  # noqa: E402
from app.reconciliation_engine.normalization import build_normalizer, use_normalizer  # noqa: E402

WINDOW = 3


def narration_differs(left: list[str], right: list[str]) -> list[bool]:
    """The engine's own comparison (type guessed per row, as today), so counts agree."""
    with use_normalizer(build_normalizer(None, ())):
        out = []
        for a, b in zip(left, right):
            result = compare_values(a, b, "NARRATION", "DESCRIPTION")
            out.append(not (result.matched and result.confidence == 100))
        return out


def run(rows: int, mode: str, books_csv: Path, bank_csv: Path) -> dict:
    steps: dict[str, float] = {}
    con = duckdb.connect()

    def timed(name: str, sql: str) -> None:
        started = time.perf_counter()
        con.execute(sql)
        steps[name] = round(time.perf_counter() - started, 3)

    timed("read_csv", f"""
        CREATE TABLE src AS SELECT row_number() OVER () AS sp, DEBIT AS amount, CAST(BOOKS_DATE AS DATE) AS day, NARRATION AS narr,
               CAST(round(DEBIT * 100) AS BIGINT) AS cents FROM read_csv_auto('{books_csv.as_posix()}');
        CREATE TABLE dst AS SELECT row_number() OVER () AS dp, AMOUNT AS amount, CAST(VALUE_DATE AS DATE) AS day, DESCRIPTION AS narr,
               CAST(round(AMOUNT * 100) AS BIGINT) AS cents FROM read_csv_auto('{bank_csv.as_posix()}');
    """)
    join = (
        "d.amount >= s.amount - 0.005 AND d.amount <= s.amount + 0.005" if mode == "range" else "d.cents = s.cents"
    )
    timed("candidates", f"""
        CREATE TABLE cand AS
        SELECT s.sp, d.dp, abs(date_diff('day', s.day, d.day)) AS gap
        FROM src s JOIN dst d ON {join} AND d.day >= s.day - INTERVAL {WINDOW} DAY AND d.day <= s.day + INTERVAL {WINDOW} DAY;
    """)
    timed("mutually_unique", """
        CREATE TABLE per_source AS SELECT sp, count(*) AS n FROM cand GROUP BY sp;
        CREATE TABLE per_destination AS SELECT dp, count(*) AS claims FROM cand GROUP BY dp;
        CREATE TABLE pairs AS
          SELECT c.sp, c.dp, c.gap = 0 AS exact
          FROM cand c JOIN per_source ps USING (sp) JOIN per_destination pd USING (dp)
          WHERE ps.n = 1 AND pd.claims = 1;
    """)
    started = time.perf_counter()
    differing = con.execute("""
        SELECT p.sp, s.narr AS a, d.narr AS b FROM pairs p JOIN src s USING (sp) JOIN dst d USING (dp)
        WHERE s.narr IS DISTINCT FROM d.narr
    """).fetch_df()
    flags = narration_differs(differing["a"].tolist(), differing["b"].tolist())
    text_diff = pd.DataFrame({"sp": differing["sp"], "differs": flags})  # noqa: F841
    con.execute("CREATE TABLE text_diff AS SELECT * FROM text_diff WHERE differs")
    steps["compare_fields"] = round(time.perf_counter() - started, 3)

    started = time.perf_counter()
    pairs, exact = con.execute("SELECT count(*), count(*) FILTER (WHERE exact) FROM pairs").fetchone()
    sources, destinations = con.execute("SELECT (SELECT count(*) FROM src), (SELECT count(*) FROM dst)").fetchone()
    with_candidates = con.execute("SELECT count(*) FROM per_source").fetchone()[0]
    differences = con.execute(
        "SELECT count(*) FROM pairs p LEFT JOIN text_diff t USING (sp) WHERE NOT p.exact OR t.sp IS NOT NULL"
    ).fetchone()[0]
    steps["classify"] = round(time.perf_counter() - started, 3)
    return {
        "rows": rows, "engine": f"duckdb-keyless-{mode}", "read_csv_s": steps.pop("read_csv"),
        "reconcile_s": round(sum(steps.values()), 2), "steps": steps,
        "matched": pairs, "to_confirm": pairs - exact, "ambiguous": with_candidates - pairs,
        "differences": differences, "only_books": sources - pairs, "only_bank": destinations - pairs,
        "candidate_pairs": con.execute("SELECT count(*) FROM cand").fetchone()[0],
    }


def main() -> None:
    rows = int(sys.argv[1])
    modes = sys.argv[2:] or ["range", "cents"]
    paths = bench.csv_files_for(rows)
    for mode in modes:
        bench.record(run(rows, mode, paths["books_csv"], paths["bank_csv"]), HERE / "proto_results.jsonl")


if __name__ == "__main__":
    main()
