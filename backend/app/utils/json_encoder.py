from __future__ import annotations

import json
import math
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np
import pandas as pd


def default_json_serializer(obj: Any) -> Any:
    """Safe default serializer for objects that Python's standard json module cannot serialize."""
    if obj is None:
        return None

    # Array / DataFrame / Series collections first
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, pd.DataFrame):
        return obj.to_dict(orient="records")
    if isinstance(obj, pd.Series):
        return obj.tolist()
    if isinstance(obj, (set, frozenset)):
        return list(obj)

    # Date / Time / Timestamps
    if isinstance(obj, (pd.Timestamp, datetime)):
        if pd.isna(obj):
            return None
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, time):
        return obj.isoformat()
    if isinstance(obj, pd.Timedelta):
        return str(obj)

    # Numpy numeric / boolean types
    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return float(obj)

    # Scalar NaT / NaN
    try:
        if pd.isna(obj):
            return None
    except (ValueError, TypeError):
        pass

    # Other common types
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (UUID, Path)):
        return str(obj)

    try:
        return str(obj)
    except Exception:
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def clean_for_json(obj: Any) -> Any:
    """Recursively clean any data structure to ensure pure, safe JSON serialization."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {str(k): clean_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean_for_json(item) for item in obj]
    if isinstance(obj, (set, frozenset)):
        return [clean_for_json(item) for item in obj]
    if isinstance(obj, np.ndarray):
        return [clean_for_json(item) for item in obj.tolist()]
    if isinstance(obj, pd.DataFrame):
        return clean_for_json(obj.to_dict(orient="records"))
    if isinstance(obj, pd.Series):
        return clean_for_json(obj.tolist())

    if isinstance(obj, (pd.Timestamp, datetime)):
        if pd.isna(obj):
            return None
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, time):
        return obj.isoformat()
    if isinstance(obj, pd.Timedelta):
        return str(obj)

    if isinstance(obj, (bool, np.bool_)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return float(obj)

    try:
        if pd.isna(obj):
            return None
    except (ValueError, TypeError):
        pass

    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (UUID, Path)):
        return str(obj)

    return default_json_serializer(obj)


def safe_json_dumps(obj: Any, **kwargs: Any) -> str:
    """Serialize obj to a JSON formatted str safely."""
    cleaned = clean_for_json(obj)
    if "default" not in kwargs:
        kwargs["default"] = default_json_serializer
    return json.dumps(cleaned, **kwargs)


def safe_json_dump(obj: Any, fp: Any, **kwargs: Any) -> None:
    """Serialize obj as a JSON formatted stream to fp safely."""
    cleaned = clean_for_json(obj)
    if "default" not in kwargs:
        kwargs["default"] = default_json_serializer
    json.dump(cleaned, fp, **kwargs)
