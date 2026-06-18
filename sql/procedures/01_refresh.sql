-- Incremental refresh stored procedure (PostgreSQL / PL/pgSQL).
-- Recomputes fact_shift_summary for a date range straight from the reading
-- fact, mirroring the Python KPI formulas in warehouse/kpi.py.
--
-- Incremental refresh:  CALL sp_refresh_shift_summary('2025-03-01','2025-03-02');
-- Rollback path: the body runs in the caller's transaction; on error the whole
--   range rolls back. Re-running a clean date range is idempotent (delete+insert).
-- Statements separated by the `-- @statement` sentinel for the applier.

-- @statement
CREATE OR REPLACE PROCEDURE sp_refresh_shift_summary(
    p_from        date,
    p_to          date,
    p_nameplate   numeric DEFAULT 750.0,
    p_interval_h  numeric DEFAULT (1.0 / 60.0)
)
LANGUAGE plpgsql AS $$
BEGIN
    -- Idempotent: clear the range first so re-runs are clean.
    DELETE FROM fact_shift_summary fss
    USING dim_shift ds
    WHERE fss.shift_key = ds.shift_key
      AND ds.shift_date::date BETWEEN p_from AND p_to;

    INSERT INTO fact_shift_summary
    WITH m AS (
        SELECT
            t.shift_key,
            f.timestamp,
            MAX(f.value) FILTER (WHERE tg.tag_name = 'feed_throughput_tph') AS thr,
            MAX(f.value) FILTER (WHERE tg.tag_name = 'feed_grade_gpt')       AS grade,
            MAX(f.value) FILTER (WHERE tg.tag_name = 'recovery_pct')         AS rec,
            MAX(f.value) FILTER (WHERE tg.tag_name = 'tails_grade_gpt')      AS tails,
            MAX(f.value) FILTER (WHERE tg.tag_name = 'grind_p80_um')         AS p80,
            MAX(f.value) FILTER (WHERE tg.tag_name = 'mill_power_kw')        AS power,
            MAX(f.value) FILTER (WHERE tg.tag_name = 'mill_load_pct')        AS mload,
            MAX(f.value) FILTER (WHERE tg.tag_name = 'cyanide_dose_kgpt')    AS cn,
            MAX(f.value) FILTER (WHERE tg.tag_name = 'lime_dose_kgpt')       AS lime,
            MAX(f.value) FILTER (WHERE tg.tag_name = 'gold_poured_oz')       AS poured
        FROM fact_process_readings f
        JOIN dim_time  t  ON f.time_key = t.time_key
        JOIN dim_tag   tg ON f.tag_key = tg.tag_key
        JOIN dim_shift ds ON t.shift_key = ds.shift_key
        WHERE ds.shift_date::date BETWEEN p_from AND p_to
        GROUP BY t.shift_key, f.timestamp
    ),
    sched AS (
        SELECT shift_key, COUNT(*) AS scheduled_minutes
        FROM dim_time
        GROUP BY shift_key
    ),
    qual AS (
        SELECT t.shift_key,
               AVG((f.quality_flag = 'Good')::int::numeric) AS good_frac
        FROM fact_process_readings f
        JOIN dim_time t ON f.time_key = t.time_key
        GROUP BY t.shift_key
    ),
    agg AS (
        SELECT
            m.shift_key,
            SUM(m.thr) * p_interval_h AS tonnes_milled,
            AVG(m.thr) FILTER (WHERE m.thr > 0) AS avg_throughput_tph,
            SUM(m.grade * m.thr) FILTER (WHERE m.thr > 0 AND m.grade IS NOT NULL)
                / NULLIF(SUM(m.thr) FILTER (WHERE m.thr > 0 AND m.grade IS NOT NULL), 0) AS avg_head_grade_gpt,
            SUM(m.tails * m.thr) FILTER (WHERE m.thr > 0 AND m.tails IS NOT NULL)
                / NULLIF(SUM(m.thr) FILTER (WHERE m.thr > 0 AND m.tails IS NOT NULL), 0) AS avg_tails_grade_gpt,
            SUM(m.rec * m.thr) FILTER (WHERE m.thr > 0 AND m.rec IS NOT NULL)
                / NULLIF(SUM(m.thr) FILTER (WHERE m.thr > 0 AND m.rec IS NOT NULL), 0) AS avg_recovery_pct,
            AVG(m.p80) FILTER (WHERE m.thr > 0) AS avg_grind_p80_um,
            AVG(m.power) FILTER (WHERE m.thr > 0) AS avg_mill_power_kw,
            SUM(m.power) FILTER (WHERE m.thr > 0) * p_interval_h
                / NULLIF(SUM(m.thr) * p_interval_h, 0) AS specific_energy_kwh_t,
            SUM(m.cn * m.thr) FILTER (WHERE m.thr > 0)
                / NULLIF(SUM(m.thr) FILTER (WHERE m.thr > 0), 0) AS cyanide_kg_per_t,
            SUM(m.lime * m.thr) FILTER (WHERE m.thr > 0)
                / NULLIF(SUM(m.thr) FILTER (WHERE m.thr > 0), 0) AS lime_kg_per_t,
            SUM(m.thr * m.grade) FILTER (WHERE m.thr > 0 AND m.grade IS NOT NULL AND m.rec IS NOT NULL AND m.tails IS NOT NULL)
                * p_interval_h / 31.1034768 AS gold_in_feed_oz,
            SUM(m.thr * m.grade * m.rec / 100.0) FILTER (WHERE m.thr > 0 AND m.grade IS NOT NULL AND m.rec IS NOT NULL AND m.tails IS NOT NULL)
                * p_interval_h / 31.1034768 AS gold_recovered_oz,
            SUM(m.thr * m.tails) FILTER (WHERE m.thr > 0 AND m.grade IS NOT NULL AND m.rec IS NOT NULL AND m.tails IS NOT NULL)
                * p_interval_h / 31.1034768 AS gold_in_tails_oz,
            COALESCE(SUM(m.poured), 0) AS gold_poured_oz,
            COUNT(*) FILTER (WHERE m.thr > 0) AS running_minutes
        FROM m
        GROUP BY m.shift_key
    )
    SELECT
        a.shift_key,
        ds.shift_date,
        ds.shift_name,
        ds.crew,
        a.tonnes_milled,
        a.avg_throughput_tph,
        a.avg_head_grade_gpt,
        a.avg_tails_grade_gpt,
        a.avg_recovery_pct,
        a.avg_grind_p80_um,
        a.avg_mill_power_kw,
        a.specific_energy_kwh_t,
        a.cyanide_kg_per_t,
        a.lime_kg_per_t,
        a.gold_in_feed_oz,
        a.gold_recovered_oz,
        a.gold_in_tails_oz,
        a.gold_poured_oz,
        s.scheduled_minutes,
        a.running_minutes,
        s.scheduled_minutes - a.running_minutes AS downtime_minutes,
        100.0 * a.running_minutes / NULLIF(s.scheduled_minutes, 0) AS availability_pct,
        100.0 * a.avg_throughput_tph / NULLIF(p_nameplate, 0) AS utilization_pct,
        100.0 * q.good_frac AS quality_pct,
        (100.0 * a.running_minutes / NULLIF(s.scheduled_minutes, 0))
            * (100.0 * a.avg_throughput_tph / NULLIF(p_nameplate, 0))
            * (100.0 * q.good_frac) / 10000.0 AS oee_pct
    FROM agg a
    JOIN dim_shift ds ON a.shift_key = ds.shift_key
    JOIN sched s ON a.shift_key = s.shift_key
    JOIN qual q ON a.shift_key = q.shift_key;
END;
$$;

-- @statement
-- Convenience wrapper: refresh the whole campaign.
CREATE OR REPLACE PROCEDURE sp_refresh_all()
LANGUAGE plpgsql AS $$
DECLARE
    lo date;
    hi date;
BEGIN
    SELECT MIN(shift_date::date), MAX(shift_date::date) INTO lo, hi FROM dim_shift;
    IF lo IS NOT NULL THEN
        CALL sp_refresh_shift_summary(lo, hi);
    END IF;
END;
$$;
