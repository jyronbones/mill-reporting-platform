# Data Dictionary

_Auto-generated from the SQLAlchemy schema by `python -m mill docs`. Do not edit by hand._

## `dim_equipment`

Equipment register by flowsheet stage.

| Column | Type | Nullable | Description | Source |
| --- | --- | --- | --- | --- |
| `equipment_key` | INTEGER | no |  |  |
| `equipment_id` | VARCHAR(24) | no | Equipment tag | EQUIPMENT_CATALOG |
| `name` | VARCHAR(80) | no |  |  |
| `stage` | VARCHAR(40) | no |  |  |
| `nameplate_capacity` | FLOAT | yes | Design capacity | EQUIPMENT_CATALOG |
| `capacity_unit` | VARCHAR(24) | yes |  |  |

## `dim_shift`

One row per shift instance with crew and window.

| Column | Type | Nullable | Description | Source |
| --- | --- | --- | --- | --- |
| `shift_key` | INTEGER | no |  |  |
| `shift_date` | VARCHAR(10) | no |  |  |
| `shift_name` | VARCHAR(8) | no |  |  |
| `crew` | VARCHAR(4) | no | Rotating crew (A-D) | load_time_dimensions |
| `start_ts` | DATETIME | no |  |  |
| `end_ts` | DATETIME | no |  |  |

## `dim_tag`

Tag catalogue with units, stage, and DQ bounds.

| Column | Type | Nullable | Description | Source |
| --- | --- | --- | --- | --- |
| `tag_key` | INTEGER | no |  |  |
| `tag_name` | VARCHAR(64) | no | Tag identifier | TAG_CATALOG |
| `description` | VARCHAR(200) | no |  |  |
| `unit` | VARCHAR(24) | no |  |  |
| `stage` | VARCHAR(40) | no |  |  |
| `expected_min` | FLOAT | yes | Lower bound for DQ range checks | TAG_CATALOG |
| `expected_max` | FLOAT | yes | Upper bound for DQ range checks | TAG_CATALOG |

## `dim_time`

Time dimension at minute grain with shift attributes.

| Column | Type | Nullable | Description | Source |
| --- | --- | --- | --- | --- |
| `time_key` | BIGINT | no | Surrogate time key (yyyymmddHHMM) | load_time_dimensions |
| `timestamp` | DATETIME | no |  |  |
| `date` | VARCHAR(10) | no |  |  |
| `year` | INTEGER | no |  |  |
| `month` | INTEGER | no |  |  |
| `day` | INTEGER | no |  |  |
| `hour` | INTEGER | no |  |  |
| `minute` | INTEGER | no |  |  |
| `day_of_week` | VARCHAR(9) | no |  |  |
| `shift_key` | INTEGER | no | FK to dim_shift | load_time_dimensions |
| `shift_name` | VARCHAR(8) | no | Day or Night | load_time_dimensions |
| `is_daytime` | INTEGER | no |  |  |

## `dq_results`

Output of the data-quality engine.

| Column | Type | Nullable | Description | Source |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | no |  |  |
| `run_ts` | DATETIME | no |  |  |
| `rule` | VARCHAR(48) | no | DQ rule that fired | dq engine |
| `tag_name` | VARCHAR(64) | yes |  |  |
| `timestamp` | DATETIME | yes |  |  |
| `shift_key` | INTEGER | yes |  |  |
| `severity` | VARCHAR(12) | no | Info / Warning / Error | dq engine |
| `detail` | VARCHAR(300) | no |  |  |

## `fact_process_readings`

Reading-grain fact (deduped, key-mapped).

| Column | Type | Nullable | Description | Source |
| --- | --- | --- | --- | --- |
| `reading_key` | INTEGER | no |  |  |
| `time_key` | BIGINT | no |  |  |
| `tag_key` | INTEGER | no |  |  |
| `equipment_key` | INTEGER | yes |  |  |
| `timestamp` | DATETIME | no |  |  |
| `value` | FLOAT | yes | Cleaned reading value | load_fact_readings |
| `quality_flag` | VARCHAR(16) | no | Carried-through historian quality | load_fact_readings |

