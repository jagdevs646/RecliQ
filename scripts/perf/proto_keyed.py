"""Prototype: the keyed reconciliation done on whole columns instead of row by row.

It reproduces the outcome counts of the "keyed" scenario in bench.py (key REFERENCE =
CHEQUE_UTR; compare amount, date and narration; accept amount differences up to 5 and
narrations at least 75% similar) with one join and column comparisons, in pandas, Polars
and DuckDB, to measure what the design in docs/performance-scaling-plan.md can reach.
Texts that differ are scored with the engine's own comparison, so the counts agree.

    python scripts/perf/proto_keyed.py <rows> [pandas] [polars] [duckdb] [io]

"io" also times file formats: .xlsx with openpyxl and calamine, CSV, Parquet, pickle, and
writing results as Parquet, CSV and Excel (set BIG_XLSX=1 to also write every matched row
to Excel). Needs the optional packages in requirements.txt next to this file.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bench  # noqa: E402  (sets sys.path and the environment)

import pandas as pd  # noqa: E402

try:  # Optional: only needed for the engines named on the command line.
    import duckdb
    import polars as pl
except ImportError:  # pragma: no cover
    duckdb = pl = None
from app.reconciliation_engine.matching import compare_values  # noqa: E402
from app.reconciliation_engine.normalization import build_normalizer, use_normalizer  # noqa: E402


def engine_text_scores(left: list[str], right: list[str]) -> list[int]:
    """Similarity of each differing text pair, from the engine's own comparison (so results agree)."""
    with use_normalizer(build_normalizer(None, ())):
        return [compare_values(a, b, "NARRATION", "DESCRIPTION", "text").confidence for a, b in zip(left, right)]

RESULTS = HERE / "proto_results.jsonl"
OUT = bench.OUT


def record(entry: dict) -> None:
    bench.record(entry, RESULTS)


def timed(timings: dict, name: str):
    class _Timer:
        def __enter__(self):
            self.started = time.perf_counter()

        def __exit__(self, *exc):
            timings[name] = round(time.perf_counter() - self.started, 3)
    return _Timer()


# ── pandas ────────────────────────────────────────────────────────────────
def reconcile_pandas(books: pd.DataFrame, bank: pd.DataFrame) -> tuple[dict, dict]:
    t: dict[str, float] = {}
    with timed(t, "normalize_keys"):
        books = books.copy()
        bank = bank.copy()
        books["_ROW"] = books.index + 2
        bank["_ROW"] = bank.index + 2
        books["_KEY"] = books["REFERENCE"].astype(str).str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
        bank["_KEY"] = bank["CHEQUE_UTR"].astype(str).str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
    with timed(t, "consolidate_duplicates"):
        # Rows sharing the key on one side are summed, as the engine does today.
        def consolidate(frame: pd.DataFrame, amount: str) -> pd.DataFrame:
            duplicated = frame["_KEY"].duplicated(keep=False)
            if not duplicated.any():
                frame["_GROUP"] = 1
                return frame
            singles = frame[~duplicated].assign(_GROUP=1)
            groups = frame[duplicated].groupby("_KEY", sort=False)
            merged = groups.first()
            merged[amount] = groups[amount].sum()
            merged["_GROUP"] = groups.size()
            return pd.concat([singles, merged.reset_index()], ignore_index=True)
        books = consolidate(books, "DEBIT")
        bank = consolidate(bank, "AMOUNT")
    with timed(t, "join"):
        joined = books.merge(bank, on="_KEY", how="outer", suffixes=("", "_BANK"), indicator=True)
        both = joined[joined["_merge"] == "both"]
        only_books = joined[joined["_merge"] == "left_only"]
        only_bank = joined[joined["_merge"] == "right_only"]
    with timed(t, "compare"):
        amount_diff = (both["DEBIT"] - both["AMOUNT"]).round(2)
        date_gap = (pd.to_datetime(both["BOOKS_DATE"]) - pd.to_datetime(both["VALUE_DATE"])).dt.days.abs()
        left = both["NARRATION"].astype(str).str.strip().str.lower()
        right = both["DESCRIPTION"].astype(str).str.strip().str.lower()
        text_differs = left != right
    with timed(t, "similarity_on_differing_text"):
        # Fuzzy scoring only where the texts differ (a minority of rows).
        scores = pd.Series(100, index=both.index, dtype="int64")
        if text_differs.any():
            a = both.loc[text_differs, "NARRATION"].astype(str).tolist()
            b = both.loc[text_differs, "DESCRIPTION"].astype(str).tolist()
            scores.loc[text_differs] = engine_text_scores(a, b)
        text_differs = scores < 100
    with timed(t, "tolerances_and_classify"):
        amount_bad = amount_diff.abs() > 5
        amount_ok_with_tolerance = (amount_diff.abs() > 0.005) & ~amount_bad
        date_bad = date_gap > 0
        text_bad = text_differs & (scores < 75)
        text_ok_with_tolerance = text_differs & ~text_bad
        differs = amount_bad | date_bad | text_bad
        within_tolerance = ~differs & (amount_ok_with_tolerance | text_ok_with_tolerance)
        both = both.assign(AMOUNT_DIFF=amount_diff, DATE_GAP_DAYS=date_gap, NARRATION_SIMILARITY=scores)
        differences = both[differs]
        matched = both[~differs]
    with timed(t, "comments"):
        # The reason text, built only for rows that need one.
        comment = pd.Series("", index=matched.index, dtype="object")
        amount_note = amount_ok_with_tolerance[matched.index]
        text_note = text_ok_with_tolerance[matched.index]
        comment[amount_note] = "DEBIT differs by " + matched.loc[amount_note, "AMOUNT_DIFF"].abs().map("{:.2f}".format) + ", within ±5"
        text_part = "NARRATION is " + matched.loc[text_note, "NARRATION_SIMILARITY"].astype(str) + "% similar, at least 75% required"
        comment[text_note] = (comment[text_note] + "; " + text_part).str.lstrip("; ")
        matched = matched.assign(COMMENT=comment)
    frames = {"matched": matched, "differences": differences, "only_books": only_books, "only_bank": only_bank}
    counts = {name: len(frame) for name, frame in frames.items()}
    counts["within_tolerance"] = int(within_tolerance.sum())
    return t, {"counts": counts, "frames": frames}


