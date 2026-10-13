"""Time RecliQ's reconciliation engine at a given number of rows per file.

    python scripts/perf/bench.py <rows> [read] [keyed] [secondary] [secondary_exact] [keyless] [--profile]

Generates a Books file and a Bank file (see ``generate``), then times each step
separately: reading, the engine, the Excel report, and the stored result data.
One JSON line per measurement is printed and appended to results.jsonl.
Run from the backend's virtual environment. See docs/performance-scaling-plan.md.
"""
from __future__ import annotations

import gc
import json
import os
import random
import sys
import time
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
# The engine imports the app settings: keep them away from the development database and storage.
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(HERE / 'out' / 'bench.db').as_posix()}")
os.environ.setdefault("LOCAL_STORAGE_PATH", str(HERE / "out" / "storage"))

import pandas as pd  # noqa: E402

DATA = HERE / "data"
OUT = HERE / "out"
DATA.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)
RESULTS = HERE / "results.jsonl"

PARTIES = ["Sharma Traders Pvt Ltd", "ACME Industries Limited", "Orbit Telecom", "Bharat Steel Co", "Zenith Logistics LLP",
           "Kumar & Sons", "Nova Pharma Private Limited", "Apex Infra Projects", "Sunrise Foods", "Delta Power Systems"]


def peak_memory_mb() -> float:
    """Peak memory of this process; on Windows -1 without psutil."""
    if sys.platform != "win32":
        import resource
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024) / 1e6, 1)
    try:
        import psutil
    except ImportError:
        return -1.0
    info = psutil.Process().memory_info()
    return round(getattr(info, "peak_wset", info.rss) / 1e6, 1)


def generate(rows: int, seed: int = 7) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Books (12 columns) and Bank (10 columns) with the usual kinds of difference.

    Of the Books rows: 3% are missing from Bank, 4% differ in amount, 3% are dated 1-3 days
    later, 15% have a reworded narration, 2% have the reference written differently
    (UTR-123 for UTR123), 1% have a mistyped reference, and 1% are split into two rows.
    Bank also has 3% rows of its own (bank charges).
    """
    rng = random.Random(seed)
    start = date(2026, 4, 1)
    books, bank = [], []
    for i in range(rows):
        ref = f"UTR{26000000 + i}"
        party = rng.choice(PARTIES)
        amount = round(rng.uniform(100, 250000), 2)
        day = start + timedelta(days=rng.randrange(180))
        narration = f"{party} payment invoice {rng.randrange(1000, 9999)}"
        books.append({
            "VOUCHER_NO": f"JV/{i:07d}", "BOOKS_DATE": day, "REFERENCE": ref, "PARTY": party, "NARRATION": narration,
            "DEBIT": amount, "BRANCH": rng.choice(["MUM", "DEL", "BLR", "CHN"]), "COST_CENTRE": f"CC{rng.randrange(40):03d}",
            "GSTIN": f"27ABCDE{rng.randrange(1000, 9999)}F1Z{rng.randrange(10)}", "CREATED_BY": rng.choice(["anita", "rahul", "meera"]),
            "REMARKS": rng.choice(["", "urgent", "monthly", "advance"]), "PERIOD": day.strftime("%b-%Y"),
        })
        roll = rng.random()
        if roll < 0.03:          # Only in books.
            continue
        bank_ref, bank_amount, bank_day, bank_narr, bank_party = ref, amount, day, narration, party
        if roll < 0.07:          # Amount differs.
            bank_amount = round(amount + rng.choice([-1, 1]) * rng.uniform(0.5, 500), 2)
        elif roll < 0.10:        # Date 1-3 days later.
            bank_day = day + timedelta(days=rng.randrange(1, 4))
        elif roll < 0.25:        # Reworded narration.
            bank_narr = f"NEFT {party.upper()} SETTLEMENT"
        elif roll < 0.27:        # Reference written differently (the same key once normalized).
            bank_ref = f"UTR-{26000000 + i}"
        elif roll < 0.28:        # Mistyped reference (only found by similarity).
            digits = list(str(26000000 + i))
            digits[-2], digits[-1] = digits[-1], digits[-2]
            bank_ref = "UTR" + "".join(digits)
            bank_party = party.upper()
        bank.append({
            "TXN_DATE": bank_day, "VALUE_DATE": bank_day, "CHEQUE_UTR": bank_ref, "PAYEE": bank_party,
            "DESCRIPTION": bank_narr, "AMOUNT": bank_amount, "CLOSING_BALANCE": round(rng.uniform(1e5, 9e6), 2),
            "BRANCH_CODE": books[-1]["BRANCH"], "CHANNEL": rng.choice(["NEFT", "RTGS", "IMPS", "UPI"]),
            "TXN_ID": f"T{rng.randrange(10**9):09d}",
        })
        if rng.random() < 0.01:  # A split payment: the same reference twice in books.
            books[-1]["DEBIT"] = round(amount / 2, 2)
            books.append({**books[-1], "VOUCHER_NO": f"JV/{i:07d}B"})
    for j in range(int(rows * 0.03)):  # Bank-only records.
        bank.append({
            "TXN_DATE": start, "VALUE_DATE": start, "CHEQUE_UTR": f"CHG{j:07d}", "PAYEE": "HDFC Bank",
            "DESCRIPTION": "BANK CHARGES", "AMOUNT": round(rng.uniform(10, 900), 2), "CLOSING_BALANCE": 0.0,
            "BRANCH_CODE": "0001", "CHANNEL": "CHG", "TXN_ID": f"C{j:09d}",
        })
    rng.shuffle(bank)
    return pd.DataFrame(books), pd.DataFrame(bank)


def csv_files_for(rows: int) -> dict[str, Path]:
    paths = {f"{name}_csv": DATA / f"{name}_{rows}.csv" for name in ("books", "bank")}
    if not all(path.exists() for path in paths.values()):
        books, bank = generate(rows)
        books.to_csv(paths["books_csv"], index=False)
        bank.to_csv(paths["bank_csv"], index=False)
    return paths


def files_for(rows: int) -> dict[str, Path]:
    """CSV and .xlsx copies of both files (writing a million-row .xlsx takes several minutes)."""
    paths = csv_files_for(rows)
    paths.update({name: DATA / f"{name}_{rows}.xlsx" for name in ("books", "bank")})
    if not all(paths[name].exists() for name in ("books", "bank")):
        for name, frame in zip(("books", "bank"), generate(rows)):  # Same seed, so the same rows as the CSV.
            frame.to_excel(paths[name], index=False, engine="xlsxwriter")
    return paths


def record(entry: dict, results: Path = RESULTS) -> None:
    entry["peak_memory_mb"] = peak_memory_mb()
    print(json.dumps(entry), flush=True)
    with results.open("a", encoding="utf8") as handle:
        handle.write(json.dumps(entry) + "\n")


def load(paths: dict[str, Path], fmt: str = "csv") -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Read both files the way the application does (no upload limits applied)."""
    from app.reconciliation_engine.ingestion.extractor import _parse_table_unchecked
    from app.reconciliation_engine.preprocessing.vector_preprocessing import prepare_dataframe

    timings, frames = {}, []
    for name in ("books", "bank"):
        path = paths[name] if fmt == "xlsx" else paths[f"{name}_csv"]
        started = time.perf_counter()
        frame = _parse_table_unchecked(path, path.name, "Sheet1" if fmt == "xlsx" else "default")
        timings[f"read_{name}_{fmt}_s"] = round(time.perf_counter() - started, 2)
        frames.append(prepare_dataframe(frame))
    return frames[0], frames[1], timings


