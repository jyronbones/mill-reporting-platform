"""Auto-generate ``docs/data_dictionary.md`` by introspecting the schema.

Pulls table/column/type/nullable straight from the SQLAlchemy metadata and
layers on curated descriptions + source-of-record so the dictionary stays in
lockstep with the actual warehouse.
"""
from __future__ import annotations

from pathlib import Path

from ..config import REPO_ROOT
from ..schema import metadata

# (description, source) keyed by "table.column". Anything unlisted falls back
# to a generic note so the doc still generates cleanly as the schema evolves.
DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "raw_historian.tag_name": ("Historian tag identifier", "Process simulator"),
    "raw_historian.timestamp": ("Sample timestamp", "Process simulator"),
    "raw_historian.value": ("Raw sampled value (may be null on gaps)", "Process simulator"),
    "raw_historian.quality_flag": ("Historian quality: Good/Bad/Questionable/Substituted", "Process simulator"),
    "dim_time.time_key": ("Surrogate time key (yyyymmddHHMM)", "load_time_dimensions"),
    "dim_time.shift_key": ("FK to dim_shift", "load_time_dimensions"),
    "dim_time.shift_name": ("Day or Night", "load_time_dimensions"),
    "dim_tag.tag_name": ("Tag identifier", "TAG_CATALOG"),
    "dim_tag.expected_min": ("Lower bound for DQ range checks", "TAG_CATALOG"),
    "dim_tag.expected_max": ("Upper bound for DQ range checks", "TAG_CATALOG"),
    "dim_equipment.equipment_id": ("Equipment tag", "EQUIPMENT_CATALOG"),
    "dim_equipment.nameplate_capacity": ("Design capacity", "EQUIPMENT_CATALOG"),
    "dim_shift.crew": ("Rotating crew (A-D)", "load_time_dimensions"),
    "fact_process_readings.value": ("Cleaned reading value", "load_fact_readings"),
    "fact_process_readings.quality_flag": ("Carried-through historian quality", "load_fact_readings"),
    "fact_shift_summary.tonnes_milled": ("Tonnes processed in the shift", "load_shift_summary"),
    "fact_shift_summary.avg_recovery_pct": ("Tonnage-weighted gold recovery", "load_shift_summary"),
    "fact_shift_summary.gold_poured_oz": ("Gold poured in the shift (oz)", "load_shift_summary"),
    "fact_shift_summary.gold_in_feed_oz": ("Contained gold in feed (oz)", "load_shift_summary"),
    "fact_shift_summary.gold_recovered_oz": ("Recovered gold (oz)", "load_shift_summary"),
    "fact_shift_summary.gold_in_tails_oz": ("Gold lost to tails (oz)", "load_shift_summary"),
    "fact_shift_summary.availability_pct": ("Uptime / scheduled time", "load_shift_summary"),
    "fact_shift_summary.utilization_pct": ("Throughput / nameplate (OEE performance)", "load_shift_summary"),
    "fact_shift_summary.oee_pct": ("Availability x utilization x quality", "load_shift_summary"),
    "dq_results.rule": ("DQ rule that fired", "dq engine"),
    "dq_results.severity": ("Info / Warning / Error", "dq engine"),
    "fault_log.fault_type": ("Injected fault family (ground truth)", "simulator.faults"),
}

_TABLE_NOTES: dict[str, str] = {
    "raw_historian": "PI-style historian landing zone (one row per tag per sample).",
    "dim_time": "Time dimension at minute grain with shift attributes.",
    "dim_tag": "Tag catalogue with units, stage, and DQ bounds.",
    "dim_equipment": "Equipment register by flowsheet stage.",
    "dim_shift": "One row per shift instance with crew and window.",
    "fact_process_readings": "Reading-grain fact (deduped, key-mapped).",
    "fact_shift_summary": "Shift-grain KPI aggregate — the BI workhorse.",
    "dq_results": "Output of the data-quality engine.",
    "fault_log": "Ground-truth log of deliberately injected faults.",
    "load_audit": "Incremental-load bookkeeping / watermarks.",
}


def generate(out_path: Path | None = None) -> Path:
    out = out_path or (REPO_ROOT / "docs" / "data_dictionary.md")
    out.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Data Dictionary",
        "",
        "_Auto-generated from the SQLAlchemy schema by "
        "`python -m mill docs`. Do not edit by hand._",
        "",
    ]
    for table in metadata.sorted_tables:
        note = _TABLE_NOTES.get(table.name, "")
        lines += [f"## `{table.name}`", "", note, "", "| Column | Type | Nullable | Description | Source |", "| --- | --- | --- | --- | --- |"]
        for col in table.columns:
            key = f"{table.name}.{col.name}"
            desc, source = DESCRIPTIONS.get(key, ("", ""))
            lines.append(
                f"| `{col.name}` | {col.type} | {'yes' if col.nullable else 'no'} | {desc} | {source} |"
            )
        lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out
