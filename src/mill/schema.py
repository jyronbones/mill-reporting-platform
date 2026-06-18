"""Star-schema and supporting table definitions (SQLAlchemy Core).

Layers, top to bottom:
  raw_historian        PI-style time-series landing zone (tag, ts, value, flag)
  dim_*                conformed dimensions
  fact_process_readings   reading-grain fact (one row per tag per timestamp)
  fact_shift_summary   shift-grain aggregate fact (the BI workhorse)
  dq_results           output of the data-quality engine
  fault_log            ground-truth record of injected faults (for tests/demo)
  load_audit           incremental-load bookkeeping

Defined in Core so the same DDL runs on Postgres (the real warehouse) and on
SQLite (used by the test suite, so pytest needs no Docker daemon).
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)

metadata = MetaData()

# --- Raw historian landing zone ------------------------------------------
raw_historian = Table(
    "raw_historian",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tag_name", String(64), nullable=False, index=True),
    Column("timestamp", DateTime, nullable=False, index=True),
    Column("value", Float, nullable=True),  # nullable: gaps / bad reads
    Column("quality_flag", String(16), nullable=False, default="Good"),
)

# --- Dimensions -----------------------------------------------------------
dim_time = Table(
    "dim_time",
    metadata,
    Column("time_key", BigInteger, primary_key=True, autoincrement=False),  # yyyymmddHHMM
    Column("timestamp", DateTime, nullable=False, unique=True),
    Column("date", String(10), nullable=False),
    Column("year", Integer, nullable=False),
    Column("month", Integer, nullable=False),
    Column("day", Integer, nullable=False),
    Column("hour", Integer, nullable=False),
    Column("minute", Integer, nullable=False),
    Column("day_of_week", String(9), nullable=False),
    Column("shift_key", Integer, nullable=False),  # FK -> dim_shift
    Column("shift_name", String(8), nullable=False),  # Day / Night
    Column("is_daytime", Integer, nullable=False),  # 1/0
)

dim_tag = Table(
    "dim_tag",
    metadata,
    Column("tag_key", Integer, primary_key=True, autoincrement=True),
    Column("tag_name", String(64), nullable=False, unique=True),
    Column("description", String(200), nullable=False),
    Column("unit", String(24), nullable=False),
    Column("stage", String(40), nullable=False),
    Column("expected_min", Float, nullable=True),
    Column("expected_max", Float, nullable=True),
)

dim_equipment = Table(
    "dim_equipment",
    metadata,
    Column("equipment_key", Integer, primary_key=True, autoincrement=True),
    Column("equipment_id", String(24), nullable=False, unique=True),
    Column("name", String(80), nullable=False),
    Column("stage", String(40), nullable=False),
    Column("nameplate_capacity", Float, nullable=True),
    Column("capacity_unit", String(24), nullable=True),
)

dim_shift = Table(
    "dim_shift",
    metadata,
    Column("shift_key", Integer, primary_key=True),  # yyyymmdd*10 + D/N index
    Column("shift_date", String(10), nullable=False),
    Column("shift_name", String(8), nullable=False),  # Day / Night
    Column("crew", String(4), nullable=False),
    Column("start_ts", DateTime, nullable=False),
    Column("end_ts", DateTime, nullable=False),
)

# --- Facts ----------------------------------------------------------------
fact_process_readings = Table(
    "fact_process_readings",
    metadata,
    Column("reading_key", Integer, primary_key=True, autoincrement=True),
    Column("time_key", BigInteger, nullable=False, index=True),
    Column("tag_key", Integer, nullable=False, index=True),
    Column("equipment_key", Integer, nullable=True),
    Column("timestamp", DateTime, nullable=False, index=True),
    Column("value", Float, nullable=True),
    Column("quality_flag", String(16), nullable=False, default="Good"),
    UniqueConstraint("tag_key", "timestamp", name="uq_reading_tag_ts"),
)

fact_shift_summary = Table(
    "fact_shift_summary",
    metadata,
    Column("shift_key", Integer, primary_key=True),
    Column("shift_date", String(10), nullable=False),
    Column("shift_name", String(8), nullable=False),
    Column("crew", String(4), nullable=False),
    Column("tonnes_milled", Float),
    Column("avg_throughput_tph", Float),
    Column("avg_head_grade_gpt", Float),
    Column("avg_tails_grade_gpt", Float),
    Column("avg_recovery_pct", Float),
    Column("avg_grind_p80_um", Float),
    Column("avg_mill_power_kw", Float),
    Column("specific_energy_kwh_t", Float),
    Column("cyanide_kg_per_t", Float),
    Column("lime_kg_per_t", Float),
    Column("gold_in_feed_oz", Float),
    Column("gold_recovered_oz", Float),
    Column("gold_in_tails_oz", Float),
    Column("gold_poured_oz", Float),
    Column("scheduled_minutes", Integer),
    Column("running_minutes", Integer),
    Column("downtime_minutes", Integer),
    Column("availability_pct", Float),
    Column("utilization_pct", Float),
    Column("quality_pct", Float),
    Column("oee_pct", Float),
)

# --- Data quality + governance support -----------------------------------
dq_results = Table(
    "dq_results",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_ts", DateTime, nullable=False),
    Column("rule", String(48), nullable=False, index=True),
    Column("tag_name", String(64), nullable=True),
    Column("timestamp", DateTime, nullable=True),
    Column("shift_key", Integer, nullable=True),
    Column("severity", String(12), nullable=False),  # Info/Warning/Error
    Column("detail", String(300), nullable=False),
)

fault_log = Table(
    "fault_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("fault_type", String(32), nullable=False, index=True),
    Column("tag_name", String(64), nullable=True),
    Column("ts_start", DateTime, nullable=True),
    Column("ts_end", DateTime, nullable=True),
    Column("shift_key", Integer, nullable=True),
    Column("detail", String(300), nullable=False),
)

load_audit = Table(
    "load_audit",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("layer", String(32), nullable=False),
    Column("load_ts", DateTime, nullable=False),
    Column("watermark_ts", DateTime, nullable=True),  # high-water mark loaded
    Column("rows_loaded", Integer, nullable=False),
    Column("detail", String(200), nullable=True),
)


# Canonical tag catalogue. Drives dim_tag, the simulator's tag set, and the DQ
# range checks. (description, unit, stage, expected_min, expected_max)
TAG_CATALOG: dict[str, tuple[str, str, str, float | None, float | None]] = {
    "feed_throughput_tph": ("Mill feed throughput", "t/h", "Crushing/Grinding", 0.0, 1100.0),
    "feed_grade_gpt": ("Gold head grade", "g/t", "Feed", 0.0, 8.0),
    "mill_power_kw": ("Combined SAG+ball mill power", "kW", "Grinding", 0.0, 14000.0),
    "grind_p80_um": ("Grind product P80", "um", "Grinding", 40.0, 300.0),
    "mill_load_pct": ("Mill volumetric load", "%", "Grinding", 0.0, 45.0),
    "cyanide_dose_kgpt": ("Cyanide addition rate", "kg/t", "Leach", 0.0, 2.0),
    "lime_dose_kgpt": ("Lime addition rate", "kg/t", "Leach", 0.0, 4.0),
    "recovery_pct": ("Gold recovery", "%", "Recovery", 0.0, 100.0),
    "tails_grade_gpt": ("Tailings gold grade", "g/t", "Tailings", 0.0, 2.0),
    "gold_poured_oz": ("Gold poured per shift", "oz", "Gold Room", 0.0, 5000.0),
}

# Equipment catalogue -> dim_equipment.
EQUIPMENT_CATALOG: list[tuple[str, str, str, float | None, str | None]] = [
    ("CR-01", "Primary Jaw Crusher", "Crushing", 1200.0, "t/h"),
    ("SAG-01", "SAG Mill", "Grinding", 8000.0, "kW"),
    ("BM-01", "Ball Mill", "Grinding", 6000.0, "kW"),
    ("GRV-01", "Gravity Concentrator", "Gravity", None, None),
    ("LCH-01", "Leach Tank Train", "Leach", None, None),
    ("ADR-01", "Elution / Gold Room", "Gold Room", None, None),
]

# Which equipment a tag is primarily associated with (for the reading fact).
TAG_EQUIPMENT: dict[str, str] = {
    "feed_throughput_tph": "CR-01",
    "feed_grade_gpt": "CR-01",
    "mill_power_kw": "SAG-01",
    "grind_p80_um": "BM-01",
    "mill_load_pct": "SAG-01",
    "cyanide_dose_kgpt": "LCH-01",
    "lime_dose_kgpt": "LCH-01",
    "recovery_pct": "LCH-01",
    "tails_grade_gpt": "LCH-01",
    "gold_poured_oz": "ADR-01",
}


def create_all(engine) -> None:
    """Create every table if it does not already exist (idempotent)."""
    metadata.create_all(engine)


def drop_all(engine) -> None:
    """Drop every table (used by `init-db --reset`)."""
    metadata.drop_all(engine)
