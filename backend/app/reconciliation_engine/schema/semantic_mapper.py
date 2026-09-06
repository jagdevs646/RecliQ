from typing import List, Dict, Tuple, Any
from rapidfuzz import process, fuzz
import re

# Common synonym mappings in accounting / reconciliation
SYNONYMS = {
    "invoice number": ["document number", "invoice no", "inv no", "reference number", "ref no", "transaction id"],
    "invoice date": ["posting date", "doc date", "transaction date", "date"],
    "vendor name": ["supplier", "vendor", "party name", "counterparty", "merchant"],
    "taxable value": ["base amount", "net amount", "taxable amount", "subtotal", "gross amount"],
    "gst": ["tax amount", "tax", "vat", "igst", "cgst", "sgst"],
    "total book value": ["gross amount", "total amount", "total", "invoice value", "amount"]
}


def normalize_column_name_for_mapping(col: str) -> str:
    """Normalize a column name for semantic comparison."""
    # Convert to lowercase and replace underscores/hyphens with space
    normalized = col.lower().replace("_", " ").replace("-", " ")
    # Remove special characters
    normalized = re.sub(r'[^a-z0-9\s]', '', normalized)
    # Remove extra spaces
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized


def get_synonyms(normalized_col: str) -> List[str]:
    """Get all synonyms for a given normalized column name."""
    syns = [normalized_col]
    for key, values in SYNONYMS.items():
        if normalized_col == key or normalized_col in values:
            syns.append(key)
            syns.extend(values)
    return list(set(syns))


def semantic_map_columns(source_columns: List[str], target_columns: List[str]) -> List[Dict[str, Any]]:
    """
    Map columns from source to target based on semantic similarity and string matching.
    Returns a list of mappings with confidence scores.
    """
    mappings = []
    
    # Keep track of already mapped target columns to avoid duplicates for high confidence
    mapped_targets = set()

    for src in source_columns:
        norm_src = normalize_column_name_for_mapping(src)
        src_synonyms = get_synonyms(norm_src)
        
        best_match = None
        best_score = 0
        
        for tgt in target_columns:
            if tgt in mapped_targets:
                continue
                
            norm_tgt = normalize_column_name_for_mapping(tgt)
            tgt_synonyms = get_synonyms(norm_tgt)
            
            # Check for direct synonym intersection
            if set(src_synonyms).intersection(set(tgt_synonyms)):
                score = 100
            else:
                # Use rapidfuzz for string similarity
                score = fuzz.token_set_ratio(norm_src, norm_tgt)
                
            if score > best_score:
                best_score = score
                best_match = tgt
                
        if best_match and best_score >= 60: # Threshold for considering a match
            confidence = "High" if best_score >= 90 else "Medium" if best_score >= 75 else "Low"
            
            mappings.append({
                "source": src,
                "target": best_match,
                "score": best_score,
                "confidence": confidence
            })
            
            if confidence == "High":
                mapped_targets.add(best_match)
        else:
            mappings.append({
                "source": src,
                "target": None,
                "score": 0,
                "confidence": "None"
            })
            
    return mappings