# ── polars ────────────────────────────────────────────────────────────────
def reconcile_polars(books_path: Path, bank_path: Path) -> tuple[dict, dict]:
    t: dict[str, float] = {}
    with timed(t, "read_csv"):
        books = pl.read_csv(books_path, try_parse_dates=True).with_row_index("_ROW", offset=2)
        bank = pl.read_csv(bank_path, try_parse_dates=True).with_row_index("_ROW_BANK", offset=2)
    with timed(t, "normalize_keys"):
        books = books.with_columns(pl.col("REFERENCE").str.to_uppercase().str.replace_all(r"[^A-Z0-9]", "").alias("_KEY"))
        bank = bank.with_columns(pl.col("CHEQUE_UTR").str.to_uppercase().str.replace_all(r"[^A-Z0-9]", "").alias("_KEY"))
    with timed(t, "consolidate_duplicates"):
        books = books.group_by("_KEY", maintain_order=True).agg(
            pl.all().exclude("DEBIT").first(), pl.col("DEBIT").sum(), pl.len().alias("_GROUP"))
        bank = bank.group_by("_KEY", maintain_order=True).agg(
            pl.all().exclude("AMOUNT").first(), pl.col("AMOUNT").sum(), pl.len().alias("_GROUP_BANK"))
    with timed(t, "join"):
        both = books.join(bank, on="_KEY", how="inner")
        only_books = books.join(bank, on="_KEY", how="anti")
        only_bank = bank.join(books, on="_KEY", how="anti")
    with timed(t, "compare"):
        both = both.with_columns(
            (pl.col("DEBIT") - pl.col("AMOUNT")).round(2).alias("AMOUNT_DIFF"),
            (pl.col("BOOKS_DATE") - pl.col("VALUE_DATE")).dt.total_days().abs().alias("DATE_GAP_DAYS"),
            (pl.col("NARRATION").str.strip_chars().str.to_lowercase() != pl.col("DESCRIPTION").str.strip_chars().str.to_lowercase()).alias("_TEXT_DIFFERS"),
        )
    with timed(t, "similarity_on_differing_text"):
        differing = both.filter(pl.col("_TEXT_DIFFERS")).select("_KEY", "NARRATION", "DESCRIPTION")
        scores = engine_text_scores(differing["NARRATION"].to_list(), differing["DESCRIPTION"].to_list())
        both = both.join(pl.DataFrame({"_KEY": differing["_KEY"], "NARRATION_SIMILARITY": scores}), on="_KEY", how="left").with_columns(
            pl.col("NARRATION_SIMILARITY").fill_null(100)).with_columns((pl.col("NARRATION_SIMILARITY") < 100).alias("_TEXT_DIFFERS"))
    with timed(t, "tolerances_and_classify"):
        both = both.with_columns(
            ((pl.col("AMOUNT_DIFF").abs() > 5) | (pl.col("DATE_GAP_DAYS") > 0) | (pl.col("_TEXT_DIFFERS") & (pl.col("NARRATION_SIMILARITY") < 75))).alias("_DIFFERS"))
        differences = both.filter(pl.col("_DIFFERS"))
        matched = both.filter(~pl.col("_DIFFERS"))
    with timed(t, "comments"):
        matched = matched.with_columns(
            pl.concat_str([
                pl.when((pl.col("AMOUNT_DIFF").abs() > 0.005)).then(pl.format("DEBIT differs by {}, within ±5", pl.col("AMOUNT_DIFF").abs())).otherwise(pl.lit("")),
                pl.when(pl.col("_TEXT_DIFFERS")).then(pl.format("NARRATION is {}% similar, at least 75% required", pl.col("NARRATION_SIMILARITY"))).otherwise(pl.lit("")),
            ], separator="; ").str.strip_chars("; ").alias("COMMENT"))
    frames = {"matched": matched, "differences": differences, "only_books": only_books, "only_bank": only_bank}
    return t, {"counts": {name: frame.height for name, frame in frames.items()}, "frames": frames}


