"""Apply the SQL semantic layer (views + procedures) to the warehouse.

PostgreSQL only — these use PG-native features (FILTER, window functions,
PL/pgSQL). On SQLite (the test path) this is a documented no-op.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine

from ..config import REPO_ROOT

SQL_DIRS = [REPO_ROOT / "sql" / "views", REPO_ROOT / "sql" / "procedures"]
SENTINEL = "-- @statement"


def _statements(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    chunks = [c.strip() for c in text.split(SENTINEL)]
    return [c for c in chunks if c and not c.lstrip().startswith("--") or "CREATE" in c]


def apply_sql(engine: Engine) -> int:
    """Execute every view/procedure file. Returns statements applied."""
    if engine.dialect.name != "postgresql":
        return 0
    applied = 0
    with engine.begin() as conn:
        for d in SQL_DIRS:
            if not d.exists():
                continue
            for f in sorted(d.glob("*.sql")):
                for stmt in _statements(f):
                    # Strip leading comment-only lines but keep the DDL body.
                    if "CREATE" not in stmt.upper():
                        continue
                    # psycopg reads '%' as a parameter placeholder even with no
                    # params; double it so literal '%' (e.g. in comments) is safe.
                    conn.exec_driver_sql(stmt.replace("%", "%%"))
                    applied += 1
    return applied
