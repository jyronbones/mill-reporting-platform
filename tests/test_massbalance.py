"""Mass-balance reconciliation must flag EXACTLY the injected shifts."""
from __future__ import annotations

import pandas as pd
from sqlalchemy import select

from mill.schema import dq_results, fact_shift_summary


def test_reconciliation_flags_exactly_injected_shifts(pipeline):
    faults = pipeline["faults"]
    injected = set(
        int(k) for k in faults.loc[faults["fault_type"] == "mass_balance", "shift_key"]
    )
    assert injected, "test config should inject at least one mass-balance fault"

    with pipeline["engine"].connect() as conn:
        dq = pd.read_sql(select(dq_results).where(dq_results.c.rule == "mass_balance"), conn)
    detected = set(int(k) for k in dq["shift_key"])

    assert detected == injected, (
        f"reconciliation mismatch: only-injected={injected - detected}, "
        f"only-detected={detected - injected}"
    )


def test_clean_shifts_balance_within_tolerance(pipeline):
    """Non-injected shifts should reconcile: feed ~= recovered + tails."""
    faults = pipeline["faults"]
    injected = set(
        int(k) for k in faults.loc[faults["fault_type"] == "mass_balance", "shift_key"]
    )
    tol = pipeline["cfg"].reconciliation["tolerance_pct"] / 100.0

    with pipeline["engine"].connect() as conn:
        df = pd.read_sql(select(fact_shift_summary), conn)

    clean = df[~df["shift_key"].isin(injected)].dropna(subset=["gold_in_feed_oz"])
    clean = clean[clean["gold_in_feed_oz"] > 0]
    rel = (
        (clean["gold_in_feed_oz"] - (clean["gold_recovered_oz"] + clean["gold_in_tails_oz"])).abs()
        / clean["gold_in_feed_oz"]
    )
    assert (rel <= tol).all(), f"clean shifts exceeding tolerance: {rel[rel > tol].tolist()}"
