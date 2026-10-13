from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PERF_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "perf"
sys.path.insert(0, str(PERF_SCRIPTS))

import bench


def test_keyed_narration_scenario_has_different_text_with_numbers_on_both_sides() -> None:
    source, destination = bench.scenario_frames(3, "keyed_numeric_narration", pd.DataFrame(), pd.DataFrame())

    assert source["KEY"].tolist() == destination["KEY"].tolist()
    assert all(left != right for left, right in zip(source["NARRATION"], destination["DESCRIPTION"]))
    assert all(any(char.isdigit() for char in value) for value in source["NARRATION"])
    assert all(any(char.isdigit() for char in value) for value in destination["DESCRIPTION"])


def test_keyless_scenario_uses_percent_band_and_matching_narrative() -> None:
    source, destination = bench.scenario_frames(10, "keyless_percent_narrative", pd.DataFrame(), pd.DataFrame())
    config = bench.SCENARIOS["keyless_percent_narrative"]["matching_passes"][0]

    assert config["type"] == "amount_tolerance"
    assert config["amount_tolerance_percent"] > 0
    assert config["narrative_source"] == "NARRATIVE"
    assert config["narrative_destination"] == "DESCRIPTION"
    assert source["NARRATIVE"].tolist() == destination["DESCRIPTION"].tolist()
    assert all(
        abs(left - right) <= max(abs(left) * config["amount_tolerance_percent"] / 100, 0.005)
        for left, right in zip(source["AMOUNT"], destination["AMOUNT"])
    )
