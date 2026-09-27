from __future__ import annotations

from datetime import datetime, timezone

def build_universal_data_model(
    job_type: str,
    file_1_name: str,
    file_2_name: str,
    matching_keys: list[str],
    reconciliation_results: list[dict],
    file_1_not_found: list[dict],
    file_2_not_found: list[dict],
    matched_records: list[dict],
    total_file_1: int,
    total_file_2: int,
    identity_resolution: list[dict] | None = None,
    secondary_keys: list[str] | None = None,
    compared_fields: list[str] | None = None,
    extra_metadata: dict | None = None,
) -> dict:

    # 1. One difference row per mismatched field. Records are identified by
    # their primary (and secondary) key values, so no synthetic IDs are added.
    exceptions = []

    exc_type_counts = {}
    field_mismatch_counts = {}
    field_match_counts = {}
    has_critical = False

    # Analyze reconciliation_results (which contains mismatches)
    for row in reconciliation_results:
        match_key = str(
            row.get(
                "COMPOSITE MATCH KEY",
                row.get("MATCH KEY", row.get(matching_keys[0], "") if matching_keys else ""),
            )
        )
        matched_key = row.get("MATCHED COMPOSITE KEY", row.get("MATCHED KEY"))
        record_context = {"Primary Key": match_key}
        if matched_key is not None:
            record_context["Matched Key"] = str(matched_key)
        if row.get("SECONDARY KEY"):
            record_context["Secondary Key"] = row["SECONDARY KEY"]
        trailing_context = {"Grouped Rows": row["GROUPED ROWS"]} if row.get("GROUPED ROWS") else {}
        
        # Determine specific field differences
        # Keys typically look like "FIELD (FILE 1)", "FIELD (FILE 2)", "FIELD DIFF", "FIELD STATUS"
        fields_processed = set()

        for k in row.keys():
            if k.endswith(" (FILE 1)"):
                base_field = k.replace(" (FILE 1)", "")
                if base_field in fields_processed or base_field == "ROW":
                    continue
                
                f1_val = row.get(f"{base_field} (FILE 1)")
                f2_val = row.get(f"{base_field} (FILE 2)")
                status = row.get(f"{base_field} STATUS", "Mismatch")
                diff = row.get(f"{base_field} DIFF")
                
                if status == "Mismatch" or status == "Partial Match" or str(status).endswith("%"):
                    fields_processed.add(base_field)
                    
                    # Update field stats
                    field_mismatch_counts[base_field] = field_mismatch_counts.get(base_field, 0) + 1
                    
                    exc_type = "Value Difference" if diff is not None else "Text Difference"
                    if diff is not None and abs(float(diff)) > 1000:
                        has_critical = True
                        
                    exc_type_counts[exc_type] = exc_type_counts.get(exc_type, 0) + 1
                    
                    diff_pct = None
                    if diff is not None and f2_val is not None:
                        try:
                            f2_num = float(f2_val)
                            if f2_num != 0:
                                diff_pct = float(diff) / f2_num
                        except (ValueError, TypeError):
                            diff_pct = None
                        
                    exceptions.append({
                        **record_context,
                        "Field": base_field,
                        "File 1 Value": f1_val,
                        "File 2 Value": f2_val,
                        "Difference": diff,
                        "Difference %": diff_pct,
                        **trailing_context,
                    })
                else:
                    field_match_counts[base_field] = field_match_counts.get(base_field, 0) + 1
    
    # Add missing records to exception summary
    if file_1_not_found:
        exc_type_counts["Missing in File 2"] = len(file_1_not_found)
    if file_2_not_found:
        exc_type_counts["Missing in File 1"] = len(file_2_not_found)

    exception_summary = [
        {"Exception Type": k, "Count": v, "Impact": ""} 
        for k, v in exc_type_counts.items()
    ]
    
    field_exception_summary = []
    all_fields = set(field_mismatch_counts.keys()).union(set(field_match_counts.keys()))
    # Estimate total matches for a field based on fully matched records
    fully_matched_count = len(matched_records)
    
    for f in all_fields:
        mismatches = field_mismatch_counts.get(f, 0)
        # Matches = matches within reconciliation_results + all fully matched records
        matches = field_match_counts.get(f, 0) + fully_matched_count
        total = matches + mismatches
        match_pct = (matches / total) if total > 0 else 0
        field_exception_summary.append({
            "Field": f,
            "Matched": matches,
            "Mismatch": mismatches,
            "Match %": match_pct
        })
    
    # 2. Overall Status
    overall_status = "PASSED"
    if exceptions or file_1_not_found or file_2_not_found:
        overall_status = "EXCEPTIONS FOUND"
        if has_critical:
            overall_status = "CRITICAL EXCEPTIONS"
            
    # 3. Control Checks
    control_checks = [
        {
            "Control": "Record Count", 
            "File 1": total_file_1, 
            "File 2": total_file_2, 
            "Result": "Pass" if total_file_1 == total_file_2 else "Exception"
        },
        {
            "Control": "Missing Records (File 1)",
            "File 1": len(file_2_not_found),
            "File 2": "-",
            "Result": "Exception" if file_2_not_found else "Pass"
        },
        {
            "Control": "Missing Records (File 2)",
            "File 1": "-",
            "File 2": len(file_1_not_found),
            "Result": "Exception" if file_1_not_found else "Pass"
        }
    ]

    identity_resolution = identity_resolution or []
    identity_counts = {
        classification: sum(
            record.get("IDENTITY CLASSIFICATION") == classification
            for record in identity_resolution
        )
        for classification in ("EXACT_MATCH", "EXCEPTION_MATCH", "AMBIGUOUS_MATCH", "NOT_FOUND")
    }

    return {
        "metadata": {
            "reconciliation_name": "Generic Reconciliation" if job_type == "generic" else "GST Reconciliation",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "file_1_name": file_1_name,
            "file_2_name": file_2_name,
            "matching_keys": matching_keys,
            "secondary_keys": secondary_keys or [],
            "compared_fields": compared_fields or [],
            **(extra_metadata or {}),
        },
        "statistics": {
            "total_file_1": total_file_1,
            "total_file_2": total_file_2,
            "matched": fully_matched_count,
            "mismatched": len(reconciliation_results),
            "missing_in_file_1": len(file_2_not_found),
            "missing_in_file_2": len(file_1_not_found),
            "identity": identity_counts,
        },
        "overall_status": overall_status,
        "exception_summary": exception_summary,
        "field_exception_summary": field_exception_summary,
        "exceptions": exceptions,
        "matched_records": matched_records,
        "missing_in_file_1": file_2_not_found,  # Items in file 2 not in file 1
        "missing_in_file_2": file_1_not_found,  # Items in file 1 not in file 2
        "identity_resolution": identity_resolution,
        "control_checks": control_checks,
    }
