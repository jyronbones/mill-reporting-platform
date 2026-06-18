"""Generate the campaign and land it in the raw historian.

Loading is idempotent and supports incremental appends: re-running only inserts
rows newer than the current high-water mark in ``raw_historian``. The injected
fault ground-truth is written to ``fault_log`` on a fresh (full) generation.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy import Engine, delete, func, insert, select

from ..config import Config
from ..schema import create_all, fault_log, load_audit, raw_historian
from .faults import inject
from .flowsheet import simulate


def _max_raw_ts(engine: Engine) -> datetime | None:
    with engine.connect() as conn:
        return conn.execute(select(func.max(raw_historian.c.timestamp))).scalar()


def generate_raw(
    cfg: Config,
    engine: Engine,
    *,
    incremental: bool = True,
    reset: bool = False,
) -> int:
    """Run the simulator and load raw_historian. Returns rows inserted."""
    create_all(engine)

    if reset:
        with engine.begin() as conn:
            conn.execute(delete(raw_historian))
            conn.execute(delete(fault_log))

    sim = simulate(cfg)
    long, events = inject(sim, cfg)

    watermark = None if reset else _max_raw_ts(engine)
    fresh = watermark is None

    if watermark is not None:
        long = long[long["timestamp"] > pd.Timestamp(watermark)]

    rows = int(len(long))
    if rows:
        long[["tag_name", "timestamp", "value", "quality_flag"]].to_sql(
            raw_historian.name,
            engine,
            if_exists="append",
            index=False,
            chunksize=10_000,
        )

    # Ground-truth fault log: written once, on the initial full generation.
    if fresh and events:
        with engine.begin() as conn:
            conn.execute(insert(fault_log), events)

    with engine.begin() as conn:
        conn.execute(
            insert(load_audit),
            [
                {
                    "layer": "raw_historian",
                    "load_ts": datetime.now(),
                    "watermark_ts": pd.Timestamp(long["timestamp"].max()).to_pydatetime()
                    if rows
                    else watermark,
                    "rows_loaded": rows,
                    "detail": "fresh generation" if fresh else "incremental append",
                }
            ],
        )
    return rows
