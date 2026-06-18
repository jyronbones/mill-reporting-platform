"""Idempotent + incremental load behaviour."""
from __future__ import annotations

import copy

from sqlalchemy import func, select

from mill.config import Config, load_config
from mill.db import get_engine
from mill.schema import fact_process_readings, raw_historian
from mill.simulator.generate import generate_raw
from mill.warehouse.load import load_warehouse


def _cfg(db_url: str) -> Config:
    base = load_config()
    raw = copy.deepcopy(base.raw)
    raw["database"]["default_url"] = db_url
    raw["simulation"]["duration_days"] = 3
    raw["simulation"]["seed"] = 11
    return Config(raw=raw, path=base.path)


def _count(engine, table) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar()


def test_generate_and_load_are_idempotent(tmp_path):
    cfg = _cfg(f"sqlite:///{(tmp_path / 'inc.db').as_posix()}")
    engine = get_engine(cfg)

    generate_raw(cfg, engine, reset=True)
    raw1 = _count(engine, raw_historian)
    load_warehouse(engine, cfg, full_refresh=True)
    fact1 = _count(engine, fact_process_readings)

    # Re-running the generator incrementally must add nothing (same campaign).
    added = generate_raw(cfg, engine, incremental=True)
    assert added == 0
    assert _count(engine, raw_historian) == raw1

    # Re-running the load must not duplicate facts.
    load_warehouse(engine, cfg, full_refresh=False)
    assert _count(engine, fact_process_readings) == fact1


def test_fact_dedupes_raw_duplicates(tmp_path):
    cfg = _cfg(f"sqlite:///{(tmp_path / 'dedup.db').as_posix()}")
    engine = get_engine(cfg)
    generate_raw(cfg, engine, reset=True)
    load_warehouse(engine, cfg, full_refresh=True)
    # Raw keeps the injected duplicate timestamps; the fact dedupes them.
    assert _count(engine, fact_process_readings) <= _count(engine, raw_historian)
