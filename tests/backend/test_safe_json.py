import json
import math
import sys
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import pytest

from app.utils.json_encoder import default_json_serializer, safe_json_dump, safe_json_dumps


def test_safe_json_dumps_various_types():
    data = {
        "timestamp": pd.Timestamp("2026-08-21 18:45:00"),
        "nat": pd.NaT,
        "date": date(2026, 8, 21),
        "datetime": datetime(2026, 8, 21, 18, 45),
        "time": time(18, 45, 0),
        "timedelta": pd.Timedelta(days=1),
        "np_int64": np.int64(100),
        "np_float64": np.float64(45.5),
        "np_nan": np.nan,
        "np_bool": np.bool_(True),
        "np_array": np.array([1, 2, 3]),
        "set_data": {1, 2, 3},
        "decimal": Decimal("99.99"),
        "uuid": uuid4(),
        "path": Path("/test/path"),
    }

    result_str = safe_json_dumps(data)
    assert isinstance(result_str, str)
    
    parsed = json.loads(result_str)
    assert parsed["timestamp"] == "2026-08-21T18:45:00"
    assert parsed["nat"] is None
    assert parsed["date"] == "2026-08-21"
    assert parsed["np_int64"] == 100
    assert parsed["np_float64"] == 45.5
    assert parsed["np_nan"] is None
    assert parsed["np_bool"] is True
    assert parsed["np_array"] == [1, 2, 3]
    assert parsed["set_data"] == [1, 2, 3]
    assert parsed["decimal"] == 99.99


def test_safe_json_dump_file(tmp_path: Path):
    out_file = tmp_path / "test.json"
    data = {
        "ts": pd.Timestamp("2026-01-01"),
        "num": np.int32(10),
    }

    with open(out_file, "w", encoding="utf-8") as f:
        safe_json_dump(data, f)

    assert out_file.exists()
    with open(out_file, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded["ts"] == "2026-01-01T00:00:00"
    assert loaded["num"] == 10