# ── duckdb ────────────────────────────────────────────────────────────────
def reconcile_duckdb(books_path: Path, bank_path: Path) -> tuple[dict, dict]:
    t: dict[str, float] = {}
    con = duckdb.connect()
    with timed(t, "read_csv"):
        con.execute(f"CREATE TABLE books AS SELECT *, regexp_replace(upper(REFERENCE), '[^A-Z0-9]', '', 'g') AS k FROM read_csv_auto('{books_path.as_posix()}')")
        con.execute(f"CREATE TABLE bank AS SELECT *, regexp_replace(upper(CHEQUE_UTR), '[^A-Z0-9]', '', 'g') AS k FROM read_csv_auto('{bank_path.as_posix()}')")
    with timed(t, "consolidate_and_join_and_compare"):
        con.execute("""
            CREATE TABLE b1 AS SELECT k, first(NARRATION) AS narration, first(BOOKS_DATE) AS d, sum(DEBIT) AS amount, count(*) AS n FROM books GROUP BY k;
            CREATE TABLE b2 AS SELECT k, first(DESCRIPTION) AS narration, first(VALUE_DATE) AS d, sum(AMOUNT) AS amount, count(*) AS n FROM bank GROUP BY k;
            CREATE TABLE both_sides AS
              SELECT b1.k, b1.amount AS a1, b2.amount AS a2, round(b1.amount - b2.amount, 2) AS amount_diff,
                     abs(date_diff('day', b1.d, b2.d)) AS date_gap,
                     b1.narration AS n1, b2.narration AS n2,
                     lower(trim(b1.narration)) <> lower(trim(b2.narration)) AS text_differs
              FROM b1 JOIN b2 USING (k);
        """)
    with timed(t, "similarity_on_differing_text"):
        differing = con.execute("SELECT k, n1, n2 FROM both_sides WHERE text_differs").to_arrow_table()
        scores = engine_text_scores(differing["n1"].to_pylist(), differing["n2"].to_pylist())
        score_table = pl.DataFrame({"k": differing["k"].to_pylist(), "similarity": scores}).to_arrow()  # noqa: F841
        con.execute("CREATE TABLE scored AS SELECT b.*, coalesce(s.similarity, 100) AS similarity FROM both_sides b LEFT JOIN score_table s USING (k)")
    with timed(t, "classify"):
        counts = dict(zip(("matched", "differences"), con.execute("""
            SELECT count(*) FILTER (WHERE NOT (abs(amount_diff) > 5 OR date_gap > 0 OR similarity < 75)),
                   count(*) FILTER (WHERE abs(amount_diff) > 5 OR date_gap > 0 OR similarity < 75) FROM scored""").fetchone()))
        counts["only_books"] = con.execute("SELECT count(*) FROM b1 ANTI JOIN b2 USING (k)").fetchone()[0]
        counts["only_bank"] = con.execute("SELECT count(*) FROM b2 ANTI JOIN b1 USING (k)").fetchone()[0]
    return t, {"counts": counts, "frames": {}}


