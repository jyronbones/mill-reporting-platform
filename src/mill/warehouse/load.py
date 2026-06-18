"""Load raw_historian -> star-schema warehouse.

Incremental by design (watermark on timestamp) and transactional (a date-range
load is atomic; failure rolls back — the documented rollback path). Dimensions
are conformed and idempotent; the reading fact dedupes the historian's
deliberate duplicate timestamps while the raw layer keeps them for DQ to flag.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
from sqlalchemy import Engine, delete, func, insert, select

from ..config import Config
from ..schema import (
    EQUIPMENT_CATALOG,
    TAG_CATALOG,
    TAG_EQUIPMENT,
    dim_equipment,
    dim_shift,
    dim_tag,
    dim_time,
    fact_process_readings,
    fact_shift_summary,
    load_audit,
    raw_historian,
)
from ..simulator.flowsheet import assign_shifts
from . import kpi

_REQUIRED = ["feed_throughput_tph", "feed_grade_gpt", "recovery_pct", "tails_grade_gpt"]


def _time_key_series(idx: pd.DatetimeIndex) -> pd.Series:
    return (
        idx.year * 100000000
        + idx.month * 1000000
        + idx.day * 10000
        + idx.hour * 100
        + idx.minute
    )


# ---------------------------------------------------------------------------
# Dimensions
# ---------------------------------------------------------------------------
def load_static_dimensions(engine: Engine) -> None:
    """Populate dim_tag and dim_equipment from the catalogues (idempotent)."""
    with engine.begin() as conn:
        have_tags = set(conn.execute(select(dim_tag.c.tag_name)).scalars())
        rows = [
            {
                "tag_name": name,
                "description": desc,
                "unit": unit,
                "stage": stage,
                "expected_min": lo,
                "expected_max": hi,
            }
            for name, (desc, unit, stage, lo, hi) in TAG_CATALOG.items()
            if name not in have_tags
        ]
        if rows:
            conn.execute(insert(dim_tag), rows)

        have_eq = set(conn.execute(select(dim_equipment.c.equipment_id)).scalars())
        rows = [
            {
                "equipment_id": eid,
                "name": name,
                "stage": stage,
                "nameplate_capacity": cap,
                "capacity_unit": unit,
            }
            for (eid, name, stage, cap, unit) in EQUIPMENT_CATALOG
            if eid not in have_eq
        ]
        if rows:
            conn.execute(insert(dim_equipment), rows)


def load_time_dimensions(engine: Engine, cfg: Config) -> None:
    """Derive dim_time + dim_shift from the timestamps present in raw_historian."""
    with engine.connect() as conn:
        ts = conn.execute(select(raw_historian.c.timestamp).distinct()).scalars().all()
        have_time = set(conn.execute(select(dim_time.c.time_key)).scalars())
        have_shift = set(conn.execute(select(dim_shift.c.shift_key)).scalars())

    if not ts:
        return
    idx = pd.DatetimeIndex(sorted(set(ts)))
    shift_key, shift_name, crew, shifts = assign_shifts(idx, cfg)
    tk = _time_key_series(idx)

    time_rows = []
    for i, t in enumerate(idx):
        key = int(tk[i])
        if key in have_time:
            continue
        time_rows.append(
            {
                "time_key": key,
                "timestamp": t.to_pydatetime(),
                "date": t.strftime("%Y-%m-%d"),
                "year": int(t.year),
                "month": int(t.month),
                "day": int(t.day),
                "hour": int(t.hour),
                "minute": int(t.minute),
                "day_of_week": t.strftime("%A"),
                "shift_key": int(shift_key[i]),
                "shift_name": str(shift_name[i]),
                "is_daytime": 1 if shift_name[i] == "Day" else 0,
            }
        )

    shift_rows = [
        {
            "shift_key": s["shift_key"],
            "shift_date": s["shift_date"],
            "shift_name": s["shift_name"],
            "crew": s["crew"],
            "start_ts": s["start_ts"],
            "end_ts": s["end_ts"],
        }
        for s in shifts
        if s["shift_key"] not in have_shift
    ]

    with engine.begin() as conn:
        if shift_rows:
            conn.execute(insert(dim_shift), shift_rows)
        if time_rows:
            conn.execute(insert(dim_time), time_rows)


# ---------------------------------------------------------------------------
# Reading fact
# ---------------------------------------------------------------------------
def load_fact_readings(engine: Engine, cfg: Config) -> int:
    """Load new raw rows into fact_process_readings (deduped, key-mapped)."""
    with engine.connect() as conn:
        watermark = conn.execute(
            select(func.max(fact_process_readings.c.timestamp))
        ).scalar()
        tag_keys = dict(
            conn.execute(select(dim_tag.c.tag_name, dim_tag.c.tag_key)).all()
        )
        eq_keys = dict(
            conn.execute(
                select(dim_equipment.c.equipment_id, dim_equipment.c.equipment_key)
            ).all()
        )

    q = select(
        raw_historian.c.tag_name,
        raw_historian.c.timestamp,
        raw_historian.c.value,
        raw_historian.c.quality_flag,
    )
    if watermark is not None:
        q = q.where(raw_historian.c.timestamp > watermark)
    raw = pd.read_sql(q, engine)
    if raw.empty:
        return 0

    raw["timestamp"] = pd.to_datetime(raw["timestamp"])
    raw = raw.drop_duplicates(subset=["tag_name", "timestamp"], keep="first")

    raw["tag_key"] = raw["tag_name"].map(tag_keys)
    raw["equipment_key"] = raw["tag_name"].map(
        lambda t: eq_keys.get(TAG_EQUIPMENT.get(t))
    )
    raw["time_key"] = _time_key_series(pd.DatetimeIndex(raw["timestamp"])).to_numpy()
    raw = raw.dropna(subset=["tag_key"])

    out = raw[
        ["time_key", "tag_key", "equipment_key", "timestamp", "value", "quality_flag"]
    ].copy()
    out["equipment_key"] = out["equipment_key"].astype("object").where(
        out["equipment_key"].notna(), None
    )
    records = out.to_dict("records")
    # NaN values -> SQL NULL
    for r in records:
        if isinstance(r["value"], float) and np.isnan(r["value"]):
            r["value"] = None

    with engine.begin() as conn:
        conn.execute(insert(fact_process_readings), records)
    return len(records)


# ---------------------------------------------------------------------------
# Shift-summary fact
# ---------------------------------------------------------------------------
def _shift_metrics(df: pd.DataFrame, scheduled: int, good_frac: float, cfg: Config) -> dict:
    interval_h = cfg.interval_seconds / 3600.0
    nameplate = cfg.nameplate_tph

    for col in TAG_CATALOG:
        if col not in df:
            df[col] = np.nan

    thr = df["feed_throughput_tph"]
    grade = df["feed_grade_gpt"]
    rec = df["recovery_pct"]
    tails = df["tails_grade_gpt"]

    running = thr.notna() & (thr > 0)
    valid = running & df[_REQUIRED[1:]].notna().all(axis=1)

    tonnes = kpi.tonnes_milled(thr.to_numpy(), interval_h)
    running_min = int(running.sum())
    avg_thr = float(thr[running].mean()) if running_min else float("nan")
    avail = kpi.availability_pct(running_min, scheduled)
    util = kpi.utilization_pct(avg_thr, nameplate)
    quality = 100.0 * good_frac
    oee = kpi.oee_pct(avail, util, quality)

    return {
        "tonnes_milled": tonnes,
        "avg_throughput_tph": avg_thr,
        "avg_head_grade_gpt": kpi.weighted_mean(grade[valid].to_numpy(), thr[valid].to_numpy()),
        "avg_tails_grade_gpt": kpi.weighted_mean(tails[valid].to_numpy(), thr[valid].to_numpy()),
        "avg_recovery_pct": kpi.weighted_mean(rec[valid].to_numpy(), thr[valid].to_numpy()),
        "avg_grind_p80_um": float(df["grind_p80_um"][running].mean()) if running_min else None,
        "avg_mill_power_kw": float(df["mill_power_kw"][running].mean()) if running_min else None,
        "specific_energy_kwh_t": kpi.specific_energy(df["mill_power_kw"][running].to_numpy(), tonnes, interval_h),
        "cyanide_kg_per_t": kpi.reagent_per_tonne(df["cyanide_dose_kgpt"][running].to_numpy(), thr[running].to_numpy()),
        "lime_kg_per_t": kpi.reagent_per_tonne(df["lime_dose_kgpt"][running].to_numpy(), thr[running].to_numpy()),
        "gold_in_feed_oz": kpi.gold_oz(thr[valid].to_numpy(), grade[valid].to_numpy(), interval_h),
        "gold_recovered_oz": kpi.recovered_oz(thr[valid].to_numpy(), grade[valid].to_numpy(), rec[valid].to_numpy(), interval_h),
        "gold_in_tails_oz": kpi.gold_oz(thr[valid].to_numpy(), tails[valid].to_numpy(), interval_h),
        "gold_poured_oz": float(np.nansum(df["gold_poured_oz"].to_numpy())),
        "scheduled_minutes": int(scheduled),
        "running_minutes": running_min,
        "downtime_minutes": int(scheduled - running_min),
        "availability_pct": avail,
        "utilization_pct": util,
        "quality_pct": quality,
        "oee_pct": oee,
    }


def _clean(d: dict) -> dict:
    return {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in d.items()}


def load_shift_summary(engine: Engine, cfg: Config, shift_keys: list[int] | None = None) -> int:
    """Compute fact_shift_summary for the given shifts (or all)."""
    # Reading-level data joined to its shift and tag name.
    j = (
        fact_process_readings.join(dim_time, fact_process_readings.c.time_key == dim_time.c.time_key)
        .join(dim_tag, fact_process_readings.c.tag_key == dim_tag.c.tag_key)
    )
    q = select(
        dim_time.c.shift_key,
        fact_process_readings.c.timestamp,
        dim_tag.c.tag_name,
        fact_process_readings.c.value,
        fact_process_readings.c.quality_flag,
    ).select_from(j)
    if shift_keys:
        q = q.where(dim_time.c.shift_key.in_(shift_keys))
    data = pd.read_sql(q, engine)
    if data.empty:
        return 0
    data["timestamp"] = pd.to_datetime(data["timestamp"])

    # Scheduled minutes + shift descriptors.
    with engine.connect() as conn:
        sq = select(dim_time.c.shift_key, func.count().label("n")).group_by(dim_time.c.shift_key)
        if shift_keys:
            sq = sq.where(dim_time.c.shift_key.in_(shift_keys))
        sched = dict(conn.execute(sq).all())
        dq = select(dim_shift.c.shift_key, dim_shift.c.shift_date, dim_shift.c.shift_name, dim_shift.c.crew)
        if shift_keys:
            dq = dq.where(dim_shift.c.shift_key.in_(shift_keys))
        desc = {r.shift_key: r for r in conn.execute(dq)}

    # Quality fraction per shift.
    qual = data.assign(good=(data["quality_flag"] == "Good").astype(int)).groupby("shift_key")["good"].mean()

    rows = []
    for shift_key, grp in data.groupby("shift_key"):
        wide = grp.pivot_table(index="timestamp", columns="tag_name", values="value", aggfunc="first")
        d = desc.get(shift_key)
        if d is None:
            continue
        metrics = _shift_metrics(wide, int(sched.get(shift_key, len(wide))), float(qual.get(shift_key, 1.0)), cfg)
        row = {
            "shift_key": int(shift_key),
            "shift_date": d.shift_date,
            "shift_name": d.shift_name,
            "crew": d.crew,
            **metrics,
        }
        rows.append(_clean(row))

    keys = [r["shift_key"] for r in rows]
    with engine.begin() as conn:
        if keys:
            conn.execute(delete(fact_shift_summary).where(fact_shift_summary.c.shift_key.in_(keys)))
            conn.execute(insert(fact_shift_summary), rows)
    return len(rows)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def load_warehouse(engine: Engine, cfg: Config, *, full_refresh: bool = False) -> dict:
    """Run the whole warehouse load. Returns a small summary dict."""
    if full_refresh:
        with engine.begin() as conn:
            for tbl in (fact_shift_summary, fact_process_readings, dim_time, dim_shift):
                conn.execute(delete(tbl))

    load_static_dimensions(engine)
    load_time_dimensions(engine, cfg)
    n_facts = load_fact_readings(engine, cfg)
    n_shifts = load_shift_summary(engine, cfg)  # recompute all (small, shift-grain)

    with engine.begin() as conn:
        conn.execute(
            insert(load_audit),
            [
                {
                    "layer": "warehouse",
                    "load_ts": datetime.now(),
                    "watermark_ts": None,
                    "rows_loaded": n_facts,
                    "detail": f"facts={n_facts}, shift_summaries={n_shifts}",
                }
            ],
        )
    return {"fact_readings": n_facts, "shift_summaries": n_shifts}
