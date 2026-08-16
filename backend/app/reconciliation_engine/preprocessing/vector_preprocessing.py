from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from app.reconciliation_engine.cache import (
    is_blank,
    normalize_header,
    to_number,
)


def normalize_headers(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy(deep=False)
    df.columns = [normalize_header(col) for col in df.columns]
    return df


def is_horizontal_orientation(orientation: str) -> bool:
    return str(orientation).lower().startswith("horizontal")


def transform_horizontal_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    work = df.dropna(how="all").copy()
    if work.empty:
        return work

    first_col = work.columns[0]
    work = work[work[first_col].notna()].copy()
    work[first_col] = work[first_col].astype(str).str.strip()
    work = work[work[first_col] != ""]

    if work.empty:
        return work

    transposed = work.set_index(first_col).T.reset_index(drop=True)
    transposed.columns = [normalize_header(col) for col in transposed.columns]
    return transposed


def prepare_dataframe(df: pd.DataFrame, orientation: str = "vertical") -> pd.DataFrame:
    if is_horizontal_orientation(orientation):
        df = transform_horizontal_dataframe(df)
    return normalize_headers(df)


def fields_label(fields: list[str]) -> str:
    return ",".join(fields)


def normalize_fields(fields: Iterable[str]) -> list[str]:
    return [normalize_header(field).replace('"', "") for field in fields if normalize_header(field).replace('"', "")]


def normalise_gst_df(df: pd.DataFrame, amount_columns: list[str]) -> pd.DataFrame:
    df.columns = [str(c).upper().strip().replace("\n", "").replace("\r", "") for c in df.columns]
    for col in amount_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    if "INVOICE DATE" in df.columns:
        df["INVOICE DATE"] = pd.to_datetime(df["INVOICE DATE"], errors="coerce")
    for col in ("GSTR", "INVOICE NO.", "NAME OF TRADER/FIRM/COMPANY"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.upper().str.strip()
    return df


def merge_duplicate_invoices(df: pd.DataFrame, merge_key_columns: list[str], amount_columns: list[str]) -> pd.DataFrame:
    key_cols = [col for col in merge_key_columns if col in df.columns]
    amt_cols = [col for col in amount_columns if col in df.columns]
    non_key_non_amount = [col for col in df.columns if col not in key_cols and col not in amt_cols]

    agg_dict = {col: "sum" for col in amt_cols}
    agg_dict.update({col: "first" for col in non_key_non_amount})

    merged = df.groupby(key_cols, sort=False, dropna=False).agg(agg_dict).reset_index()
    original_order = [col for col in df.columns if col in merged.columns]
    return merged[original_order]


def aggregate_by_key(df: pd.DataFrame, key_col: str) -> pd.DataFrame:
    if key_col not in df.columns:
        raise KeyError(key_col)

    df = df.copy(deep=False)
    agg = {}
    for col in df.columns:
        if col == key_col:
            continue
        sample = df[col].dropna()
        if not sample.empty and sample.map(lambda value: to_number(value) is not None).all():
            df[col] = df[col].apply(lambda x: to_number(x) or 0.0)
            agg[col] = "sum"
        else:
            agg[col] = "first"

    grouped = df.groupby(key_col, sort=False, dropna=False).agg(agg).reset_index()
    # Round numeric columns after sum
    for col, func in agg.items():
        if func == "sum":
            grouped[col] = grouped[col].round(2)
    return grouped[df.columns]


def read_excel_columns(path: Path, orientation: str = "vertical") -> list[str]:
    if is_horizontal_orientation(orientation):
        df = pd.read_excel(path)
        return list(prepare_dataframe(df, orientation=orientation).columns)
    df = pd.read_excel(path, nrows=0)
    return [normalize_header(col) for col in df.columns]