_RULES = [{"file_1_fields": ["DEBIT"], "file_2_fields": ["AMOUNT"]},
          {"file_1_fields": ["NARRATION"], "file_2_fields": ["DESCRIPTION"]}]
SCENARIOS = {
    # Key REFERENCE = CHEQUE_UTR; compare amount, date and narration; accept small differences.
    "keyed": dict(
        key_file_1=["REFERENCE"], key_file_2=["CHEQUE_UTR"],
        rules=[_RULES[0], {"file_1_fields": ["BOOKS_DATE"], "file_2_fields": ["VALUE_DATE"]}, _RULES[1]],
        tolerances=[{"field": "DEBIT", "amount": 5}, {"field": "NARRATION", "similarity": 75}],
    ),
    # The same key, and the party name "must also match" (similar text).
    "secondary": dict(
        key_file_1=["REFERENCE"], key_file_2=["CHEQUE_UTR"], rules=_RULES,
        secondary_conditions=[{"source_column": "PARTY", "destination_column": "PAYEE", "comparison_method": "matcher_based"}],
    ),
    # The same key, and the branch "must also match" (exact text).
    "secondary_exact": dict(
        key_file_1=["REFERENCE"], key_file_2=["CHEQUE_UTR"], rules=_RULES,
        secondary_conditions=[{"source_column": "BRANCH", "destination_column": "BRANCH_CODE", "comparison_method": "exact_text"}],
    ),
    # No key: same amount and dates at most 3 days apart.
    "keyless": dict(
        key_file_1=[], key_file_2=[], rules=_RULES,
        matching_passes=[{"type": "amount_date", "amount_source": "DEBIT", "amount_destination": "AMOUNT",
                          "date_source": "BOOKS_DATE", "date_destination": "VALUE_DATE", "date_window_days": 3}],
    ),
    # Unhinted text values contain letters and digits on both sides. This is a
    # deliberate stress case for deciding a compared column's type once.
    "keyed_numeric_narration": dict(
        key_file_1=["KEY"], key_file_2=["KEY"],
        rules=[{"file_1_fields": ["NARRATION"], "file_2_fields": ["DESCRIPTION"]}],
        tolerances=[{"field": "NARRATION", "similarity": 75}],
    ),
    # Percentage amount bands plus a required narrative, with bounded
    # candidate ranges because neighboring amounts are spaced apart.
    "keyless_percent_narrative": dict(
        key_file_1=[], key_file_2=[],
        rules=[
            {"file_1_fields": ["AMOUNT"], "file_2_fields": ["AMOUNT"]},
            {"file_1_fields": ["NARRATIVE"], "file_2_fields": ["DESCRIPTION"]},
        ],
        matching_passes=[{
            "type": "amount_tolerance",
            "amount_source": "AMOUNT",
            "amount_destination": "AMOUNT",
            "amount_tolerance_percent": 0.00005,
            "narrative_source": "NARRATIVE",
            "narrative_destination": "DESCRIPTION",
            "narrative_threshold": 100,
        }],
    ),
}


