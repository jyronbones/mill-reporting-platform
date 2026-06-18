"""Command-line entry point: `python -m mill <command>`.

Commands (run in this order for a fresh build, or just use `all`):
    init-db   create tables          generate  run simulator -> raw_historian
    load      raw -> warehouse       views     apply SQL views + procedures (PG)
    dq        run data-quality       docs      generate governance docs
    all       do everything          info      print row counts
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from sqlalchemy import func, select

from .config import load_config
from .db import get_engine
from .dq.engine import run_all as dq_run
from .governance import data_dictionary, kpi_definitions, lineage
from .schema import create_all, drop_all, metadata
from .simulator.generate import generate_raw
from .warehouse.load import load_warehouse
from .warehouse.views import apply_sql


def _engine_cfg(args):
    cfg = load_config(getattr(args, "config", None))
    return get_engine(cfg), cfg


def cmd_init_db(args):
    engine, _ = _engine_cfg(args)
    if args.reset:
        drop_all(engine)
    create_all(engine)
    print(f"init-db: {len(metadata.tables)} tables ready on {engine.url}")


def cmd_generate(args):
    engine, cfg = _engine_cfg(args)
    t0 = datetime.now()
    n = generate_raw(cfg, engine, incremental=not args.reset, reset=args.reset)
    print(f"generate: {n:,} raw rows in {(datetime.now() - t0).total_seconds():.1f}s")


def cmd_load(args):
    engine, cfg = _engine_cfg(args)
    t0 = datetime.now()
    res = load_warehouse(engine, cfg, full_refresh=args.full)
    print(
        f"load: {res['fact_readings']:,} readings, {res['shift_summaries']} shift summaries "
        f"in {(datetime.now() - t0).total_seconds():.1f}s"
    )


def cmd_views(args):
    engine, _ = _engine_cfg(args)
    n = apply_sql(engine)
    if n == 0 and engine.dialect.name != "postgresql":
        print("views: skipped (SQL semantic layer is PostgreSQL-only)")
    else:
        print(f"views: applied {n} view/procedure statements")


def cmd_dq(args):
    engine, cfg = _engine_cfg(args)
    counts = dq_run(engine, cfg)
    total = sum(counts.values())
    print(f"dq: {total} findings -> dq_results")
    for rule, n in sorted(counts.items()):
        print(f"    {rule:18s} {n}")


def cmd_docs(args):
    _engine_cfg(args)  # ensures config loads
    p1 = data_dictionary.generate()
    p2 = kpi_definitions.generate()
    p3 = lineage.generate()
    print(f"docs: wrote {p1.name}, {p2.name}, {p3.name} to docs/")


def cmd_info(args):
    engine, _ = _engine_cfg(args)
    print(f"info: {engine.url}")
    with engine.connect() as conn:
        for tbl in metadata.sorted_tables:
            try:
                n = conn.execute(select(func.count()).select_from(tbl)).scalar()
            except Exception:
                n = "—"
            print(f"    {tbl.name:24s} {n}")


def cmd_all(args):
    cmd_init_db(args)
    cmd_generate(args)
    cmd_load(args)
    cmd_views(args)
    cmd_dq(args)
    cmd_docs(args)
    print("all: done.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mill", description="Mill operational data platform")
    p.add_argument("--config", help="path to config.yaml (default: repo root)")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("init-db", help="create tables")
    sp.add_argument("--reset", action="store_true", help="drop existing tables first")
    sp.set_defaults(func=cmd_init_db)

    sp = sub.add_parser("generate", help="run simulator -> raw_historian")
    sp.add_argument("--reset", action="store_true", help="wipe raw + fault log first")
    sp.set_defaults(func=cmd_generate)

    sp = sub.add_parser("load", help="raw -> warehouse")
    sp.add_argument("--full", action="store_true", help="full refresh (wipe facts first)")
    sp.set_defaults(func=cmd_load)

    sp = sub.add_parser("views", help="apply SQL views + procedures (PG only)")
    sp.set_defaults(func=cmd_views)

    sp = sub.add_parser("dq", help="run data-quality engine")
    sp.set_defaults(func=cmd_dq)

    sp = sub.add_parser("docs", help="generate governance docs")
    sp.set_defaults(func=cmd_docs)

    sp = sub.add_parser("info", help="print row counts")
    sp.set_defaults(func=cmd_info)

    sp = sub.add_parser("all", help="init-db + generate + load + views + dq + docs")
    sp.add_argument("--reset", action="store_true", help="reset raw + tables first")
    sp.add_argument("--full", action="store_true", help="full warehouse refresh")
    sp.set_defaults(func=cmd_all)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