# ── input and output formats ──────────────────────────────────────────────
def measure_io(rows: int, paths: dict[str, Path], frames: dict[str, pd.DataFrame]) -> None:
    t: dict[str, float] = {}
    books_xlsx = paths["books"]
    if rows <= 200_000:
        with timed(t, "read_xlsx_openpyxl"):
            pd.read_excel(books_xlsx, engine="openpyxl")
    with timed(t, "read_xlsx_calamine"):
        frame = pd.read_excel(books_xlsx, engine="calamine")
    with timed(t, "read_csv_pandas"):
        pd.read_csv(paths["books_csv"])
    with timed(t, "read_csv_pyarrow"):
        pd.read_csv(paths["books_csv"], engine="pyarrow")
    parquet = OUT / f"books_{rows}.parquet"
    with timed(t, "write_parquet_cache"):
        frame.to_parquet(parquet, index=False)
    with timed(t, "read_parquet_cache"):
        pd.read_parquet(parquet)
    pickle_path = OUT / f"books_{rows}.pkl"
    with timed(t, "write_pickle_cache"):
        frame.to_pickle(pickle_path)
    with timed(t, "read_pickle_cache"):
        pd.read_pickle(pickle_path)
    t["parquet_mb"] = round(parquet.stat().st_size / 1e6, 1)
    t["pickle_mb"] = round(pickle_path.stat().st_size / 1e6, 1)
    t["xlsx_mb"] = round(books_xlsx.stat().st_size / 1e6, 1)

    matched = frames["matched"].drop(columns=["_merge"], errors="ignore")
    differences = frames["differences"].drop(columns=["_merge"], errors="ignore")
    with timed(t, "write_results_parquet_all"):
        for name, frame in frames.items():
            frame.drop(columns=["_merge"], errors="ignore").to_parquet(OUT / f"proto_{name}_{rows}.parquet", index=False)
    with timed(t, "write_matched_csv"):
        matched.to_csv(OUT / f"proto_matched_{rows}.csv", index=False)
    with timed(t, "write_differences_xlsx_constant_memory"):
        import xlsxwriter
        workbook = xlsxwriter.Workbook(OUT / f"proto_differences_{rows}.xlsx", {"constant_memory": True})
        sheet = workbook.add_worksheet("Differences")
        sheet.write_row(0, 0, list(differences.columns))
        for index, values in enumerate(differences.astype(object).where(differences.notna(), None).itertuples(index=False, name=None), start=1):
            sheet.write_row(index, 0, [str(v) if hasattr(v, "isoformat") else v for v in values])
        workbook.close()
    if len(matched) < 200_000 or os.environ.get("BIG_XLSX"):
        with timed(t, "write_matched_xlsx_constant_memory"):
            import xlsxwriter
            workbook = xlsxwriter.Workbook(OUT / f"proto_matched_{rows}.xlsx", {"constant_memory": True})
            sheet = workbook.add_worksheet("Matched")
            sheet.write_row(0, 0, list(matched.columns))
            for index, values in enumerate(matched.astype(object).where(matched.notna(), None).itertuples(index=False, name=None), start=1):
                sheet.write_row(index, 0, [str(v) if hasattr(v, "isoformat") else v for v in values])
            workbook.close()
    t["differences_rows"] = len(differences)
    t["matched_rows"] = len(matched)
    record({"rows": rows, "engine": "io", **t})


def main() -> None:
    rows = int(sys.argv[1])
    engines = [arg for arg in sys.argv[2:] if arg != "io"] or ["pandas"]
    started = time.perf_counter()
    # The .xlsx copies are only needed for "io" (writing a million-row .xlsx takes several minutes).
    paths = bench.files_for(rows) if "io" in sys.argv[2:] else bench.csv_files_for(rows)
    print(f"files ready in {time.perf_counter() - started:.1f}s", flush=True)

    frames = {}
    for engine in engines:
        if engine == "pandas":
            started = time.perf_counter()
            books = pd.read_csv(paths["books_csv"], parse_dates=["BOOKS_DATE"])
            bank = pd.read_csv(paths["bank_csv"], parse_dates=["TXN_DATE", "VALUE_DATE"])
            read_s = time.perf_counter() - started
            started = time.perf_counter()
            timings, result = reconcile_pandas(books, bank)
            total = time.perf_counter() - started
            frames = result["frames"]
        elif engine == "polars":
            started = time.perf_counter()
            timings, result = reconcile_polars(paths["books_csv"], paths["bank_csv"])
            read_s = timings.pop("read_csv")
            total = time.perf_counter() - started - read_s
        else:
            started = time.perf_counter()
            timings, result = reconcile_duckdb(paths["books_csv"], paths["bank_csv"])
            read_s = timings.pop("read_csv")
            total = time.perf_counter() - started - read_s
        record({"rows": rows, "engine": engine, "read_csv_s": round(read_s, 2), "reconcile_s": round(total, 2), "steps": timings, **result["counts"]})
    if "io" in sys.argv[2:] and frames:
        measure_io(rows, paths, frames)


if __name__ == "__main__":
    main()
