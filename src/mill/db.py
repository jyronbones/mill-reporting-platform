"""Engine/connection helpers shared by every layer."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import Connection

from .config import Config

_ENGINES: dict[str, Engine] = {}


def get_engine(cfg: Config) -> Engine:
    """Return a cached SQLAlchemy engine for the configured database URL."""
    url = cfg.db_url
    if url not in _ENGINES:
        connect_args = {}
        # SQLite needs this to be usable across the loader's threads/sessions.
        kwargs: dict = {"future": True}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _ENGINES[url] = create_engine(url, connect_args=connect_args, **kwargs)
    return _ENGINES[url]


@contextmanager
def transaction(engine: Engine) -> Iterator[Connection]:
    """Open a connection inside a transaction, committing on success.

    Used by the loader so a date-range load is atomic — any failure rolls the
    whole range back, which is the documented rollback path for the warehouse.
    """
    with engine.begin() as conn:
        yield conn