def scenario_frames(
    rows: int,
    scenario: str,
    books: pd.DataFrame,
    bank: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return deterministic inputs for focused Phase 3 matching scenarios."""
    if scenario == "keyed_numeric_narration":
        keys = [f"K{i:08d}" for i in range(rows)]
        source = pd.DataFrame({
            "KEY": keys,
            "NARRATION": [f"Service March 2026 invoice {i:08d}" for i in range(rows)],
        })
        destination = pd.DataFrame({
            "KEY": keys,
            "DESCRIPTION": [f"Service Mar 2026 invoice {i:08d}" for i in range(rows)],
        })
        return source, destination

    if scenario == "keyless_percent_narrative":
        amounts = [1000.0 + i * 1000.0 for i in range(rows)]
        source = pd.DataFrame({
            "AMOUNT": amounts,
            "NARRATIVE": [f"Payment reference {i:08d} for March 2026" for i in range(rows)],
        })
        destination = pd.DataFrame({
            "AMOUNT": [round(amount * (1 + 0.00000025), 6) for amount in amounts],
            "DESCRIPTION": [f"Payment reference {i:08d} for March 2026" for i in range(rows)],
        })
        return source, destination

    return books, bank


def run_engine(rows: int, scenario: str, books: pd.DataFrame, bank: pd.DataFrame, profile: bool = False) -> None:
    from app.reconciliation_engine.engine import run_generic_reconciliation
    from app.reconciliation_engine.universal_reporter import generate_enterprise_report
    from app.utils.json_encoder import safe_json_dump

    gc.collect()
    stages: list[tuple[str, float]] = []
    profiler = None
    if profile:
        import cProfile
        profiler = cProfile.Profile()
        profiler.enable()
    started = time.perf_counter()
    result = run_generic_reconciliation(
        books.copy(), bank.copy(), OUT / "unused.xlsx", write_report=False,
        progress_callback=lambda percent, message: stages.append((message, time.perf_counter())), **SCENARIOS[scenario],
    )
    engine_s = time.perf_counter() - started
    if profiler:
        profiler.disable()
        import pstats
        with (OUT / f"profile_{scenario}_{rows}.txt").open("w", encoding="utf8") as handle:
            stats = pstats.Stats(profiler, stream=handle).sort_stats("tottime")
            stats.print_stats(35)
            stats.sort_stats("cumulative").print_stats(45)
    stage_times, previous = {}, started
    for message, at in stages:
        stage_times[message[:40]] = round(at - previous, 2)
        previous = at
    universal = result["universal_data"]

    started = time.perf_counter()
    report_path = OUT / f"report_{scenario}_{rows}.xlsx"
    generate_enterprise_report(universal, {}, report_path)
    report_s = time.perf_counter() - started

    started = time.perf_counter()
    json_path = OUT / f"report_{scenario}_{rows}.json"
    with json_path.open("w", encoding="utf8") as handle:
        safe_json_dump(universal, handle)
    json_s = time.perf_counter() - started

    summary = result["summary"]
    record({
        "rows": rows, "scenario": scenario, "profiled": profile, "engine_s": round(engine_s, 2),
        "excel_report_s": round(report_s, 2), "result_json_s": round(json_s, 2),
        "report_mb": round(report_path.stat().st_size / 1e6, 1), "result_json_mb": round(json_path.stat().st_size / 1e6, 1),
        "stages": stage_times, "matched": summary["matched_records"], "differences": summary["report_rows"],
        "only_1": summary["only_in_file_1"], "only_2": summary["only_in_file_2"],
        "ambiguous": summary["ambiguous_matches"], "to_confirm": summary["exception_matches"],
        "within_tolerance": summary["within_tolerance_records"],
    })


def main() -> None:
    rows = int(sys.argv[1])
    scenarios = [arg for arg in sys.argv[2:] if arg != "--profile"] or ["keyed"]
    profile = "--profile" in sys.argv
    paths = files_for(rows) if "read" in scenarios else csv_files_for(rows)
    books, bank, csv_timings = load(paths, "csv")
    if "read" in scenarios:
        _, _, xlsx_timings = load(paths, "xlsx")
        record({"rows": rows, "scenario": "read", "books_rows": len(books), "bank_rows": len(bank), **csv_timings, **xlsx_timings})
    for scenario in scenarios:
        if scenario in SCENARIOS:
            scenario_books, scenario_bank = scenario_frames(rows, scenario, books, bank)
            run_engine(rows, scenario, scenario_books, scenario_bank, profile)


if __name__ == "__main__":
    main()
