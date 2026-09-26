import pandas as pd
from typing import List, Dict, Any, Tuple


def find_duplicates(df: pd.DataFrame, key_cols: List[str]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Finds duplicates in a dataframe based on key columns.
    Returns (deduplicated_df, duplicates_df)
    """
    if not key_cols:
        return df, pd.DataFrame()
        
    duplicates_mask = df.duplicated(subset=key_cols, keep=False)
    duplicates_df = df[duplicates_mask].copy()
    
    # We keep the first occurrence in the deduped df, but mark it if it had duplicates
    deduped_df = df.drop_duplicates(subset=key_cols, keep='first').copy()
    
    return deduped_df, duplicates_df


def consolidate_duplicate_keys(df: pd.DataFrame, key_cols: List[str], amount_cols: List[str]) -> pd.DataFrame:
    """Merge rows that share a complete key into one record before matching.

    Several invoices booked under the same key (e.g. ID + vendor) reconcile as
    one consolidated record: amount columns are summed, other columns keep the
    first row's value, and ``__GROUP_COUNT__`` / ``__GROUPED_ROWS__`` record
    which source rows were combined. Rows with a blank key component are never
    merged, because a blank is not an identity. Unlike ``find_duplicates``,
    no row is dropped.
    """
    df = df.copy()
    df["__GROUP_COUNT__"] = 1
    df["__GROUPED_ROWS__"] = df["_ROW_NO"].astype(str) if "_ROW_NO" in df.columns else ""
    key_cols = [column for column in key_cols if column in df.columns]
    if not key_cols or df.empty:
        return df

    keys = df[key_cols].astype(str).apply(lambda column: column.str.strip())
    complete = ~(df[key_cols].isna().any(axis=1) | keys.isin(["", "nan", "None", "NaT"]).any(axis=1))
    repeated = complete & keys.duplicated(keep=False)
    if not repeated.any():
        return df

    from app.reconciliation_engine.cache import to_number

    groups = df[repeated].copy()
    group_ids = keys[repeated].apply(tuple, axis=1)
    for column in amount_cols:
        if column in groups.columns and column not in key_cols:
            groups[column] = groups[column].map(to_number)
    aggregations = {column: "first" for column in groups.columns if column not in {"__GROUP_COUNT__", "__GROUPED_ROWS__"}}
    for column in amount_cols:
        if column in groups.columns and column not in key_cols:
            aggregations[column] = lambda values: values.sum(min_count=1)
    if "_ROW_NO" in groups.columns:
        aggregations["_ROW_NO"] = "min"
    grouped = groups.groupby(group_ids, sort=False).agg(aggregations)
    grouped["__GROUP_COUNT__"] = groups.groupby(group_ids, sort=False).size()
    if "_ROW_NO" in groups.columns:
        grouped["__GROUPED_ROWS__"] = groups.groupby(group_ids, sort=False)["_ROW_NO"].agg(
            lambda rows: ", ".join(str(row) for row in rows)
        )
    merged = pd.concat([df[~repeated], grouped.reset_index(drop=True)], ignore_index=True)
    return merged.sort_values("_ROW_NO", kind="stable").reset_index(drop=True) if "_ROW_NO" in merged.columns else merged


def group_for_many_to_one(df: pd.DataFrame, key_cols: List[str], amount_cols: List[str]) -> pd.DataFrame:
    """
    Groups records for one-to-many/many-to-one reconciliation.
    """
    if not key_cols:
        return df
        
    agg_dict = {col: "sum" for col in amount_cols if col in df.columns}
    non_agg = [col for col in df.columns if col not in amount_cols and col not in key_cols]
    agg_dict.update({col: "first" for col in non_agg})
    
    grouped = df.groupby(key_cols, dropna=False).agg(agg_dict).reset_index()
    
    # Calculate how many records were grouped
    counts = df.groupby(key_cols, dropna=False).size().reset_index(name='__GROUP_COUNT__')
    
    merged = pd.merge(grouped, counts, on=key_cols, how='left')
    return merged
