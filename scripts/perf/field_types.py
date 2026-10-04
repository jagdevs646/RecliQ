"""Which comparison type does the engine pick for a compared column, row by row?

    python scripts/perf/field_types.py [rows]

Runs the "keyed" scenario of bench.py with a recording wrapper around detect_matcher_type
and shows how often each type was chosen per mapped pair of columns, with examples. A
column whose name gives no hint (NARRATION / DESCRIPTION) is typed again for every row
from the two values in that row. See docs/performance-scaling-plan.md, section 4.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bench  # noqa: E402

from app.reconciliation_engine import engine  # noqa: E402
from app.reconciliation_engine.matching import compare_values  # noqa: E402
from app.reconciliation_engine.normalization import build_normalizer, use_normalizer  # noqa: E402

rows = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
counts: dict = collections.defaultdict(collections.Counter)
examples: dict = {}
original = engine.detect_matcher_type


def recording(values=None, column_name="", other_column_name=""):
    kind = original(values, column_name, other_column_name)
    if values is not None and len(values) == 2:
        label = f"{column_name} / {other_column_name}"
        same = str(values[0]) == str(values[1])
        counts[label][(kind, "same" if same else "different")] += 1
        examples.setdefault((label, kind, same), [str(values[0])[:60], str(values[1])[:60]])
    return kind


engine.detect_matcher_type = recording
paths = bench.csv_files_for(rows)
books, bank, _ = bench.load(paths, "csv")
engine.run_generic_reconciliation(books.copy(), bank.copy(), bench.OUT / "unused.xlsx", write_report=False, **bench.SCENARIOS["keyed"])
for label, counter in counts.items():
    print(label)
    for (kind, same), n in sorted(counter.items()):
        print(f"   {kind:12s} {same:10s} {n:7d}   e.g. {examples[(label, kind, same == 'same')]}")

print("--- a reworded text that keeps a number on both sides")
with use_normalizer(build_normalizer(None, ())):
    for a, b in [
        ("Sharma Traders payment invoice 1234", "Sharma Traders pymt invoice 1234"),
        ("Sharma Traders payment invoice", "Sharma Traders pymt invoice"),
        ("Rent for March 2026", "Rent for Mar 2026"),
        ("Rent for March", "Rent for Mar"),
    ]:
        auto = compare_values(a, b, "NARRATION", "DESCRIPTION")
        text = compare_values(a, b, "NARRATION", "DESCRIPTION", "text")
        print(json.dumps({"a": a, "b": b, "auto_type": auto.matcher_type, "auto_matched": auto.matched, "auto_confidence": auto.confidence,
                          "as_text_matched": text.matched, "as_text_confidence": text.confidence}))
