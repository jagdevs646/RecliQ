import pandas as pd
from typing import List, Dict, Any

def analyze_keys(df1: pd.DataFrame, df2: pd.DataFrame) -> Dict[str, Any]:
    """
    Analyzes columns in both dataframes to recommend the best matching key.
    Checks uniqueness, missing values, and overlap.
    """
    common_cols = set(df1.columns).intersection(set(df2.columns))
    # Remove lineage columns
    common_cols = {col for col in common_cols if not col.startswith("__")}
    
    candidates = []
    
    for col in common_cols:
        # Check completeness (non-null ratio)
        comp1 = df1[col].notna().mean()
        comp2 = df2[col].notna().mean()
        
        # We need highly complete columns
        if comp1 < 0.9 or comp2 < 0.9:
            continue
            
        # Check uniqueness
        uniq1 = df1[col].nunique() / len(df1) if len(df1) > 0 else 0
        uniq2 = df2[col].nunique() / len(df2) if len(df2) > 0 else 0
        
        # Check overlap
        set1 = set(df1[col].dropna().astype(str).str.lower().str.strip())
        set2 = set(df2[col].dropna().astype(str).str.lower().str.strip())
        overlap = len(set1.intersection(set2))
        max_possible = min(len(set1), len(set2))
        overlap_ratio = overlap / max_possible if max_possible > 0 else 0
        
        # Calculate a crude score
        score = (uniq1 + uniq2) / 2 * 0.4 + (comp1 + comp2) / 2 * 0.2 + overlap_ratio * 0.4
        
        is_date = "date" in str(col).lower()
        
        candidates.append({
            "column": col,
            "uniqueness": (uniq1 + uniq2) / 2,
            "completeness": (comp1 + comp2) / 2,
            "overlap": overlap_ratio,
            "score": score,
            "is_date": is_date
        })
        
    candidates.sort(key=lambda x: x["score"], reverse=True)
    
    if not candidates:
        return {"recommended_key": None, "confidence": 0, "alternatives": []}
        
    best = candidates[0]
    
    # If the best key is a date and uniqueness is low, suggest a composite key
    if best["is_date"] and best["uniqueness"] < 0.8:
        # Find a good numeric column for a composite key
        numeric_cols = [c for c in common_cols if pd.api.types.is_numeric_dtype(df1[c]) and c != best["column"]]
        if numeric_cols:
            composite_col = numeric_cols[0] # Just picking the first numeric for simplicity
            return {
                "recommended_key": [best["column"], composite_col],
                "confidence": int(best["score"] * 100),
                "is_composite": True,
                "reason": f"{best['column']} has duplicates. Recommended combining with {composite_col}.",
                "alternatives": candidates[1:4]
            }
            
    return {
        "recommended_key": [best["column"]],
        "confidence": int(best["score"] * 100),
        "is_composite": False,
        "alternatives": candidates[1:4]
    }
