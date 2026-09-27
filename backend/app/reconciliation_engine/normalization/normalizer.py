import pandas as pd

from app.reconciliation_engine.cache import parse_date_value


def normalize_date_series(series: pd.Series) -> pd.Series:
    """Normalize a series of dates into a standard canonical format YYYY-MM-DD."""
    normalized = series.map(parse_date_value)  # Honors the rule's date convention.
    return normalized.map(lambda value: value.isoformat() if value is not None else None).fillna(series.astype(str))


def normalize_text_series(series: pd.Series) -> pd.Series:
    """Normalize text (lowercase, strip extra spaces)."""
    return series.astype(str).str.lower().str.replace(r'\s+', ' ', regex=True).str.strip()


def normalize_identifier_series(series: pd.Series) -> pd.Series:
    """
    Normalize identifiers by removing common safe separators: - / | and spaces.
    E.g. ABC-123, ABC/123, ABC 123 -> ABC123.
    """
    # First, convert to uppercase string
    s = series.astype(str).str.upper()
    # Remove -, /, |, \ and spaces
    return s.str.replace(r'[\-\/\|\\\s]', '', regex=True)


def normalize_number_series(series: pd.Series) -> pd.Series:
    """Normalize numbers (remove commas, currency symbols)."""
    if pd.api.types.is_numeric_dtype(series):
        return series
        
    # Remove currency symbols and commas
    s = series.astype(str).str.replace(r'[^\d\.\-]', '', regex=True)
    return pd.to_numeric(s, errors='coerce').fillna(0.0)


def normalize_dataframe(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Applies normalization to specified columns, creating new NORM_ prefixed columns.
    config = {
        "date_columns": ["Invoice Date"],
        "text_columns": ["Vendor"],
        "id_columns": ["Invoice No"],
        "number_columns": ["Amount"]
    }
    """
    df_norm = df.copy()
    
    for col in config.get("date_columns", []):
        if col in df_norm.columns:
            df_norm[f"NORM_{col}"] = normalize_date_series(df_norm[col])
            
    for col in config.get("text_columns", []):
        if col in df_norm.columns:
            df_norm[f"NORM_{col}"] = normalize_text_series(df_norm[col])
            
    for col in config.get("id_columns", []):
        # Date keys are normalized as dates, not compacted identifiers. A date
        # column can be both a configured primary key and a date field.
        if col in df_norm.columns and col not in config.get("date_columns", []):
            df_norm[f"NORM_{col}"] = normalize_identifier_series(df_norm[col])
            
    for col in config.get("number_columns", []):
        if col in df_norm.columns:
            df_norm[f"NORM_{col}"] = normalize_number_series(df_norm[col])
            
    return df_norm
