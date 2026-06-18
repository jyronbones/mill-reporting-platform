"""KPI formula correctness on known inputs + warehouse sanity checks."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from mill.warehouse import kpi
from mill.schema import dim_tag, fact_process_readings

I_H = 1.0 / 60.0  # one-minute samples expressed in hours


def test_tonnes_milled():
    assert kpi.tonnes_milled(np.array([600.0, 600.0]), I_H) == pytest.approx(20.0)


def test_gold_oz_known_value():
    # 60 t/h for 1 h at 10 g/t = 600 g = 19.29 oz
    assert kpi.gold_oz(np.array([60.0]), np.array([10.0]), 1.0) == \
        pytest.approx(600.0 / 31.1034768)


def test_recovered_oz():
    assert kpi.recovered_oz(np.array([60.0]), np.array([10.0]), np.array([90.0]), 1.0) == \
        pytest.approx(600.0 * 0.9 / 31.1034768)


def test_weighted_mean():
    assert kpi.weighted_mean(np.array([90.0, 80.0]), np.array([100.0, 300.0])) == pytest.approx(82.5)


def test_specific_energy():
    # 9000 kW for 2 minutes = 300 kWh over 20 t = 15 kWh/t
    assert kpi.specific_energy(np.array([9000.0, 9000.0]), 20.0, I_H) == pytest.approx(15.0)


def test_availability_and_utilization():
    assert kpi.availability_pct(600, 720) == pytest.approx(600 / 720 * 100)
    assert kpi.utilization_pct(700.0, 750.0) == pytest.approx(700 / 750 * 100)


def test_oee_composite():
    assert kpi.oee_pct(90.0, 90.0, 98.0) == pytest.approx(90 * 90 * 98 / 10_000.0)


def test_reagent_per_tonne_is_tonnage_weighted():
    val = kpi.reagent_per_tonne(np.array([0.4, 0.6]), np.array([100.0, 300.0]))
    assert val == pytest.approx((0.4 * 100 + 0.6 * 300) / 400)


# --- warehouse sanity -----------------------------------------------------
from mill.schema import fact_shift_summary  # noqa: E402


def _summary(pipeline) -> pd.DataFrame:
    with pipeline["engine"].connect() as conn:
        return pd.read_sql(select(fact_shift_summary), conn)


def _readings_wide(pipeline) -> pd.DataFrame:
    eng = pipeline["engine"]
    q = select(
        fact_process_readings.c.timestamp,
        dim_tag.c.tag_name,
        fact_process_readings.c.value,
    ).select_from(
        fact_process_readings.join(dim_tag, fact_process_readings.c.tag_key == dim_tag.c.tag_key)
    )
    with eng.connect() as conn:
        df = pd.read_sql(q, conn)
    return df.pivot_table(index="timestamp", columns="tag_name", values="value", aggfunc="first")


def test_recovery_in_realistic_band(pipeline):
    df = _summary(pipeline)
    rec = df["avg_recovery_pct"].dropna()
    assert len(rec) > 0
    # Simulator clamps clean recovery to [80, 97]; tonnage weighting keeps the
    # shift mean comfortably inside a realistic gold-plant band.
    assert rec.between(80, 98).mean() > 0.9


def test_tonnes_and_gold_positive(pipeline):
    df = _summary(pipeline)
    assert (df["tonnes_milled"] > 0).all()
    assert (df["gold_poured_oz"] >= 0).all()
    assert (df["gold_in_feed_oz"] > 0).all()


def test_recovery_tracks_grade_and_grind(pipeline):
    """The built-in correlations are strongest at the reading grain.

    (At shift grain, grade noise washes them out — these relationships live in
    the per-minute physics, which is what the recovery-vs-grade scatter plots.)
    """
    w = _readings_wide(pipeline)[
        ["recovery_pct", "feed_grade_gpt", "grind_p80_um", "feed_throughput_tph"]
    ].dropna()
    # Higher head grade -> higher recovery.
    assert w["recovery_pct"].corr(w["feed_grade_gpt"]) > 0.1
    # Finer grind (lower P80) -> higher recovery.
    assert w["recovery_pct"].corr(w["grind_p80_um"]) < -0.1
    # Pushing throughput -> lower recovery.
    assert w["recovery_pct"].corr(w["feed_throughput_tph"]) < 0
