# Mill KPI Curation — Microsoft Fabric Notebook (PySpark)
#
# Reads the raw shift KPI export from the Lakehouse Files area, computes
# tonnage-weighted KPIs (not flat averages), and writes two curated Delta
# tables back to the Lakehouse: crew-level and daily rollups.
#
# Pattern: Files (raw) -> Spark transform -> Tables (curated Delta).
# Run cell by cell in a Fabric notebook attached to the lakehouse.

# ── Cell 1: read raw CSV from Lakehouse Files ──────────────────────────────
from pyspark.sql import functions as F

raw_path = "Files/shift_kpis.csv"

shifts = (
    spark.read
    .option("header", True)
    .option("inferSchema", True)
    .csv(raw_path)
)

print(f"Loaded {shifts.count()} shift rows")
shifts.printSchema()
display(shifts.limit(5))


# ── Cell 2: crew-level curated KPIs (tonnage-weighted) ─────────────────────
# Recovery, head grade and grind are per-shift averages, so averaging them
# flat would weight a small shift the same as a large one. Weighting by
# tonnes_milled gives the correct production-weighted figure.

def weighted(metric_col):
    return F.sum(F.col(metric_col) * F.col("tonnes_milled")) / F.sum("tonnes_milled")

crew_kpis = (
    shifts.groupBy("crew")
    .agg(
        F.countDistinct("shift_key").alias("shifts"),
        F.sum("tonnes_milled").alias("tonnes_milled"),
        F.sum("gold_poured_oz").alias("gold_poured_oz"),
        weighted("avg_recovery_pct").alias("recovery_pct_wtd"),
        weighted("avg_head_grade_gpt").alias("head_grade_gpt_wtd"),
        weighted("avg_grind_p80_um").alias("grind_p80_um_wtd"),
        (F.sum("running_minutes") / F.sum("scheduled_minutes") * 100).alias("availability_pct"),
        F.sum("downtime_minutes").alias("downtime_min"),
    )
    .orderBy(F.desc("downtime_min"))
)

# Round for readability
for c in ["recovery_pct_wtd", "head_grade_gpt_wtd", "grind_p80_um_wtd", "availability_pct"]:
    crew_kpis = crew_kpis.withColumn(c, F.round(c, 2))
crew_kpis = crew_kpis.withColumn("tonnes_milled", F.round("tonnes_milled", 0)) \
                     .withColumn("gold_poured_oz", F.round("gold_poured_oz", 0))

display(crew_kpis)


# ── Cell 3: daily curated KPIs (tonnage-weighted) ──────────────────────────
daily_kpis = (
    shifts.groupBy("shift_date")
    .agg(
        F.sum("tonnes_milled").alias("tonnes_milled"),
        F.sum("gold_poured_oz").alias("gold_poured_oz"),
        weighted("avg_recovery_pct").alias("recovery_pct_wtd"),
        weighted("avg_throughput_tph").alias("throughput_tph_wtd"),
        F.sum("downtime_minutes").alias("downtime_min"),
    )
    .orderBy("shift_date")
)

for c in ["recovery_pct_wtd", "throughput_tph_wtd"]:
    daily_kpis = daily_kpis.withColumn(c, F.round(c, 2))
daily_kpis = daily_kpis.withColumn("tonnes_milled", F.round("tonnes_milled", 0)) \
                       .withColumn("gold_poured_oz", F.round("gold_poured_oz", 0))

display(daily_kpis)


# ── Cell 4: write curated tables back to the Lakehouse as Delta ────────────
crew_kpis.write.mode("overwrite").format("delta").saveAsTable("crew_kpis")
daily_kpis.write.mode("overwrite").format("delta").saveAsTable("daily_kpis")

print("Wrote Delta tables: crew_kpis, daily_kpis")


# ── Cell 5: verify by reading the curated table back ───────────────────────
display(spark.sql("SELECT * FROM crew_kpis ORDER BY downtime_min DESC"))
