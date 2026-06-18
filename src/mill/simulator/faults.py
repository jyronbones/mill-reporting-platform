"""Deliberate data-quality fault injection.

Every fault is recorded to a ground-truth event list (-> ``fault_log``) so the
test suite can assert the DQ engine catches *exactly* what was injected.

Fault families:
  out_of_range   impossible values (recovery>100 or <0, negative mill power)
  stuck_sensor   a tag frozen at one value for a long run
  gap            dropped timestamps (missing data)
  duplicate      duplicated timestamps
  bad_flag       historian Bad/Questionable/Substituted quality flags
  mass_balance   shift-level metal-accounting break (tails grade biased high)

Range/stuck/mass-balance faults are applied to the dense arrays before melting;
gap/duplicate/bad_flag are applied to the long form. Stuck and range faults are
deliberately confined to tags that do NOT enter the gold balance, and the
mass-balance break is the only fault that perturbs it — so reconciliation flags
exactly the injected shifts and nothing else.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import Config
from .flowsheet import GOLD_OZ_PER_GRAM, SimulationResult

# Tags safe to corrupt without disturbing the gold mass balance.
_NON_BALANCE_TAGS = [
    "mill_power_kw",
    "grind_p80_um",
    "mill_load_pct",
    "cyanide_dose_kgpt",
    "lime_dose_kgpt",
]
_BAD_FLAGS = ["Bad", "Questionable", "Substituted"]


def _pois(rng: np.random.Generator, rate: float) -> int:
    return int(rng.poisson(max(0.0, rate)))


def inject(sim: SimulationResult, cfg: Config):
    """Return (long_df, fault_events) with faults applied.

    long_df columns: tag_name, timestamp, value, quality_flag
    fault_events: list of dicts matching the fault_log table.
    """
    rng = np.random.default_rng(cfg.seed + 1)
    f = cfg.faults
    days = cfg.duration_days
    idx = sim.idx
    n = len(idx)
    running = sim.running
    running_pos = np.flatnonzero(running)
    events: list[dict] = []

    cols = {c: sim.wide[c].to_numpy().copy() for c in sim.wide.columns}

    # --- out_of_range -----------------------------------------------------
    for _ in range(_pois(rng, f["out_of_range_per_day"] * days)):
        kind = rng.choice(["rec_hi", "rec_lo", "pwr_neg"])
        i = int(rng.choice(running_pos))
        if kind == "rec_hi":
            tag, val = "recovery_pct", float(rng.uniform(101, 130))
        elif kind == "rec_lo":
            tag, val = "recovery_pct", float(rng.uniform(-10, -1))
        else:
            tag, val = "mill_power_kw", float(rng.uniform(-2000, -100))
        cols[tag][i] = val
        events.append(
            {
                "fault_type": "out_of_range",
                "tag_name": tag,
                "ts_start": idx[i].to_pydatetime(),
                "ts_end": idx[i].to_pydatetime(),
                "shift_key": int(sim.shift_key[i]),
                "detail": f"forced {tag}={val:.1f} (out of expected range)",
            }
        )

    # --- stuck_sensor -----------------------------------------------------
    stuck_len = int(f["stuck_minutes"] * 60 / cfg.interval_seconds)
    for _ in range(_pois(rng, f["stuck_sensor_per_week"] * days / 7.0)):
        tag = str(rng.choice(_NON_BALANCE_TAGS))
        # Find a start where the whole window stays running.
        start = None
        for _try in range(20):
            cand = int(rng.integers(0, max(1, n - stuck_len)))
            if running[cand : cand + stuck_len].all():
                start = cand
                break
        if start is None:
            continue
        frozen = float(cols[tag][start])
        cols[tag][start : start + stuck_len] = frozen
        events.append(
            {
                "fault_type": "stuck_sensor",
                "tag_name": tag,
                "ts_start": idx[start].to_pydatetime(),
                "ts_end": idx[start + stuck_len - 1].to_pydatetime(),
                "shift_key": int(sim.shift_key[start]),
                "detail": f"{tag} frozen at {frozen:.2f} for {stuck_len} samples",
            }
        )

    # --- mass_balance break (shift level) --------------------------------
    bias = float(f["massbalance_tails_bias"])
    for shift in sim.shifts:
        if rng.random() < f["massbalance_shift_rate"]:
            k = shift["shift_key"]
            pos = np.flatnonzero(sim.shift_key == k)
            cols["tails_grade_gpt"][pos] *= bias
            events.append(
                {
                    "fault_type": "mass_balance",
                    "tag_name": "tails_grade_gpt",
                    "ts_start": shift["start_ts"],
                    "ts_end": shift["end_ts"],
                    "shift_key": int(k),
                    "detail": f"tails grade biased x{bias} -> feed != recovered + tails",
                }
            )

    # --- melt to long form -----------------------------------------------
    wide2 = pd.DataFrame(cols, index=idx)
    long = (
        wide2.reset_index()
        .rename(columns={"index": "timestamp"})
        .melt(id_vars="timestamp", var_name="tag_name", value_name="value")
    )
    long["quality_flag"] = "Good"

    # gold_poured_oz: one value per shift, stamped at shift end.
    poured_rows = []
    poured_map = dict(
        zip(sim.gold_per_shift["shift_key"], sim.gold_per_shift["poured_oz"])
    )
    for shift in sim.shifts:
        poured_rows.append(
            {
                "timestamp": shift["end_ts"],
                "tag_name": "gold_poured_oz",
                "value": float(poured_map.get(shift["shift_key"], 0.0)),
                "quality_flag": "Good",
            }
        )
    long = pd.concat([long, pd.DataFrame(poured_rows)], ignore_index=True)
    long["timestamp"] = pd.to_datetime(long["timestamp"])

    # --- gap: drop a contiguous run of one tag ----------------------------
    drop_idx = np.zeros(len(long), dtype=bool)
    gap_len = pd.Timedelta(minutes=f["gap_minutes"])
    for _ in range(_pois(rng, f["gap_per_day"] * days)):
        tag = str(rng.choice(list(sim.wide.columns)))
        i = int(rng.choice(running_pos))
        s = idx[i]
        e = s + gap_len
        m = (long["tag_name"] == tag) & (long["timestamp"] >= s) & (long["timestamp"] < e)
        if m.any():
            drop_idx |= m.to_numpy()
            events.append(
                {
                    "fault_type": "gap",
                    "tag_name": tag,
                    "ts_start": s.to_pydatetime(),
                    "ts_end": e.to_pydatetime(),
                    "shift_key": int(sim.shift_key[i]),
                    "detail": f"{int(m.sum())} samples dropped from {tag}",
                }
            )

    # --- bad_flag: corrupt the historian quality flag ---------------------
    flags = long["quality_flag"].to_numpy().copy()
    keep_pos = np.flatnonzero(~drop_idx)
    for _ in range(_pois(rng, f["bad_flag_per_day"] * days)):
        tag = str(rng.choice(list(sim.wide.columns)))
        i = int(rng.choice(running_pos))
        s = idx[i]
        e = s + pd.Timedelta(minutes=5)
        m = (long["tag_name"] == tag) & (long["timestamp"] >= s) & (long["timestamp"] < e)
        m = m.to_numpy() & ~drop_idx
        if m.any():
            flag = str(rng.choice(_BAD_FLAGS))
            flags[m] = flag
            events.append(
                {
                    "fault_type": "bad_flag",
                    "tag_name": tag,
                    "ts_start": s.to_pydatetime(),
                    "ts_end": e.to_pydatetime(),
                    "shift_key": int(sim.shift_key[i]),
                    "detail": f"{int(m.sum())} samples flagged {flag}",
                }
            )
    long["quality_flag"] = flags

    long = long.loc[~drop_idx].reset_index(drop=True)

    # --- duplicate: re-emit a few timestamps with a slightly different value
    dup_rows = []
    for _ in range(_pois(rng, f["duplicate_per_day"] * days)):
        tag = str(rng.choice(list(sim.wide.columns)))
        i = int(rng.choice(running_pos))
        s = idx[i]
        sub = long[(long["tag_name"] == tag) & (long["timestamp"] == s)]
        if sub.empty:
            continue
        row = sub.iloc[0].to_dict()
        row["value"] = float(row["value"]) * (1.0 + rng.normal(0, 0.01))
        dup_rows.append(row)
        events.append(
            {
                "fault_type": "duplicate",
                "tag_name": tag,
                "ts_start": s.to_pydatetime(),
                "ts_end": s.to_pydatetime(),
                "shift_key": int(sim.shift_key[i]),
                "detail": f"duplicate timestamp emitted for {tag}",
            }
        )
    if dup_rows:
        long = pd.concat([long, pd.DataFrame(dup_rows)], ignore_index=True)

    long = long.sort_values(["timestamp", "tag_name"]).reset_index(drop=True)
    return long, events
