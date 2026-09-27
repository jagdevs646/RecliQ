"""Data-quality pre-check, run before matching.

For every sheet rule it reads both sheets exactly as the run will (same
header cleanup, same transformations, same key normal form) and reports:

* missing or empty columns and empty sheets (blockers: the run would fail);
* blank key values and repeated keys (repeats are combined by the run);
* how many source keys exist in the other file at all;
* compared fields whose types disagree, and non-numeric values in amounts;
* ambiguous or mixed date formats, with the detected convention;
* blank secondary keys and unusable amount/date values for keyless passes.

Blockers must be fixed; warnings can be corrected or confirmed by the user.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Iterable

import pandas as pd
from sqlalchemy.orm import Session

from app.reconciliation_engine.cache import is_blank, normalize_header, to_number, use_date_convention
from app.reconciliation_engine.matching.indexed_matcher import detect_matcher_type, non_empty_values, normalized_identity_value
from app.reconciliation_engine.normalization.entities import build_normalizer, use_normalizer
from app.reconciliation_engine.transformations import apply_transformations
from app.schemas.reconciliation import ReconciliationPlan

_NUMERIC_DATE = re.compile(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\s*$")
_ISO_DATE = re.compile(r"^\s*\d{4}-\d{1,2}-\d{1,2}")
_EXAMPLES = 5
LOW_OVERLAP = 0.5


def _issue(severity: str, category: str, message: str, *, side: str | None = None, column: str | None = None,
           count: int | None = None, examples: list[dict] | None = None, suggestion: dict | None = None) -> dict:
    return {
        "severity": severity,
        "category": category,
        "side": side,
        "column": column,
        "message": message,
        "count": count,
        "examples": examples or [],
        "suggestion": suggestion,
    }


def _examples(df: pd.DataFrame, mask: pd.Series, column: str) -> list[dict]:
    rows = df.loc[mask].head(_EXAMPLES)
    return [
        {"row": int(row.get("_ROW_NO", index + 2)), "value": None if is_blank(row.get(column)) else str(row.get(column))}
        for index, row in rows.iterrows()
    ]


def _blank_mask(series: pd.Series) -> pd.Series:
    return series.map(is_blank)


def _side_name(side: str) -> str:
    return "source" if side == "source" else "destination"


def _date_evidence(series: pd.Series) -> dict[str, Any]:
    """Classify numeric text dates: which could be read either way?"""
    day_first = month_first = ambiguous = iso = 0
    ambiguous_examples: list[str] = []
    for value in series:
        if is_blank(value) or isinstance(value, (datetime, date, pd.Timestamp)):
            continue
        text = str(value)
        if _ISO_DATE.match(text):
            iso += 1
            continue
        match = _NUMERIC_DATE.match(text)
        if not match:
            continue
        first, second = int(match.group(1)), int(match.group(2))
        if first > 12 >= second:
            day_first += 1
        elif second > 12 >= first:
            month_first += 1
        elif first != second and first <= 12 and second <= 12:
            ambiguous += 1
            if len(ambiguous_examples) < _EXAMPLES:
                ambiguous_examples.append(text)
    return {"day_first": day_first, "month_first": month_first, "ambiguous": ambiguous, "iso": iso, "examples": ambiguous_examples}


def _check_dates(df: pd.DataFrame, column: str, side: str, dayfirst: bool) -> list[dict]:
    evidence = _date_evidence(df[column])
    issues: list[dict] = []
    current = "day first (DD/MM/YYYY)" if dayfirst else "month first (MM/DD/YYYY)"
    switch = {"date_format": "month_first" if dayfirst else "day_first"}
    wrong = evidence["month_first"] if dayfirst else evidence["day_first"]
    right = evidence["day_first"] if dayfirst else evidence["month_first"]
    if wrong and not right:
        issues.append(_issue(
            "blocker", "date_format",
            f"{column} contains dates such as {'03/25/2026' if dayfirst else '25/03/2026'}, which are only valid "
            f"{'month first' if dayfirst else 'day first'}, but this rule reads dates {current}. Switch the date format.",
            side=side, column=column, count=wrong, suggestion=switch,
        ))
    elif wrong and right:
        issues.append(_issue(
            "warning", "date_format",
            f"{column} mixes day-first and month-first dates ({right} vs {wrong} values). Some dates will be read incorrectly.",
            side=side, column=column, count=wrong,
        ))
    if evidence["ambiguous"] and not wrong:
        confirmed_by = f"; {right} other value(s) confirm this format" if right else ""
        issues.append(_issue(
            "info" if right else "warning", "date_format",
            f"{evidence['ambiguous']} date(s) in {column} (e.g. {', '.join(evidence['examples'][:2])}) could be read either way. "
            f"They will be read {current}{confirmed_by}.",
            side=side, column=column, count=evidence["ambiguous"],
            examples=[{"row": None, "value": value} for value in evidence["examples"]],
            suggestion=None if right else switch,
        ))
    if evidence["iso"] and (evidence["day_first"] or evidence["month_first"] or evidence["ambiguous"]):
        issues.append(_issue(
            "info", "date_format", f"{column} mixes ISO dates (2026-03-25) with other formats; both are understood.",
            side=side, column=column, count=evidence["iso"],
        ))
    return issues


def _numeric_share(series: pd.Series) -> tuple[float, pd.Series]:
    values = series[~_blank_mask(series)]
    if values.empty:
        return 0.0, pd.Series(dtype=bool)
    non_numeric = values.map(lambda value: to_number(value) is None)
    return 1 - non_numeric.mean(), non_numeric


def _check_rule(df1: pd.DataFrame, df2: pd.DataFrame, rule: dict) -> list[dict]:
    issues: list[dict] = []
    dayfirst = bool(rule.get("date_dayfirst", True))
    frames = {"source": df1, "destination": df2}

    for side, df in frames.items():
        if df.empty:
            issues.append(_issue("blocker", "empty", f"The {side} sheet has no data rows.", side=side))
    if any(df.empty for df in frames.values()):
        return issues

    keys = {
        "source": [normalize_header(column) for column in rule.get("key_file_1") or []],
        "destination": [normalize_header(column) for column in rule.get("key_file_2") or []],
    }
    mappings = [
        ([normalize_header(column) for column in mapping.get("file_1_fields", [])], [normalize_header(column) for column in mapping.get("file_2_fields", [])])
        for mapping in rule.get("rules", [])
    ]
    conditions = rule.get("secondary_conditions") or []
    passes = rule.get("matching_passes") or []

    referenced = {
        "source": keys["source"] + [column for left, _ in mappings for column in left]
        + [normalize_header(condition["source_column"]) for condition in conditions]
        + [normalize_header(item[field]) for item in passes for field in ("amount_source", "date_source", "narrative_source") if item.get(field)],
        "destination": keys["destination"] + [column for _, right in mappings for column in right]
        + [normalize_header(condition["destination_column"]) for condition in conditions]
        + [normalize_header(item[field]) for item in passes for field in ("amount_destination", "date_destination", "narrative_destination") if item.get(field)],
    }
    missing_any = False
    for side, columns in referenced.items():
        missing = [column for column in dict.fromkeys(columns) if column not in frames[side].columns]
        if missing:
            missing_any = True
            issues.append(_issue(
                "blocker", "missing_column", f"Column(s) not found in the {side} sheet: {', '.join(missing)}.", side=side, count=len(missing)
            ))
    if missing_any:
        return issues

    # ── Keys ──────────────────────────────────────────────────────────────
    identities: dict[str, pd.Series] = {}
    if keys["source"]:
        key_types = [
            detect_matcher_type(non_empty_values(list(df1[s].head(500)) + list(df2[d].head(500)), limit=200), s, d)
            for s, d in zip(keys["source"], keys["destination"])
        ]
        for side, df in frames.items():
            columns = keys[side]
            blank = pd.Series(False, index=df.index)
            for column in columns:
                blank |= _blank_mask(df[column])
            blank_count = int(blank.sum())
            if blank_count == len(df):
                issues.append(_issue("blocker", "blank_keys", f"Every row has a blank key ({', '.join(columns)}) in the {side} sheet.",
                                     side=side, column=", ".join(columns), count=blank_count))
            elif blank_count:
                issues.append(_issue(
                    "warning", "blank_keys",
                    f"{blank_count:,} of {len(df):,} {side} rows ({blank_count / len(df):.1%}) have a blank key; they cannot be matched by key"
                    + (" but are still tried by the amount/date passes." if passes else " and will be listed as not found."),
                    side=side, column=", ".join(columns), count=blank_count, examples=_examples(df, blank, columns[0]),
                ))
            identity = df[columns].apply(
                lambda row: tuple(normalized_identity_value(value, kind) for value, kind in zip(row, key_types)), axis=1
            )
            identity = identity[~blank]
            identities[side] = identity
            repeated = identity[identity.duplicated(keep=False)]
            if not repeated.empty:
                groups = repeated.nunique()
                grouping = " and secondary keys" if conditions else ""
                issues.append(_issue(
                    "warning" if groups > len(df) * 0.2 else "info", "duplicate_keys",
                    f"{groups:,} key value(s) appear more than once in the {side} sheet ({len(repeated):,} rows). "
                    f"Rows sharing the same key{grouping} are combined and their amounts added before matching.",
                    side=side, column=", ".join(columns), count=groups,
                    examples=_examples(df, pd.Series(df.index.isin(repeated.index), index=df.index), columns[0]),
                ))
        source_keys, destination_keys = set(identities["source"]), set(identities["destination"])
        if source_keys:
            overlap = len(source_keys & destination_keys) / len(source_keys)
            if overlap < LOW_OVERLAP:
                issues.append(_issue(
                    "warning", "key_overlap",
                    f"Only {overlap:.0%} of source keys exist in the destination sheet. Check that the key columns hold the same "
                    "identifier in both files (a prefix or format difference may need a transformation).",
                    count=len(source_keys & destination_keys),
                ))
            else:
                issues.append(_issue("info", "key_overlap", f"{overlap:.0%} of source keys exist in the destination sheet.",
                                     count=len(source_keys & destination_keys)))

    # ── Compared fields ──────────────────────────────────────────────────
    date_columns: dict[str, set[str]] = {"source": set(), "destination": set()}
    for left, right in mappings:
        if len(left) != 1 or len(right) != 1:
            continue
        column_1, column_2 = left[0], right[0]
        kind = detect_matcher_type(non_empty_values(list(df1[column_1].head(300)) + list(df2[column_2].head(300)), limit=200), column_1, column_2)
        share_1, bad_1 = _numeric_share(df1[column_1])
        share_2, bad_2 = _numeric_share(df2[column_2])
        if kind == "numeric" or (share_1 >= 0.9 and share_2 >= 0.9):
            for side, df, column, share, bad in (("source", df1, column_1, share_1, bad_1), ("destination", df2, column_2, share_2, bad_2)):
                if bad.empty:
                    continue
                count = int(bad.sum())
                if share < 0.5:
                    issues.append(_issue("warning", "type_mismatch",
                                         f"{column} in the {side} sheet is mostly text, but it is compared as an amount.",
                                         side=side, column=column, count=count))
                elif count:
                    mask = pd.Series(False, index=df.index)
                    mask.loc[bad[bad].index] = True
                    issues.append(_issue("warning", "non_numeric",
                                         f"{count:,} value(s) in {column} ({side}) are not numbers and will be treated as blank.",
                                         side=side, column=column, count=count, examples=_examples(df, mask, column)))
        elif (share_1 >= 0.9) != (share_2 >= 0.9) and min(share_1, share_2) < 0.5:
            issues.append(_issue("warning", "type_mismatch",
                                 f"{column_1} ↔ {column_2}: one side holds numbers and the other text, so values will rarely agree.",
                                 column=f"{column_1} ↔ {column_2}"))
        if kind == "date":
            date_columns["source"].add(column_1)
            date_columns["destination"].add(column_2)

    for side, columns in keys.items():
        for column in columns:
            if detect_matcher_type(non_empty_values(frames[side][column].head(300), limit=100), column, "") == "date":
                date_columns[side].add(column)
    for condition in conditions:
        if condition.get("comparison_method") == "normalized_date":
            date_columns["source"].add(normalize_header(condition["source_column"]))
            date_columns["destination"].add(normalize_header(condition["destination_column"]))
        for side, column in (("source", normalize_header(condition["source_column"])), ("destination", normalize_header(condition["destination_column"]))):
            blank = int(_blank_mask(frames[side][column]).sum())
            if blank:
                issues.append(_issue("warning", "blank_secondary",
                                     f"{blank:,} {side} row(s) have a blank {column}; secondary keys must match, so these rows cannot be matched by key.",
                                     side=side, column=column, count=blank))
    for item in passes:
        for side, amount_field, date_field in (("source", "amount_source", "date_source"), ("destination", "amount_destination", "date_destination")):
            df = frames[side]
            amount = normalize_header(item[amount_field])
            unusable = int(df[amount].map(lambda value: to_number(value) is None).sum())
            if unusable:
                issues.append(_issue("warning", "pass_amount",
                                     f"{unusable:,} {side} row(s) have no usable {amount}; the amount/date pass skips them.",
                                     side=side, column=amount, count=unusable))
            if item.get(date_field):
                date_columns[side].add(normalize_header(item[date_field]))

    for side, columns in date_columns.items():
        for column in sorted(columns):
            issues.extend(_check_dates(frames[side], column, side, dayfirst))
    return issues


def run_precheck(db: Session, session_id: str, plan: ReconciliationPlan) -> dict[str, Any]:
    from app.api.routes.analysis import _load_and_consolidate
    from app.schemas.reconciliation import FileSource

    results = []
    for rule in plan.execution_rules():
        label = rule.get("report_label") or rule["sheet_rule_id"]
        entry: dict[str, Any] = {
            "file_pair_id": rule["file_pair_id"],
            "sheet_rule_id": rule["sheet_rule_id"],
            "label": label,
            "date_format": "day_first" if rule.get("date_dayfirst", True) else "month_first",
        }
        try:
            sources_1 = [FileSource(**item) for item in rule["source_files_1"]]
            sources_2 = [FileSource(**item) for item in rule["source_files_2"]]
            with use_date_convention(bool(rule.get("date_dayfirst", True))), use_normalizer(build_normalizer(rule.get("normalization"))):
                df1 = _load_and_consolidate(db, session_id, sources_1)
                df2 = _load_and_consolidate(db, session_id, sources_2)
                for df in (df1, df2):
                    if "_ROW_NO" not in df.columns:
                        df["_ROW_NO"] = df.index + 2
                df1 = apply_transformations(df1, rule.get("transformations"), "source").df
                df2 = apply_transformations(df2, rule.get("transformations"), "destination").df
                entry["source_rows"], entry["destination_rows"] = len(df1), len(df2)
                entry["issues"] = _check_rule(df1, df2, rule)
        except ValueError as exc:
            entry["issues"] = [_issue("blocker", "configuration", str(exc))]
        results.append(entry)

    counts = {"blocker": 0, "warning": 0, "info": 0}
    for entry in results:
        for issue in entry["issues"]:
            counts[issue["severity"]] += 1
    status = "blockers" if counts["blocker"] else ("warnings" if counts["warning"] else "ok")
    return {"status": status, "summary": counts, "rules": results}


def precheck_summary(result: dict[str, Any]) -> dict[str, Any]:
    """The compact form stored with the job and the audit event."""
    return {
        "status": result["status"],
        **result["summary"],
        "issues": [
            {"rule": entry["sheet_rule_id"], "severity": issue["severity"], "category": issue["category"], "count": issue["count"]}
            for entry in result["rules"]
            for issue in entry["issues"]
            if issue["severity"] != "info"
        ][:100],
    }


def iter_issues(result: dict[str, Any]) -> Iterable[dict]:
    for entry in result["rules"]:
        yield from entry["issues"]