## `fact_shift_summary`

Shift-grain KPI aggregate — the BI workhorse.

| Column | Type | Nullable | Description | Source |
| --- | --- | --- | --- | --- |
| `shift_key` | INTEGER | no |  |  |
| `shift_date` | VARCHAR(10) | no |  |  |
| `shift_name` | VARCHAR(8) | no |  |  |
| `crew` | VARCHAR(4) | no |  |  |
| `tonnes_milled` | FLOAT | yes | Tonnes processed in the shift | load_shift_summary |
| `avg_throughput_tph` | FLOAT | yes |  |  |
| `avg_head_grade_gpt` | FLOAT | yes |  |  |
| `avg_tails_grade_gpt` | FLOAT | yes |  |  |
| `avg_recovery_pct` | FLOAT | yes | Tonnage-weighted gold recovery | load_shift_summary |
| `avg_grind_p80_um` | FLOAT | yes |  |  |
| `avg_mill_power_kw` | FLOAT | yes |  |  |
| `specific_energy_kwh_t` | FLOAT | yes |  |  |
| `cyanide_kg_per_t` | FLOAT | yes |  |  |
| `lime_kg_per_t` | FLOAT | yes |  |  |
| `gold_in_feed_oz` | FLOAT | yes | Contained gold in feed (oz) | load_shift_summary |
| `gold_recovered_oz` | FLOAT | yes | Recovered gold (oz) | load_shift_summary |
| `gold_in_tails_oz` | FLOAT | yes | Gold lost to tails (oz) | load_shift_summary |
| `gold_poured_oz` | FLOAT | yes | Gold poured in the shift (oz) | load_shift_summary |
| `scheduled_minutes` | INTEGER | yes |  |  |
| `running_minutes` | INTEGER | yes |  |  |
| `downtime_minutes` | INTEGER | yes |  |  |
| `availability_pct` | FLOAT | yes | Uptime / scheduled time | load_shift_summary |
| `utilization_pct` | FLOAT | yes | Throughput / nameplate (OEE performance) | load_shift_summary |
| `quality_pct` | FLOAT | yes |  |  |
| `oee_pct` | FLOAT | yes | Availability x utilization x quality | load_shift_summary |

## `fault_log`

Ground-truth log of deliberately injected faults.

| Column | Type | Nullable | Description | Source |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | no |  |  |
| `fault_type` | VARCHAR(32) | no | Injected fault family (ground truth) | simulator.faults |
| `tag_name` | VARCHAR(64) | yes |  |  |
| `ts_start` | DATETIME | yes |  |  |
| `ts_end` | DATETIME | yes |  |  |
| `shift_key` | INTEGER | yes |  |  |
| `detail` | VARCHAR(300) | no |  |  |

## `load_audit`

Incremental-load bookkeeping / watermarks.

| Column | Type | Nullable | Description | Source |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | no |  |  |
| `layer` | VARCHAR(32) | no |  |  |
| `load_ts` | DATETIME | no |  |  |
| `watermark_ts` | DATETIME | yes |  |  |
| `rows_loaded` | INTEGER | no |  |  |
| `detail` | VARCHAR(200) | yes |  |  |

## `raw_historian`

PI-style historian landing zone (one row per tag per sample).

| Column | Type | Nullable | Description | Source |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | no |  |  |
| `tag_name` | VARCHAR(64) | no | Historian tag identifier | Process simulator |
| `timestamp` | DATETIME | no | Sample timestamp | Process simulator |
| `value` | FLOAT | yes | Raw sampled value (may be null on gaps) | Process simulator |
| `quality_flag` | VARCHAR(16) | no | Historian quality: Good/Bad/Questionable/Substituted | Process simulator |
