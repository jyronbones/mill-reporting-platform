"""Shared pytest fixtures.

A single session-scoped fixture builds a short campaign on a temporary SQLite
database (so the suite needs no Docker / Postgres), runs the full pipeline
(generate -> warehouse -> dq), and exposes the engine, config, and the
injected-fault ground truth. Fault rates are boosted so every fault family is
guaranteed to appear in the short window.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import select

from mill.config import Config, load_config
from mill.db import get_engine
from mill.dq.engine import run_all as dq_run
from mill.schema import fault_log
from mill.simulator.generate import generate_raw
from mill.warehouse.load import load_warehouse


def _test_config(db_url: str) -> Config:
    cfg = load_config()
    raw = copy.deepcopy(cfg.raw)
    raw["database"]["default_url"] = db_url
    raw["simulation"]["duration_days"] = 7
    raw["simulation"]["seed"] = 7
    # Guarantee every fault family appears in a 7-day window.
    raw["faults"].update(
        {
            "out_of_range_per_day": 1.5,
            "stuck_sensor_per_week": 8.0,
            "gap_per_day": 1.5,
            "duplicate_per_day": 1.5,
            "bad_flag_per_day": 1.5,
            "massbalance_shift_rate": 0.3,
        }
    )
    return Config(raw=raw, path=cfg.path)


@pytest.fixture(scope="session")
def pipeline(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("mill") / "test.db"
    cfg = _test_config(f"sqlite:///{db_path.as_posix()}")
    engine = get_engine(cfg)

    generate_raw(cfg, engine, reset=True)
    load_warehouse(engine, cfg, full_refresh=True)
    dq_counts = dq_run(engine, cfg)

    with engine.connect() as conn:
        faults = pd.read_sql(select(fault_log), conn)

    return {
        "engine": engine,
        "cfg": cfg,
        "dq_counts": dq_counts,
        "faults": faults,
    }
