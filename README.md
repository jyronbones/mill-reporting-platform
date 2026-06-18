# Mill Operational Data & Reporting Platform

A synthetic, end-to-end operational data stack modeled on a conventional gold
processing mill. It demonstrates the full reporting lifecycle — data generation,
historian-style ingestion, a SQL warehouse, a KPI semantic layer, data-quality
reconciliation, and governance — as a backend a **Power BI** report connects to
natively.

```
process simulator (Python)        correlated tags + seeded faults
        │
        ▼
raw historian  (tag, ts, value, quality_flag)      ← mimics a PI historian
        │  staging / cleaning
        ▼
SQL warehouse  (star schema: dim_* + fact_*)
        │  KPI views + stored procedures
        ▼
KPI semantic layer        ← Power BI connects here
        │
        ▼
Power BI report  (built by hand)
```

A separate **data-quality engine** runs against the raw and warehouse layers and
writes findings to `dq_results`, including a gold **mass-balance reconciliation**.

---

## Quick start

### 1. Stand up the warehouse (PostgreSQL in Docker)

```bash
docker compose up -d          # starts Postgres 16 on localhost:5433
```

> Requires the Docker daemon to be running (start Docker Desktop first).
>
> The container publishes on host port **5433** (container 5432) to avoid
> clashing with a native PostgreSQL install that often owns 5432. If 5433 is
> also taken, change the mapping in `docker-compose.yml` and the port in
> `config.yaml` (or set `MILL_DB_URL`).

### 2. Set up Python

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate on *nix
pip install -r requirements.txt
pip install -e .
```

### 3. Build everything with one command

```bash
python -m mill all
```

`all` runs, in order: `init-db → generate → load → views → dq → docs`.
On a fresh Postgres this generates ~90 days of 1-minute data (~1.3M readings),
loads the star schema, applies the KPI views + refresh procedure, runs the DQ
engine, and regenerates the governance docs.

Individual steps:

| Command | Does |
| --- | --- |
| `python -m mill init-db [--reset]` | create (or drop+create) all tables |
| `python -m mill generate [--reset]` | run the simulator → `raw_historian` (idempotent, incremental) |
| `python -m mill load [--full]` | `raw_historian` → warehouse (incremental, transactional) |
| `python -m mill views` | apply the SQL views + stored procedures (PostgreSQL only) |
| `python -m mill dq` | run the data-quality engine → `dq_results` |
| `python -m mill docs` | regenerate `docs/` (data dictionary, KPI defs, lineage) |
| `python -m mill info` | print row counts per table |

All tunables live in [`config.yaml`](config.yaml): simulation window, interval,
process parameters, fault-injection rates, and the reconciliation tolerance.

---

## Power BI connection

Power BI Desktop connects to PostgreSQL natively (Npgsql, no ODBC):

1. **Get Data → PostgreSQL database**.
2. Server `localhost:5433`, Database `mill`.
3. **Data Connectivity mode:** Import (recommended for this demo).
4. User `mill`, password `mill` (from `docker-compose.yml`).
5. In the Navigator, load the **`v_*` views** — these are the KPI semantic layer:

| View | Use in the report |
| --- | --- |
| `v_shift_kpis` | KPI cards, shift table |
| `v_daily_kpis` | daily trend lines |
| `v_hourly_throughput` | throughput trend with shift overlay |
| `v_recovery_vs_grade` | recovery-vs-head-grade scatter |
| `v_downtime_by_day` | downtime Pareto |
| `v_gold_cumulative` | cumulative gold poured |
| `v_dq_summary` | data-quality tab (findings by rule/severity) |
| `v_quality_by_tag` | % Good historian flags per tag/shift |
| `v_massbalance_failures` | shifts that failed reconciliation |

Connection string (for reference / ADO.NET):

```
Host=localhost;Port=5433;Database=mill;Username=mill;Password=mill
```

> **Local development without Docker:** set `MILL_DB_URL=sqlite:///mill.db`
> (or copy `.env.example` to `.env`). The SQL `v_*` views are PostgreSQL-only,
> so on SQLite query the `fact_*` tables directly. The test suite always runs on
> SQLite, so `pytest` needs no Docker.

---

## Testing

```bash
pytest
```

The suite (on a short, seeded SQLite campaign) covers:

- **KPI formula correctness** on known inputs (`tests/test_kpi.py`).
- **Every seeded fault type is caught** by the DQ engine (`tests/test_dq.py`).
- **Mass-balance reconciliation flags exactly the injected shifts**
  (`tests/test_massbalance.py`).
- **Idempotent + incremental** load behaviour (`tests/test_incremental.py`).
- **Governance docs generate cleanly** (`tests/test_governance.py`).

---

## Layout

```
config.yaml              master configuration (single source of truth)
docker-compose.yml       PostgreSQL warehouse
src/mill/
  simulator/             flowsheet physics + fault injection + generator
  warehouse/             star-schema load, KPI formulas, SQL applier
  dq/                    data-quality rule engine + mass balance
  governance/            data dictionary / KPI defs / lineage generators
  cli.py                 `python -m mill <command>`
sql/
  views/                 KPI semantic-layer views (PostgreSQL)
  procedures/            incremental-refresh stored procedure (PL/pgSQL)
docs/                    generated governance artifacts
tests/                   pytest suite
```

See [`docs/lineage.md`](docs/lineage.md) for the end-to-end lineage diagram,
[`docs/kpi_definitions.md`](docs/kpi_definitions.md) for KPI formulas, and
[`docs/data_dictionary.md`](docs/data_dictionary.md) for the schema reference.

---

## What's modeled

**Flowsheet:** crushing → SAG + ball grinding → gravity → leach/CIL → gold room.

**Tags (1-min):** `feed_throughput_tph`, `feed_grade_gpt`, `mill_power_kw`,
`grind_p80_um`, `mill_load_pct`, `cyanide_dose_kgpt`, `lime_dose_kgpt`,
`recovery_pct`, `tails_grade_gpt`, and `gold_poured_oz` (per shift).

**Built-in realism:** diurnal + shift patterns, planned/unplanned downtime with
ramps, and *correlated* physics — finer grind lifts recovery, pushing throughput
degrades it, recovery tracks head grade, and tails grade closes the gold balance
by construction.

**Seeded data-quality faults** (caught by the DQ engine): out-of-range values,
stuck sensors, missing-timestamp gaps, duplicate timestamps, bad historian
quality flags, and shift-level mass-balance breaks. Every injected fault is
recorded in `fault_log` as ground truth for the tests.
