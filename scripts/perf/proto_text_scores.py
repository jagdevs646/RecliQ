"""Prototype: score the differing texts of matched pairs in one batch instead of pair by pair.

After the key join, the narrations that are not identical are compared with the engine's text
comparison. This script does that in two ways and checks that every score is the same:

  engine  the engine's own function, one call per pair (what proto_keyed.py does);
  batch   the same steps on whole columns: normal forms computed once per distinct value with
          the engine's own normalizer, then the five rapidfuzz scorers run on all pairs at
          once in compiled code, on every CPU core.

    python scripts/perf/proto_text_scores.py <rows>

See docs/performance-scaling-plan.md, section 7.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bench  # noqa: E402  (sets sys.path and the environment)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from rapidfuzz import fuzz, process  # noqa: E402

from app.reconciliation_engine.cache import normalize_text  # noqa: E402
from app.reconciliation_engine.matching import compare_values  # noqa: E402
from app.reconciliation_engine.normalization import build_normalizer, use_normalizer  # noqa: E402
from app.reconciliation_engine.normalization.entities import active_normalizer  # noqa: E402

SCORERS = (fuzz.WRatio, fuzz.token_sort_ratio, fuzz.token_set_ratio, fuzz.ratio, fuzz.partial_ratio)


def differing_pairs(rows: int) -> tuple[list[str], list[str]]:
    paths = bench.csv_files_for(rows)
    books = pd.read_csv(paths["books_csv"], usecols=["REFERENCE", "NARRATION"])
    bank = pd.read_csv(paths["bank_csv"], usecols=["CHEQUE_UTR", "DESCRIPTION"])
    books["k"] = books["REFERENCE"].str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
    bank["k"] = bank["CHEQUE_UTR"].str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
    both = books.drop_duplicates("k").merge(bank.drop_duplicates("k"), on="k")
    both = both[both["NARRATION"] != both["DESCRIPTION"]]
    return both["NARRATION"].astype(str).tolist(), both["DESCRIPTION"].astype(str).tolist()


def engine_scores(left: list[str], right: list[str]) -> list[int]:
    return [compare_values(a, b, "NARRATION", "DESCRIPTION", "text").confidence for a, b in zip(left, right)]


def batch_scores(left: list[str], right: list[str]) -> list[int]:
    normalizer = active_normalizer()
    forms = {}
    for value in set(left) | set(right):  # Once per distinct value, with the engine's own functions.
        norm = normalize_text(value)
        forms[value] = (norm, normalizer.identity(value), normalizer.canonical(value).text or norm)
    norm_1 = np.array([forms[v][0] for v in left], dtype=object)
    norm_2 = np.array([forms[v][0] for v in right], dtype=object)
    identity_1 = np.array([forms[v][1] or "" for v in left], dtype=object)
    identity_2 = np.array([forms[v][1] or "" for v in right], dtype=object)
    equal = (norm_1 == norm_2) | ((identity_1 != "") & (identity_1 == identity_2))
    scores = np.full(len(left), 100, dtype=np.int64)
    todo = np.flatnonzero(~equal)
    if len(todo):
        canonical_1 = [forms[left[i]][2] for i in todo]
        canonical_2 = [forms[right[i]][2] for i in todo]
        best = np.zeros(len(todo), dtype=np.float64)
        for scorer in SCORERS:
            best = np.maximum(best, process.cpdist(canonical_1, canonical_2, scorer=scorer, workers=-1, dtype=np.float64))
        blank = np.array([not a or not b for a, b in zip(canonical_1, canonical_2)])
        scores[todo] = np.where(blank, 0, np.minimum(best.astype(np.int64), 99))
    return scores.tolist()


def main() -> None:
    rows = int(sys.argv[1])
    left, right = differing_pairs(rows)
    with use_normalizer(build_normalizer(None, ())):
        started = time.perf_counter()
        batch = batch_scores(left, right)
        batch_s = time.perf_counter() - started
        started = time.perf_counter()
        engine = engine_scores(left, right)
        engine_s = time.perf_counter() - started
    differing = sum(a != b for a, b in zip(engine, batch))
    bench.record({
        "rows": rows, "engine": "text-scores", "pairs": len(left), "distinct_values": len(set(left) | set(right)),
        "engine_s": round(engine_s, 2), "batch_s": round(batch_s, 2), "scores_that_differ": differing,
    }, HERE / "proto_results.jsonl")


if __name__ == "__main__":
    main()
