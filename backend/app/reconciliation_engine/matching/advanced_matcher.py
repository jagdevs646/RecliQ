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
