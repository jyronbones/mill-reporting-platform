"""Configuration loading.

All tunables live in ``config.yaml`` at the repo root. The database URL can be
overridden by the ``MILL_DB_URL`` environment variable (and a ``.env`` file is
loaded automatically if present), which keeps secrets/connection details out of
the committed config.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

try:  # optional convenience; not required
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"


@dataclass(frozen=True)
class Config:
    """Parsed, validated view over ``config.yaml`` plus environment overrides."""

    raw: dict[str, Any]
    path: Path

    # ---- database -------------------------------------------------------
    @property
    def db_url(self) -> str:
        return os.environ.get("MILL_DB_URL") or self.raw["database"]["default_url"]

    @property
    def is_sqlite(self) -> bool:
        return self.db_url.startswith("sqlite")

    # ---- simulation -----------------------------------------------------
    @property
    def sim(self) -> dict[str, Any]:
        return self.raw["simulation"]

    @property
    def start(self) -> datetime:
        return datetime.fromisoformat(str(self.sim["start"]))

    @property
    def duration_days(self) -> float:
        return float(self.sim["duration_days"])

    @property
    def interval_seconds(self) -> int:
        return int(self.sim["interval_seconds"])

    @property
    def seed(self) -> int:
        return int(self.sim["seed"])

    @property
    def nameplate_tph(self) -> float:
        return float(self.sim["nameplate_tph"])

    # ---- sub-sections (returned as plain dicts) -------------------------
    @property
    def plant(self) -> dict[str, Any]:
        return self.raw["plant"]

    @property
    def shifts(self) -> dict[str, Any]:
        return self.raw["shifts"]

    @property
    def downtime(self) -> dict[str, Any]:
        return self.raw["downtime"]

    @property
    def faults(self) -> dict[str, Any]:
        return self.raw["faults"]

    @property
    def reconciliation(self) -> dict[str, Any]:
        return self.raw["reconciliation"]

    @property
    def dq(self) -> dict[str, Any]:
        return self.raw["dq"]


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load and return the platform configuration."""
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(cfg_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return Config(raw=raw, path=cfg_path)
