"""KPI formulas as small, independently-testable pure functions.

These are the canonical Python definitions used by the warehouse loader to
populate ``fact_shift_summary``. The SQL views in ``sql/views`` mirror the same
formulas for ad-hoc Power BI access; the pytest suite pins the numbers here.

Conventions:
  throughput  t/h          power     kW
  grade/tails g/t          interval_h hours per sample
  gold mass   troy ounces  (1 oz = 31.1034768 g)
"""
from __future__ import annotations

import numpy as np

GOLD_OZ_PER_GRAM = 1.0 / 31.1034768


def tonnes_milled(throughput_tph: np.ndarray, interval_h: float) -> float:
    """Total tonnes = integral of throughput over time."""
    return float(np.nansum(throughput_tph) * interval_h)


def gold_oz(throughput_tph: np.ndarray, grade_gpt: np.ndarray, interval_h: float) -> float:
    """Contained gold (oz) in a stream = tonnes * grade, summed."""
    tonnes = throughput_tph * interval_h
    grams = np.nansum(tonnes * grade_gpt)
    return float(grams * GOLD_OZ_PER_GRAM)


def recovered_oz(
    throughput_tph: np.ndarray,
    grade_gpt: np.ndarray,
    recovery_pct: np.ndarray,
    interval_h: float,
) -> float:
    """Recovered gold (oz) = feed gold * recovery fraction, per sample."""
    tonnes = throughput_tph * interval_h
    grams = np.nansum(tonnes * grade_gpt * recovery_pct / 100.0)
    return float(grams * GOLD_OZ_PER_GRAM)


def weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    """Tonnage-weighted mean (used for recovery, grades, reagent doses)."""
    w = np.nan_to_num(weights, nan=0.0)
    v = np.asarray(values, dtype=float)
    mask = ~np.isnan(v) & (w > 0)
    if not mask.any() or w[mask].sum() == 0:
        return float("nan")
    return float(np.average(v[mask], weights=w[mask]))


def specific_energy(power_kw: np.ndarray, tonnes_total: float, interval_h: float) -> float:
    """Specific energy (kWh/t) = energy consumed / tonnes processed."""
    if tonnes_total <= 0:
        return float("nan")
    energy_kwh = np.nansum(power_kw) * interval_h
    return float(energy_kwh / tonnes_total)


def reagent_per_tonne(dose_kgpt: np.ndarray, throughput_tph: np.ndarray) -> float:
    """Tonnage-weighted average reagent dose (kg/t)."""
    return weighted_mean(dose_kgpt, throughput_tph)


def availability_pct(running_minutes: float, scheduled_minutes: float) -> float:
    """Availability = uptime / scheduled time."""
    if scheduled_minutes <= 0:
        return float("nan")
    return 100.0 * running_minutes / scheduled_minutes


def utilization_pct(avg_running_throughput_tph: float, nameplate_tph: float) -> float:
    """Throughput utilization (the OEE performance factor) = rate / nameplate."""
    if nameplate_tph <= 0 or np.isnan(avg_running_throughput_tph):
        return float("nan")
    return 100.0 * avg_running_throughput_tph / nameplate_tph


def oee_pct(availability: float, utilization: float, quality: float) -> float:
    """OEE-style composite = availability x utilization x quality."""
    return availability * utilization * quality / 10_000.0
