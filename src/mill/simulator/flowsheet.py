"""Clean process model: physically-correlated tag time series for a gold mill.

Design goals (the part of the brief that matters most):
  * Diurnal + shift-based patterns.
  * Correlated relationships, not independent noise:
      - finer grind (lower P80) lifts recovery,
      - higher throughput at fixed grind degrades recovery,
      - recovery tracks head grade,
      - mill power tracks throughput and grind fineness.
  * Planned + unplanned downtime where throughput -> 0 and dependent tags
    flatline (forward-filled), with ramp-up/ramp-down transitions.
  * Mass-balance consistency by construction:
      tails_grade = head_grade * (1 - recovery/100)
    so feed gold == recovered + tails to within measurement noise. Faults
    (injected later) are what break the balance.

Everything is seeded, so a given config reproduces byte-for-byte.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from ..config import Config

GOLD_OZ_PER_GRAM = 1.0 / 31.1034768  # troy ounces per gram

# Continuous tags produced as dense minute series (gold_poured is shift-grain).
CONTINUOUS_TAGS = [
    "feed_throughput_tph",
    "feed_grade_gpt",
    "mill_power_kw",
    "grind_p80_um",
    "mill_load_pct",
    "cyanide_dose_kgpt",
    "lime_dose_kgpt",
    "recovery_pct",
    "tails_grade_gpt",
]


@dataclass
class SimulationResult:
    idx: pd.DatetimeIndex
    wide: pd.DataFrame              # continuous tags, indexed by timestamp
    running: np.ndarray             # bool, True when milling
    shift_key: np.ndarray           # int per minute
    shift_name: np.ndarray          # 'Day' / 'Night' per minute
    crew: np.ndarray                # crew letter per minute
    shifts: list[dict]              # one row per shift instance (-> dim_shift)
    gold_per_shift: pd.DataFrame    # ground-truth oz per shift (feed/rec/tails/poured)
    interval_h: float


def _ar1(n: int, phi: float, sigma: float, rng: np.random.Generator) -> np.ndarray:
    """First-order autoregressive noise: smooth, realistic sensor wander."""
    eps = rng.normal(0.0, sigma, size=n)
    out = np.empty(n, dtype=float)
    out[0] = eps[0]
    for i in range(1, n):
        out[i] = phi * out[i - 1] + eps[i]
    return out


def _build_index(cfg: Config) -> pd.DatetimeIndex:
    n = int(round(cfg.duration_days * 86400 / cfg.interval_seconds))
    return pd.date_range(cfg.start, periods=n, freq=f"{cfg.interval_seconds}s")


def assign_shifts(idx: pd.DatetimeIndex, cfg: Config):
    """Map each minute to a shift instance (Day/Night, crew, shift_key)."""
    day_start = int(cfg.shifts["day_start_hour"])
    crews = list(cfg.shifts["crews"])
    hours = idx.hour.to_numpy()
    is_day = (hours >= day_start) & (hours < day_start + 12)

    # Shift start date: an early-morning night minute belongs to the prior day.
    norm = idx.normalize()
    prev = norm - pd.Timedelta(days=1)
    shift_date = np.where(hours < day_start, prev, norm)
    shift_date = pd.to_datetime(shift_date)

    ymd = (
        shift_date.year * 10000 + shift_date.month * 100 + shift_date.day
    ).to_numpy()
    shift_key = ymd * 10 + np.where(is_day, 0, 1)
    shift_name = np.where(is_day, "Day", "Night")

    # Deterministic crew rotation over the ordered sequence of shifts.
    order = pd.unique(shift_key)
    crew_map = {k: crews[i % len(crews)] for i, k in enumerate(order)}
    crew = np.array([crew_map[k] for k in shift_key])

    shifts: list[dict] = []
    for k in order:
        mask = shift_key == k
        ts = idx[mask]
        name = shift_name[mask][0]
        sdate = pd.Timestamp(shift_date[mask][0]).strftime("%Y-%m-%d")
        shifts.append(
            {
                "shift_key": int(k),
                "shift_date": sdate,
                "shift_name": str(name),
                "crew": crew_map[k],
                "start_ts": ts.min().to_pydatetime(),
                "end_ts": ts.max().to_pydatetime(),
            }
        )
    return shift_key, shift_name, crew, shifts


def _build_downtime(idx: pd.DatetimeIndex, cfg: Config, rng: np.random.Generator):
    """Return a production_factor in [0,1] (0 = down) with ramp transitions."""
    n = len(idx)
    factor = np.ones(n, dtype=float)
    interval_min = cfg.interval_seconds / 60.0
    ramp = max(1, int(cfg.downtime["ramp_minutes"] / interval_min))
    total_days = cfg.duration_days

    windows: list[tuple[int, int]] = []

    # Planned maintenance: roughly evenly spaced.
    n_planned = int(round(cfg.downtime["planned_per_week"] * total_days / 7.0))
    plan_len = int(cfg.downtime["planned_hours"] * 60 / interval_min)
    for j in range(n_planned):
        center = int((j + 0.5) / max(1, n_planned) * n)
        jitter = rng.integers(-plan_len, plan_len)
        s = max(0, center + jitter)
        windows.append((s, min(n, s + plan_len)))

    # Unplanned trips: Poisson count, exponential length.
    n_unplanned = rng.poisson(cfg.downtime["unplanned_per_week"] * total_days / 7.0)
    for _ in range(int(n_unplanned)):
        s = int(rng.integers(0, n))
        length = int(
            max(2, rng.exponential(cfg.downtime["unplanned_hours_mean"] * 60 / interval_min))
        )
        windows.append((s, min(n, s + length)))

    for s, e in windows:
        if e <= s:
            continue
        factor[s:e] = 0.0
        # ramp down before
        r0 = max(0, s - ramp)
        if s > r0:
            factor[r0:s] = np.minimum(factor[r0:s], np.linspace(1.0, 0.0, s - r0))
        # ramp up after
        r1 = min(n, e + ramp)
        if r1 > e:
            factor[e:r1] = np.minimum(factor[e:r1], np.linspace(0.0, 1.0, r1 - e))
    return factor


def _ffill_during_downtime(arr: np.ndarray, running: np.ndarray) -> np.ndarray:
    """Forward-fill values across downtime so dependent tags flatline."""
    out = arr.copy()
    last = np.nan
    for i in range(len(out)):
        if running[i]:
            last = out[i]
        else:
            if not np.isnan(last):
                out[i] = last
    return out


def simulate(cfg: Config) -> SimulationResult:
    rng = np.random.default_rng(cfg.seed)
    idx = _build_index(cfg)
    n = len(idx)
    interval_h = cfg.interval_seconds / 3600.0
    p = cfg.plant
    nameplate = cfg.nameplate_tph

    hours = idx.hour.to_numpy() + idx.minute.to_numpy() / 60.0
    shift_key, shift_name, crew, shifts = assign_shifts(idx, cfg)

    # --- Downtime / production factor ------------------------------------
    prod_factor = _build_downtime(idx, cfg, rng)
    running = prod_factor > 0.05

    # --- Throughput ------------------------------------------------------
    diurnal = 1.0 + 0.03 * np.sin(2 * np.pi * hours / 24.0 + 0.6)
    shift_bias = np.where(shift_name == "Day", 1.015, 0.985)  # day crew nudges harder
    thr_noise = 1.0 + _ar1(n, 0.92, 0.012, rng)
    throughput = nameplate * 0.93 * diurnal * shift_bias * thr_noise * prod_factor
    throughput = np.clip(throughput, 0.0, None)

    # --- Head grade: slow ore-zone trend + mean-reverting noise ----------
    # A low-frequency deterministic component mimics moving through ore zones
    # of differing grade over the campaign; OU noise adds short-term variation.
    # Together they give head grade a *visible* range so recovery is seen to
    # track it (the recovery-vs-grade scatter in the report).
    zones = p["head_grade_gpt"] + 0.30 * np.sin(2 * np.pi * np.arange(n) / (n / 2.5) + 0.7)
    g_noise = np.empty(n)
    g_noise[0] = 0.0
    theta, sigma_g = 0.0015, 0.06 * np.sqrt(2 * 0.0015)
    for i in range(1, n):
        g_noise[i] = g_noise[i - 1] - theta * g_noise[i - 1] + rng.normal(0, sigma_g)
    grade = np.clip(zones + g_noise, 0.2, 8.0)
    grade = _ffill_during_downtime(grade, running)

    # --- Grind P80: coarser at higher throughput -------------------------
    load_ratio = np.divide(throughput, nameplate, out=np.zeros(n), where=nameplate > 0)
    p80 = p["target_p80_um"] * (1.0 + 0.20 * (load_ratio - 0.93)) + _ar1(n, 0.9, 4.0, rng)
    p80 = np.clip(p80, 45.0, 260.0)
    p80 = _ffill_during_downtime(p80, running)

    # --- Mill power: specific energy * throughput, finer grind costs more -
    sed_eff = p["specific_energy_kwh_t"] + 0.020 * (p["target_p80_um"] - p80) + _ar1(n, 0.85, 0.15, rng)
    sed_eff = np.clip(sed_eff, 6.0, 22.0)
    power = sed_eff * throughput
    power = np.where(running, power, p["idle_power_kw"])

    # --- Mill load -------------------------------------------------------
    mill_load = p["mill_load_pct"] + _ar1(n, 0.9, 1.5, rng)
    mill_load = np.where(running, mill_load, 8.0)
    mill_load = np.clip(mill_load, 0.0, 45.0)

    # --- Recovery: tracks grade, grind, throughput -----------------------
    recovery = (
        p["base_recovery_pct"]
        + 2.0 * (grade - p["head_grade_gpt"])                 # higher grade -> better
        + 0.040 * (p["target_p80_um"] - p80)                  # finer grind -> better
        - 4.0 * (load_ratio - 0.93)                           # push tonnage -> worse
        + _ar1(n, 0.88, 0.20, rng)
    )
    recovery = np.clip(recovery, 80.0, 97.0)
    recovery = _ffill_during_downtime(recovery, running)

    # --- Tailings grade: mass-balance consistent + tiny meas. noise ------
    tails = grade * (1.0 - recovery / 100.0) + rng.normal(0, 0.002, n)
    tails = np.clip(tails, 0.0, None)
    tails = _ffill_during_downtime(tails, running)

    # --- Reagents --------------------------------------------------------
    cyanide = p["cyanide_dose_kgpt"] * (1.0 + _ar1(n, 0.9, 0.04, rng))
    lime = p["lime_dose_kgpt"] * (1.0 + _ar1(n, 0.9, 0.05, rng))
    cyanide = _ffill_during_downtime(np.clip(cyanide, 0.0, None), running)
    lime = _ffill_during_downtime(np.clip(lime, 0.0, None), running)

    wide = pd.DataFrame(
        {
            "feed_throughput_tph": throughput,
            "feed_grade_gpt": grade,
            "mill_power_kw": power,
            "grind_p80_um": p80,
            "mill_load_pct": mill_load,
            "cyanide_dose_kgpt": cyanide,
            "lime_dose_kgpt": lime,
            "recovery_pct": recovery,
            "tails_grade_gpt": tails,
        },
        index=idx,
    )

    # --- Per-shift gold accounting (ground truth) ------------------------
    tonnes_min = throughput * interval_h
    feed_g = tonnes_min * grade
    recovered_g = feed_g * recovery / 100.0
    tails_g = tonnes_min * tails
    gp = pd.DataFrame(
        {
            "shift_key": shift_key,
            "feed_oz": feed_g * GOLD_OZ_PER_GRAM,
            "recovered_oz": recovered_g * GOLD_OZ_PER_GRAM,
            "tails_oz": tails_g * GOLD_OZ_PER_GRAM,
        }
    )
    gold_per_shift = gp.groupby("shift_key", as_index=False).sum()
    gold_per_shift["poured_oz"] = gold_per_shift["recovered_oz"]  # ignore in-circuit lock-up

    return SimulationResult(
        idx=idx,
        wide=wide,
        running=running,
        shift_key=shift_key,
        shift_name=shift_name,
        crew=crew,
        shifts=shifts,
        gold_per_shift=gold_per_shift,
        interval_h=interval_h,
    )
