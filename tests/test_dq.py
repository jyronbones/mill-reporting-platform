"""The DQ engine must catch every seeded fault type."""
from __future__ import annotations

import pandas as pd
from sqlalchemy import select

from mill.schema import dq_results

# injected fault_type -> dq rule that should surface it
FAULT_TO_RULE = {
    "out_of_range": "range_check",
    "stuck_sensor": "stuck_sensor",
    "gap": "gap",
    "duplicate": "duplicate",
    "bad_flag": "bad_quality_flag",
    "mass_balance": "mass_balance",
}


def _dq(pipeline) -> pd.DataFrame:
    with pipeline["engine"].connect() as conn:
        return pd.read_sql(select(dq_results), conn)


def test_every_fault_family_was_injected(pipeline):
    injected = set(pipeline["faults"]["fault_type"].unique())
    assert injected == set(FAULT_TO_RULE), f"missing fault families: {set(FAULT_TO_RULE) - injected}"


def test_every_fault_family_is_detected(pipeline):
    counts = pipeline["dq_counts"]
    for fault, rule in FAULT_TO_RULE.items():
        assert counts.get(rule, 0) > 0, f"{fault} not detected by rule {rule}"


def test_detections_cover_injections(pipeline):
    """1:1 fault families: at least as many detections as injections."""
    faults = pipeline["faults"]
    counts = pipeline["dq_counts"]
    for fault in ["out_of_range", "stuck_sensor", "gap", "duplicate"]:
        n_inj = int((faults["fault_type"] == fault).sum())
        assert counts[FAULT_TO_RULE[fault]] >= n_inj


def test_range_check_flags_known_tags(pipeline):
    dq = _dq(pipeline)
    rng = dq[dq["rule"] == "range_check"]
    # out_of_range faults are seeded only on recovery_pct and mill_power_kw.
    assert set(rng["tag_name"].unique()).issubset({"recovery_pct", "mill_power_kw"})


def test_stuck_only_on_non_balance_tags(pipeline):
    dq = _dq(pipeline)
    stuck_tags = set(dq[dq["rule"] == "stuck_sensor"]["tag_name"].unique())
    # never on tags that enter the gold balance
    assert stuck_tags.isdisjoint({"feed_throughput_tph", "feed_grade_gpt", "recovery_pct", "tails_grade_gpt"})


def test_severities_present(pipeline):
    dq = _dq(pipeline)
    assert {"Error", "Warning"}.issubset(set(dq["severity"].unique()))
