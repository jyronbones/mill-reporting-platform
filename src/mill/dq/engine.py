"""Data-quality rule engine.

Runs against the raw and warehouse layers and writes findings to ``dq_results``
(rule, tag, timestamp, shift, severity, detail). Rules:

  range_check        value outside dim_tag expected_min/max
  stuck_sensor       a tag frozen (identical value) over a long running run
  gap                missing timestamps (expected sample not present)
  duplicate          duplicate (tag, timestamp) in the historian
  bad_quality_flag   historian quality flag != Good
  quality_rollup     % Good per tag per shift below target (Warning)
  mass_balance       shift where feed gold != recovered + tails beyond tolerance

The set of seeded faults the simulator injects is exactly the set these rules
are designed to surface — see tests/test_dq.py and tests/test_massbalance.py.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
from sqlalchemy import Engine, delete, func, insert, select

from ..config import Config
from ..schema import (
    dim_tag,
    dim_time,
    dq_results,
    fact_process_readings,
    fact_shift_summary,
    raw_historian,
)
from ..simulator.flowsheet import CONTINUOUS_TAGS


def _row(rule, severity, detail, *, tag=None, ts=None, shift=None):
    return {
        "run_ts": datetime.now(),
        "rule": rule,
        "tag_name": tag,
        "timestamp": ts,
        "shift_key": shift,
        "severity": severity,
        "detail": detail[:300],
    }


def _wide_fact(engine: Engine) -> pd.DataFrame:
    q = (
        select(
            fact_process_readings.c.timestamp,
            dim_tag.c.tag_name,
            fact_process_readings.c.value,
            dim_time.c.shift_key,
        )
        .select_from(
            fact_process_readings.join(dim_tag, fact_process_readings.c.tag_key == dim_tag.c.tag_key)
            .join(dim_time, fact_process_readings.c.time_key == dim_time.c.time_key)
        )
    )
    df = pd.read_sql(q, engine)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


# ---------------------------------------------------------------------------
# Individual rules (each returns a list of dq_results rows)
# ---------------------------------------------------------------------------
def check_range(engine: Engine) -> list[dict]:
    q = (
        select(
            dim_tag.c.tag_name,
            fact_process_readings.c.timestamp,
            fact_process_readings.c.value,
            dim_tag.c.expected_min,
            dim_tag.c.expected_max,
            dim_time.c.shift_key,
        )
        .select_from(
            fact_process_readings.join(dim_tag, fact_process_readings.c.tag_key == dim_tag.c.tag_key)
            .join(dim_time, fact_process_readings.c.time_key == dim_time.c.time_key)
        )
        .where(fact_process_readings.c.value.isnot(None))
    )
    df = pd.read_sql(q, engine)
    if df.empty:
        return []
    bad = df[(df["value"] < df["expected_min"]) | (df["value"] > df["expected_max"])]
    return [
        _row(
            "range_check",
            "Error",
            f"{r.tag_name}={r.value:.2f} outside [{r.expected_min}, {r.expected_max}]",
            tag=r.tag_name,
            ts=pd.to_datetime(r.timestamp).to_pydatetime(),
            shift=int(r.shift_key),
        )
        for r in bad.itertuples()
    ]


def check_stuck(engine: Engine, cfg: Config, wide: pd.DataFrame | None = None) -> list[dict]:
    df = _wide_fact(engine) if wide is None else wide
    pivot = df.pivot_table(index="timestamp", columns="tag_name", values="value", aggfunc="first")
    pivot = pivot.sort_index()
    if "feed_throughput_tph" not in pivot:
        return []
    running = (pivot["feed_throughput_tph"].fillna(0) > 0).to_numpy()
    min_repeat = int(cfg.dq["stuck_min_repeat"])
    ts = pivot.index
    out: list[dict] = []
    for tag in CONTINUOUS_TAGS:
        if tag not in pivot:
            continue
        v = pivot[tag].to_numpy()
        n = len(v)
        i = 0
        while i < n:
            if not running[i] or np.isnan(v[i]):
                i += 1
                continue
            j = i + 1
            while j < n and running[j] and v[j] == v[i]:
                j += 1
            if j - i >= min_repeat:
                out.append(
                    _row(
                        "stuck_sensor",
                        "Error",
                        f"{tag} frozen at {v[i]:.2f} for {j - i} samples",
                        tag=tag,
                        ts=ts[i].to_pydatetime(),
                    )
                )
            i = j
    return out


def check_gaps(engine: Engine, cfg: Config) -> list[dict]:
    q = select(raw_historian.c.tag_name, raw_historian.c.timestamp).where(
        raw_historian.c.tag_name.in_(CONTINUOUS_TAGS)
    )
    df = pd.read_sql(q, engine)
    if df.empty:
        return []
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    step = pd.Timedelta(seconds=cfg.interval_seconds)
    out: list[dict] = []
    for tag, grp in df.groupby("tag_name"):
        ts = grp["timestamp"].drop_duplicates().sort_values()
        diffs = ts.diff()
        gaps = ts[diffs > step * 1.5]
        prev = ts.shift(1)
        for cur, pv, dd in zip(gaps, prev[diffs > step * 1.5], diffs[diffs > step * 1.5]):
            missing = int(dd / step) - 1
            out.append(
                _row(
                    "gap",
                    "Warning",
                    f"{tag}: {missing} samples missing after {pv}",
                    tag=tag,
                    ts=pd.to_datetime(pv).to_pydatetime(),
                )
            )
    return out


def check_duplicates(engine: Engine) -> list[dict]:
    q = (
        select(
            raw_historian.c.tag_name,
            raw_historian.c.timestamp,
            func.count().label("n"),
        )
        .group_by(raw_historian.c.tag_name, raw_historian.c.timestamp)
        .having(func.count() > 1)
    )
    df = pd.read_sql(q, engine)
    return [
        _row(
            "duplicate",
            "Warning",
            f"{r.tag_name}: {int(r.n)} rows at same timestamp",
            tag=r.tag_name,
            ts=pd.to_datetime(r.timestamp).to_pydatetime(),
        )
        for r in df.itertuples()
    ]


def check_bad_flags(engine: Engine) -> list[dict]:
    q = (
        select(
            dim_tag.c.tag_name,
            fact_process_readings.c.timestamp,
            fact_process_readings.c.quality_flag,
            dim_time.c.shift_key,
        )
        .select_from(
            fact_process_readings.join(dim_tag, fact_process_readings.c.tag_key == dim_tag.c.tag_key)
            .join(dim_time, fact_process_readings.c.time_key == dim_time.c.time_key)
        )
        .where(fact_process_readings.c.quality_flag != "Good")
    )
    df = pd.read_sql(q, engine)
    return [
        _row(
            "bad_quality_flag",
            "Warning",
            f"{r.tag_name} flagged {r.quality_flag}",
            tag=r.tag_name,
            ts=pd.to_datetime(r.timestamp).to_pydatetime(),
            shift=int(r.shift_key),
        )
        for r in df.itertuples()
    ]


def check_quality_rollup(engine: Engine, cfg: Config) -> list[dict]:
    q = (
        select(
            dim_tag.c.tag_name,
            dim_time.c.shift_key,
            fact_process_readings.c.quality_flag,
        )
        .select_from(
            fact_process_readings.join(dim_tag, fact_process_readings.c.tag_key == dim_tag.c.tag_key)
            .join(dim_time, fact_process_readings.c.time_key == dim_time.c.time_key)
        )
    )
    df = pd.read_sql(q, engine)
    if df.empty:
        return []
    df["good"] = (df["quality_flag"] == "Good").astype(int)
    roll = df.groupby(["tag_name", "shift_key"])["good"].mean().reset_index()
    target = float(cfg.dq["good_flag_target_pct"]) / 100.0
    bad = roll[roll["good"] < target]
    return [
        _row(
            "quality_rollup",
            "Warning",
            f"{r.tag_name}: {100 * r.good:.1f}% Good (target {cfg.dq['good_flag_target_pct']}%)",
            tag=r.tag_name,
            shift=int(r.shift_key),
        )
        for r in bad.itertuples()
    ]


def check_mass_balance(engine: Engine, cfg: Config) -> list[dict]:
    q = select(
        fact_shift_summary.c.shift_key,
        fact_shift_summary.c.gold_in_feed_oz,
        fact_shift_summary.c.gold_recovered_oz,
        fact_shift_summary.c.gold_in_tails_oz,
    )
    df = pd.read_sql(q, engine)
    if df.empty:
        return []
    tol = float(cfg.reconciliation["tolerance_pct"]) / 100.0
    out: list[dict] = []
    for r in df.itertuples():
        feed = r.gold_in_feed_oz or 0.0
        if feed <= 0:
            continue
        balance = (r.gold_recovered_oz or 0.0) + (r.gold_in_tails_oz or 0.0)
        rel = abs(feed - balance) / feed
        if rel > tol:
            out.append(
                _row(
                    "mass_balance",
                    "Error",
                    f"shift {r.shift_key}: feed={feed:.1f}oz vs recovered+tails={balance:.1f}oz ({rel*100:.1f}% off)",
                    shift=int(r.shift_key),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run_all(engine: Engine, cfg: Config, *, clear: bool = True) -> dict:
    """Run every rule, persist to dq_results, return per-rule counts."""
    wide = _wide_fact(engine)
    findings: list[dict] = []
    findings += check_range(engine)
    findings += check_stuck(engine, cfg, wide=wide)
    findings += check_gaps(engine, cfg)
    findings += check_duplicates(engine)
    findings += check_bad_flags(engine)
    findings += check_quality_rollup(engine, cfg)
    findings += check_mass_balance(engine, cfg)

    with engine.begin() as conn:
        if clear:
            conn.execute(delete(dq_results))
        if findings:
            conn.execute(insert(dq_results), findings)

    counts: dict[str, int] = {}
    for f in findings:
        counts[f["rule"]] = counts.get(f["rule"], 0) + 1
    return counts
