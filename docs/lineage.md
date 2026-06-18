# Data Lineage

End-to-end flow from the synthetic source to the Power BI report.
Each box is a real artifact in this repo.

```mermaid
flowchart TD
    SIM[Process simulator\nsimulator/flowsheet.py + faults.py]
    RAW[(raw_historian\nPI-style time series)]
    STG[Staging / clean\ndedupe, key-map, validate]
    DT[dim_time]
    DTAG[dim_tag]
    DEQ[dim_equipment]
    DSH[dim_shift]
    FPR[(fact_process_readings)]
    FSS[(fact_shift_summary)]
    VIEWS[KPI views + procedures\nsql/views, sql/procedures]
    DQ[DQ engine\ndq/engine.py]
    DQR[(dq_results)]
    PBI[Power BI report\nbuilt by hand]

    SIM --> RAW
    RAW --> STG
    STG --> DT & DTAG & DEQ & DSH
    STG --> FPR
    DT & DTAG & DEQ --> FPR
    FPR --> FSS
    DSH --> FSS
    FSS --> VIEWS
    FPR --> VIEWS
    RAW --> DQ
    FPR --> DQ
    FSS --> DQ
    DQ --> DQR
    VIEWS --> PBI
    DQR --> PBI
```

## Layer responsibilities

| Layer | Artifact | Responsibility |
| --- | --- | --- |
| Source | `simulator/` | Correlated synthetic tags + seeded faults |
| Raw | `raw_historian` | PI-style landing zone with quality flags |
| Staging | `warehouse/load.py` | Dedupe, key-map, conform dimensions |
| Warehouse | `fact_*`, `dim_*` | Star schema |
| Semantic | `sql/views`, `warehouse/kpi.py` | KPI definitions |
| Quality | `dq/engine.py` -> `dq_results` | Rules + mass balance |
| Report | Power BI (`.pbix`) | Built by hand on the KPI layer |
