import pandas as pd
from typing import List, Dict, Any
from app.reconciliation_engine.schema.semantic_mapper import semantic_map_columns

def consolidate_dataframes(dfs_with_metadata: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    Consolidates multiple dataframes into a single unified dataframe.
    Input format:
    [
        {
            "df": pd.DataFrame,
            "filename": "Books_Jan.xlsx",
            "sheet_name": "Sheet1"
        },
        ...
    ]
    """
    if not dfs_with_metadata:
        return pd.DataFrame()

    # The first dataframe's schema is treated as the master schema
    master_df_info = dfs_with_metadata[0]
    master_df = master_df_info["df"].copy()
    master_columns = list(master_df.columns)
    
    # Add lineage columns to master
    master_df["__SOURCE_FILE__"] = master_df_info["filename"]
    master_df["__SOURCE_SHEET__"] = master_df_info["sheet_name"]
    master_df["__SOURCE_ROW__"] = master_df.index + 2 # Assuming Excel rows (1-indexed + header)

    consolidated_df = master_df
    
    for df_info in dfs_with_metadata[1:]:
        df = df_info["df"].copy()
        current_columns = list(df.columns)
        
        # Map current columns to master columns
        mappings = semantic_map_columns(current_columns, master_columns)
        
        rename_dict = {}
        for mapping in mappings:
            # We only automatically rename if confidence is High or Medium
            if mapping["target"] and mapping["confidence"] in ["High", "Medium"]:
                rename_dict[mapping["source"]] = mapping["target"]
                
        df.rename(columns=rename_dict, inplace=True)
        
        # Add lineage
        df["__SOURCE_FILE__"] = df_info["filename"]
        df["__SOURCE_SHEET__"] = df_info["sheet_name"]
        df["__SOURCE_ROW__"] = df.index + 2
        
        # Concat
        consolidated_df = pd.concat([consolidated_df, df], ignore_index=True)

    return consolidated_df
