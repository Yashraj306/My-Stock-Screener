import math
import pandas as pd

from config import (
    ALLOW_MIS_LEVERAGE,
    CAPITAL,
    CAPITAL_CONFIGURED,
    MAX_ALLOCATION_PCT,
    MIS_LEVERAGE,
    RISK_PCT,
)
from tech import analyze, atr, clean_ohlcv, compute_indicators, cumulative_vwap, pct


def _to_fraction(val: float, default: float) -> float:
    try:
        f = float(val)
    except (TypeError, ValueError):
        return default
    if f <= 0:
        return 0.0
    return f / 100.0 if f > 1.0 else f


def position_size(
    entry: float,
    stop_loss: float,
    capital: float | None = None,
    risk_pct: float | None = None,
    max_allocation_pct: float | None = None,
    allow_leverage: bool | None = None,
    leverage: float | None = None,
) -> dict:
    cap = CAPITAL if capital is None else float(capital)
    cap_ok = (cap > 0.0) if capital is not None else bool(CAPITAL_CONFIGURED and CAPITAL > 0.0)

    r_frac = _to_fraction(RISK_PCT if risk_pct is None else risk_pct, 0.01)
    a_frac = _to_fraction(MAX_ALLOCATION_PCT if max_allocation_pct is None else max_allocation_pct, 0.25)

    lev_enabled = ALLOW_MIS_LEVERAGE if allow_leverage is None else bool(allow_leverage)
    lev_mult_raw = MIS_LEVERAGE if leverage is None else float(leverage)
    lev_mult = lev_mult_raw if (lev_enabled and lev_mult_raw > 1.0) else 1.0

    if not cap_ok or cap <= 0.0:
        return {
            "qty": 0,
            "exposure": 0.0,
            "est_margin": 0.0,
            "max_risk": 0.0,
            "sizing_status": "CAPITAL NOT CONFIGURED",
        }

    entry_f = float(entry or 0.0)
    sl_f = float(stop_loss or 0.0)
    risk_per_share = abs(entry_f - sl_f)
    if entry_f <= 0.0 or risk_per_share <= 0.0:
        return {
            "qty": 0,
            "exposure": 0.0,
            "est_margin": 0.0,
            "max_risk": 0.0,
            "sizing_status": "INVALID SL / ENTRY",
        }

    max_risk_budget = cap * r_frac
    max_notional_by_alloc = (cap * a_frac) * lev_mult

    qty_by_risk = math.floor(max_risk_budget / risk_per_share)
    qty_by_alloc = math.floor(max_notional_by_alloc / entry_f)
    qty = max(min(qty_by_risk, qty_by_alloc), 0)

    if qty <= 0:
        return {
            "qty": 0,
            "exposure": 0.0,
            "est_margin": 0.0,
            "max_risk": 0.0,
            "sizing_status": "SIZE TOO SMALL FOR RISK CAP",
        }

    exposure = round(qty * entry_f, 2)
    est_margin = round(exposure / lev_mult, 2)
    max_risk = round(qty * risk_per_share, 2)
    lev_label = f"MIS {lev_mult:g}x" if lev_mult > 1.0 else "1x Cash"

    return {
        "qty": qty,
        "exposure": exposure,
        "est_margin": est_margin,
        "max_risk": max_risk,
        "sizing_status": f"{lev_label} Assumed",
    }


def build_directional_levels(entry: float, atr_val: float, ema20: float, vwap: float, is_bull: bool) -> dict | None:
    c = float(entry or 0.0)
    if c <= 0.0:
        return None
    a = float(atr_val or 0.0)
    if a <= 0.0:
        a = max(c * 0.015, 0.05)

    if is_bull:
        sl = round(max(c - 1.5 * a, min(float(ema20 or c), float(vwap or c)) * 0.992), 2)
        if sl >= c:
            sl = round(max(c - 1.5 * a, 0.05), 2)
        if sl >= c:
            return None
        t1 = round(c + 1.8 * a, 2)
        t2 = round(c + 3.0 * a, 2)
    else:
        sl = round(min(c + 1.5 * a, max(float(ema20 or c), float(vwap or c)) * 1.008), 2)
        if sl <= c:
            sl = round(c + 1.5 * a, 2)
        if sl <= c:
            return None
        t1 = round(max(c - 1.8 * a, 0.05), 2)
        t2 = round(max(c - 3.0 * a, 0.05), 2)

    return {"stop_loss": sl, "target_1": t1, "target_2": t2}


__all__ = [
    "analyze",
    "atr",
    "build_directional_levels",
    "clean_ohlcv",
    "compute_indicators",
    "cumulative_vwap",
    "pct",
    "position_size",
]
