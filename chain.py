"""
Options-chain analytics for My Stock Screener.

Public interface retained:
    analyze_chain(df) -> dict | None

Expected input columns:
    Required: strike, ce_oi, pe_oi
    Optional: spot, ce_poi, pe_poi, ce_iv, pe_iv

Return keys are kept compatible with the existing repository:
    spot, oi_pcr, intraday_pcr, ce_change_oi, pe_change_oi,
    max_pain, resistance_strike, support_strike, atm_iv,
    sentiment, oi_action

No missing PCR, IV, spot, OI, or other market data is fabricated.
This module performs analytics only; it never places orders.
"""

import logging
import math

import numpy as np
import pandas as pd


logger = logging.getLogger(__name__)


def _num(value):
    """Convert a value to a finite float, otherwise return None."""
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _positive_number(value):
    number = _num(value)
    return number if number is not None and number > 0 else None


def _safe_sum(series):
    values = pd.to_numeric(series, errors="coerce")
    values = values.replace([np.inf, -np.inf], np.nan).dropna()
    if values.empty:
        return 0.0
    return float(values.clip(lower=0).sum())


def _get_spot(data):
    if "spot" not in data.columns:
        return None

    values = pd.to_numeric(data["spot"], errors="coerce")
    values = values.replace([np.inf, -np.inf], np.nan)
    values = values[values > 0]
    if values.empty:
        return None

    # Option-chain rows normally repeat the same underlying spot.
    # Median avoids choosing an anomalous row if the feed contains noise.
    return float(values.median())


def _calculate_max_pain(strikes, ce_oi, pe_oi):
    """
    Calculate the strike with the lowest theoretical expiry payout.

    This is a conventional OI-based max-pain estimate, not a prediction.
    """
    if len(strikes) == 0 or (ce_oi.sum() + pe_oi.sum()) <= 0:
        return None

    payouts = []
    for settlement in strikes:
        call_payout = np.maximum(settlement - strikes, 0.0) * ce_oi
        put_payout = np.maximum(strikes - settlement, 0.0) * pe_oi
        payouts.append(float(call_payout.sum() + put_payout.sum()))

    if not payouts or not np.isfinite(payouts).any():
        return None

    return float(strikes[int(np.argmin(payouts))])


def _calculate_intraday_oi(data):
    """
    Return (PCR, CE OI change, PE OI change).

    Intraday PCR is unavailable unless both previous-OI columns are present
    and complete, and CE OI change is positive. A non-positive denominator
    is not replaced with a synthetic value.
    """
    required = {"ce_poi", "pe_poi"}
    if not required.issubset(data.columns):
        return None, None, None

    previous_ce = pd.to_numeric(data["ce_poi"], errors="coerce")
    previous_pe = pd.to_numeric(data["pe_poi"], errors="coerce")

    previous_ce = previous_ce.replace([np.inf, -np.inf], np.nan)
    previous_pe = previous_pe.replace([np.inf, -np.inf], np.nan)

    valid = previous_ce.notna() & previous_pe.notna()
    if not valid.all():
        return None, None, None

    ce_change = float(
        (data["ce_oi"] - previous_ce).sum()
    )
    pe_change = float(
        (data["pe_oi"] - previous_pe).sum()
    )

    if not math.isfinite(ce_change) or not math.isfinite(pe_change):
        return None, None, None

    intraday_pcr = (
        round(pe_change / ce_change, 2)
        if ce_change > 0
        else None
    )

    return intraday_pcr, ce_change, pe_change


def _calculate_atm_iv(data, spot):
    """Average available positive CE/PE IV at the nearest strike to spot."""
    if spot is None:
        return None

    strike_distance = (data["strike"] - spot).abs()
    if strike_distance.empty:
        return None

    atm_index = strike_distance.idxmin()
    atm_row = data.loc[atm_index]

    iv_values = []
    for column in ("ce_iv", "pe_iv"):
        if column not in data.columns:
            continue
        value = _positive_number(atm_row.get(column))
        if value is not None:
            iv_values.append(value)

    if not iv_values:
        return None

    return round(sum(iv_values) / len(iv_values), 2)


def analyze_chain(df):
    """
    Analyze an option-chain DataFrame without inventing missing values.

    Returns None for invalid/empty input. Otherwise returns a dictionary
    with the same public keys as the original repository implementation.
    """
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None

    required = ["strike", "ce_oi", "pe_oi"]
    if not all(column in df.columns for column in required):
        logger.warning("Option chain missing required columns: %s", required)
        return None

    data = df.copy()

    for column in required:
        data[column] = pd.to_numeric(data[column], errors="coerce")
        data[column] = data[column].replace([np.inf, -np.inf], np.nan)

    data = data.dropna(subset=required)
    data = data[data["strike"] > 0].copy()

    if data.empty:
        return None

    # OI cannot be negative. Invalid negative values are clipped to zero,
    # but missing OI rows have already been removed.
    data["ce_oi"] = data["ce_oi"].clip(lower=0)
    data["pe_oi"] = data["pe_oi"].clip(lower=0)

    data = (
        data.sort_values("strike")
        .drop_duplicates(subset=["strike"], keep="last")
        .reset_index(drop=True)
    )

    if data.empty:
        return None

    strikes = data["strike"].to_numpy(dtype=float)
    ce_oi = data["ce_oi"].to_numpy(dtype=float)
    pe_oi = data["pe_oi"].to_numpy(dtype=float)

    total_ce = _safe_sum(data["ce_oi"])
    total_pe = _safe_sum(data["pe_oi"])

    oi_pcr = (
        round(total_pe / total_ce, 2)
        if total_ce > 0
        else None
    )

    intraday_pcr, ce_change, pe_change = _calculate_intraday_oi(data)

    max_pain = _calculate_max_pain(strikes, ce_oi, pe_oi)

    resistance = None
    if total_ce > 0:
        resistance = _num(
            data.loc[data["ce_oi"].idxmax(), "strike"]
        )

    support = None
    if total_pe > 0:
        support = _num(
            data.loc[data["pe_oi"].idxmax(), "strike"]
        )

    spot = _get_spot(data)
    atm_iv = _calculate_atm_iv(data, spot)

    # Preserve the original sentiment thresholds and only classify when
    # both total OI PCR and intraday OI PCR are actually available.
    sentiment = "INSUFFICIENT_DATA"
    oi_action = "Insufficient option-chain data"

    if oi_pcr is not None and intraday_pcr is not None:
        if oi_pcr >= 1.15 and intraday_pcr >= 1.0:
            sentiment = "BULLISH"
            oi_action = "Put OI dominance with positive relative buildup"
        elif oi_pcr <= 0.80 and intraday_pcr <= 0.85:
            sentiment = "BEARISH"
            oi_action = "Call OI dominance with negative relative buildup"
        else:
            sentiment = "NEUTRAL"
            oi_action = "Mixed or balanced OI activity"

    return {
        "spot": round(spot, 2) if spot is not None else None,
        "oi_pcr": oi_pcr,
        "intraday_pcr": intraday_pcr,
        "ce_change_oi": ce_change,
        "pe_change_oi": pe_change,
        "max_pain": round(max_pain, 2) if max_pain is not None else None,
        "resistance_strike": round(resistance, 2) if resistance is not None else None,
        "support_strike": round(support, 2) if support is not None else None,
        "atm_iv": atm_iv,
        "sentiment": sentiment,
        "oi_action": oi_action,
    }
