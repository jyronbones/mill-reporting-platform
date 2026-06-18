-- KPI semantic layer (PostgreSQL). Power BI connects to these views.
-- Statements are separated by the sentinel line `-- @statement` so the Python
-- applier (warehouse/views.py) can run them one at a time.

-- @statement
-- Shift-grain KPIs: a friendly pass-through of the summary fact.
CREATE OR REPLACE VIEW v_shift_kpis AS
SELECT
    fss.shift_key,
    fss.shift_date,
    fss.shift_name,
    fss.crew,
    fss.tonnes_milled,
    fss.avg_throughput_tph,
    fss.avg_head_grade_gpt,
    fss.avg_tails_grade_gpt,
    fss.avg_recovery_pct,
    fss.avg_grind_p80_um,
    fss.avg_mill_power_kw,
    fss.specific_energy_kwh_t,
    fss.cyanide_kg_per_t,
    fss.lime_kg_per_t,
    fss.gold_in_feed_oz,
    fss.gold_recovered_oz,
    fss.gold_in_tails_oz,
    fss.gold_poured_oz,
    fss.scheduled_minutes,
    fss.running_minutes,
    fss.downtime_minutes,
    fss.availability_pct,
    fss.utilization_pct,
    fss.quality_pct,
    fss.oee_pct
FROM fact_shift_summary fss;

-- @statement
-- Daily rollup with tonnage-weighted recovery/grade.
CREATE OR REPLACE VIEW v_daily_kpis AS
SELECT
    shift_date AS date,
    SUM(tonnes_milled) AS tonnes_milled,
    SUM(gold_poured_oz) AS gold_poured_oz,
    SUM(gold_in_feed_oz) AS gold_in_feed_oz,
    SUM(gold_in_tails_oz) AS gold_in_tails_oz,
    SUM(avg_recovery_pct * tonnes_milled) / NULLIF(SUM(tonnes_milled), 0) AS recovery_pct,
    SUM(avg_head_grade_gpt * tonnes_milled) / NULLIF(SUM(tonnes_milled), 0) AS head_grade_gpt,
    SUM(downtime_minutes) AS downtime_minutes,
    AVG(availability_pct) AS availability_pct,
    AVG(oee_pct) AS oee_pct
FROM fact_shift_summary
GROUP BY shift_date;

-- @statement
-- Hourly throughput for trend lines.
CREATE OR REPLACE VIEW v_hourly_throughput AS
SELECT
    t.date,
    t.hour,
    t.shift_name,
    AVG(f.value) AS avg_throughput_tph,
    SUM(f.value) / 60.0 AS tonnes
FROM fact_process_readings f
JOIN dim_time t ON f.time_key = t.time_key
JOIN dim_tag tag ON f.tag_key = tag.tag_key
WHERE tag.tag_name = 'feed_throughput_tph' AND f.value IS NOT NULL
GROUP BY t.date, t.hour, t.shift_name;

-- @statement
-- Recovery vs head grade by shift (the built-in correlation, for a scatter).
CREATE OR REPLACE VIEW v_recovery_vs_grade AS
SELECT
    shift_key,
    shift_date,
    shift_name,
    avg_head_grade_gpt,
    avg_grind_p80_um,
    avg_recovery_pct,
    tonnes_milled
FROM fact_shift_summary
WHERE avg_recovery_pct IS NOT NULL;

-- @statement
-- Downtime by day (feeds a Pareto in Power BI).
CREATE OR REPLACE VIEW v_downtime_by_day AS
SELECT
    shift_date AS date,
    SUM(downtime_minutes) AS downtime_minutes,
    SUM(scheduled_minutes) AS scheduled_minutes,
    100.0 * SUM(downtime_minutes) / NULLIF(SUM(scheduled_minutes), 0) AS downtime_pct
FROM fact_shift_summary
GROUP BY shift_date;

-- @statement
-- Cumulative gold poured over the campaign.
CREATE OR REPLACE VIEW v_gold_cumulative AS
SELECT
    shift_key,
    shift_date,
    shift_name,
    gold_poured_oz,
    SUM(gold_poured_oz) OVER (ORDER BY shift_key) AS cumulative_oz
FROM fact_shift_summary;

-- @statement
-- DQ summary: finding counts by rule and severity (drives the DQ tab).
CREATE OR REPLACE VIEW v_dq_summary AS
SELECT
    rule,
    severity,
    COUNT(*) AS findings
FROM dq_results
GROUP BY rule, severity;

-- @statement
-- % Good historian flags per tag per shift.
CREATE OR REPLACE VIEW v_quality_by_tag AS
SELECT
    tag.tag_name,
    t.shift_key,
    100.0 * AVG((f.quality_flag = 'Good')::int) AS pct_good,
    COUNT(*) AS samples
FROM fact_process_readings f
JOIN dim_tag tag ON f.tag_key = tag.tag_key
JOIN dim_time t ON f.time_key = t.time_key
GROUP BY tag.tag_name, t.shift_key;

-- @statement
-- Shifts that failed mass-balance reconciliation, with the discrepancy.
CREATE OR REPLACE VIEW v_massbalance_failures AS
SELECT
    d.shift_key,
    s.shift_date,
    s.shift_name,
    s.gold_in_feed_oz,
    s.gold_recovered_oz,
    s.gold_in_tails_oz,
    s.gold_recovered_oz + s.gold_in_tails_oz AS balance_oz,
    d.detail
FROM dq_results d
JOIN fact_shift_summary s ON s.shift_key = d.shift_key
WHERE d.rule = 'mass_balance';
