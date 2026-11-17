
"""
Stock Screener V2.0
Conservative Swing / BTST Setup Engine

Capital is optional.
No leverage assumptions.
No automatic order execution.
"""

import math
import numpy as np
import pandas as pd

from config import (
    SWING,
    CAPITAL,
    RISK_PCT,
    MAX_ALLOCATION_PCT,
    CAPITAL_CONFIGURED
)

from tech import analyze, atr, pct, clean_ohlcv


# ============================================================
# 1. POSITION SIZING
# ============================================================

def position_size(entry, stop, capital=None):
    """
    Conservative position sizing.

    Returns zero if capital is not configured.
    Quantity is restricted by both risk and allocation limits.
    """

    capital = CAPITAL if capital is None else capital

    if capital is None:
        return 0

    if not all(
        math.isfinite(float(x))
        for x in [entry, stop, capital]
    ):
        return 0

    if entry <= 0 or stop <= 0 or capital <= 0:
        return 0

    risk_per_share = abs(entry - stop)

    if risk_per_share <= 0:
        return 0

    risk_amount = capital * (RISK_PCT / 100.0)

    allocation_amount = (
        capital * MAX_ALLOCATION_PCT / 100.0
    )

    risk_qty = math.floor(
        risk_amount / risk_per_share
    )

    allocation_qty = math.floor(
        allocation_amount / entry
    )

    return max(
        0,
        min(risk_qty, allocation_qty)
    )


# ============================================================
# 2. SWING SETUP ENGINE
# ============================================================

def generate_setup(df, market_data=None, sector_data=None):
    """
    Generate daily timeframe Swing BUY / SELL candidates.

    Requires:
    - Valid OHLCV data
    - EMA20, EMA50, EMA200
    - RSI
    - ADX
    - ATR
    - Volume confirmation

    This function does not place orders.
    """

    data = clean_ohlcv(df)

    # Require sufficient history for EMA200.
    if data.empty or len(data) < 200:
        return None

    t = analyze(data)

    if not t:
        return None

    price = t.get("price")
    c_atr = t.get("atr")
    c_rsi = t.get("rsi")
    c_adx = t.get("adx")

    ema20 = t.get("ema20")
    ema50 = t.get("ema50")
    ema200 = t.get("ema200")

    volume_ratio = t.get("vr")

    required = [
        price, c_atr, c_rsi, c_adx,
        ema20, ema50, ema200, volume_ratio
    ]

    if any(
        value is None or not math.isfinite(float(value))
        for value in required
    ):
        return None

    if price <= 0 or c_atr <= 0:
        return None

    if c_adx < float(SWING.get("ADX_MIN", 18)):
        return None

    if volume_ratio < float(
        SWING.get("MIN_VOLUME_RATIO", 1.2)
    ):
        return None

    atr_pct = (c_atr / price) * 100

    if atr_pct > float(
        SWING.get("MAX_ATR_PERCENT", 8)
    ):
        return None

    # ------------------------------------------
    # Trend Structure
    # ------------------------------------------

    bullish_trend = (
        price > ema20 > ema50 > ema200
    )

    bearish_trend = (
        price < ema20 < ema50 < ema200
    )

    signal = None
    stop_loss = 0.0

    min_rsi = float(
        SWING.get("RSI_BULL_MIN", 50)
    )

    max_rsi = float(
        SWING.get("RSI_BULL_MAX", 72)
    )

    bear_min_rsi = float(
        SWING.get("RSI_BEAR_MIN", 28)
    )

    bear_max_rsi = float(
        SWING.get("RSI_BEAR_MAX", 50)
    )

    atr_multiplier = float(
        SWING.get("ATR_STOP_MULTIPLIER", 2.0)
    )

    target_1_r = float(
        SWING.get("TARGET_1_R", 1.5)
    )

    target_2_r = float(
        SWING.get("TARGET_2_R", 2.5)
    )

    # ------------------------------------------
    # Bullish Setup
    # ------------------------------------------

    if (
        bullish_trend
        and min_rsi <= c_rsi <= max_rsi
    ):

        signal = "BUY"

        stop_loss = round(
            price - (atr_multiplier * c_atr), 2
        )

        risk = price - stop_loss

    # ------------------------------------------
    # Bearish Setup
    # ------------------------------------------

    elif (
        bearish_trend
        and bear_min_rsi <= c_rsi <= bear_max_rsi
    ):

        signal = "SELL"

        stop_loss = round(
            price + (atr_multiplier * c_atr), 2
        )

        risk = stop_loss - price

    else:
        return None

    if risk <= 0:
        return None

    # ------------------------------------------
    # Targets
    # ------------------------------------------

    if signal == "BUY":

        target_1 = round(
            price + target_1_r * risk, 2
        )

        target_2 = round(
            price + target_2_r * risk, 2
        )

    else:

        target_1 = round(
            price - target_1_r * risk, 2
        )

        target_2 = round(
            price - target_2_r * risk, 2
        )

    # ------------------------------------------
    # Position Sizing
    # ------------------------------------------

    qty = position_size(price, stop_loss)

    capital_needed = round(qty * price, 2)

    if CAPITAL_CONFIGURED:
        sizing_status = (
            "SIZED"
            if qty > 0
            else "RISK_LIMIT_BLOCKED"
        )
    else:
        sizing_status = "CAPITAL_NOT_CONFIGURED"

    # ------------------------------------------
    # Output
    # ------------------------------------------

    return {
        "signal": signal,

        "strategy": "SWING",

        "entry": round(price, 2),

        "stop_loss": stop_loss,

        "target_1": target_1,

        "target_2": target_2,

        "risk_per_share": round(risk, 2),

        "rr_ratio": target_1_r,

        "qty": qty,

        "capital_needed": capital_needed,

        "risk_amount": round(qty * risk, 2),

        "sizing_status": sizing_status,

        "capital_configured": CAPITAL_CONFIGURED,

        "rsi": round(c_rsi, 2),

        "adx": round(c_adx, 2),

        "atr": round(c_atr, 2),

        "atr_pct": round(atr_pct, 2),

        "ema20": round(ema20, 2),

        "ema50": round(ema50, 2),

        "ema200": round(ema200, 2),

        "volume_ratio": round(volume_ratio, 2),

        "trade_execution": "SIGNAL_ONLY"
    }


# ============================================================
# 3. SUPPORT AND RESISTANCE LEVELS
# ============================================================

def levels(df):
    """Calculate previous-day pivot levels."""

    data = clean_ohlcv(df)

    if len(data) < 2:
        return {}

    prev = data.iloc[-2]

    high = float(prev["High"])
    low = float(prev["Low"])
    close = float(prev["Close"])

    pivot = (high + low + close) / 3

    r1 = (2 * pivot) - low
    s1 = (2 * pivot) - high

    r2 = pivot + (high - low)
    s2 = pivot - (high - low)

    return {
        "pivot": round(pivot, 2),
        "r1": round(r1, 2),
        "s1": round(s1, 2),
        "r2": round(r2, 2),
        "s2": round(s2, 2)
    }


# ============================================================
# 4. PRICE ACTION FLAGS
# ============================================================

def flags_for(df):
    """Detect volume spikes, inside bars and gaps."""

    data = clean_ohlcv(df)

    flags = []

    if len(data) < 21:
        return flags

    curr = data.iloc[-1]
    prev = data.iloc[-2]
    prior = data.iloc[-3]

    # Volume spike compared with previous 20 candles.
    avg_volume = data["Volume"].iloc[-21:-1].mean()

    if avg_volume > 0:
        if curr["Volume"] >= 2 * avg_volume:
            flags.append("HIGH_VOL")

    # Inside Bar
    inside_bar = (
        prev["High"] < prior["High"]
        and prev["Low"] > prior["Low"]
    )

    if inside_bar:

        if curr["Close"] > prev["High"]:
            flags.append("INSIDE_BAR_BULLISH_BO")

        elif curr["Close"] < prev["Low"]:
            flags.append("INSIDE_BAR_BEARISH_BD")

    # Gap
    gap_pct = pct(
        curr["Open"],
        prev["Close"]
    )

    if gap_pct > 1.2:
        flags.append("GAP_UP")

    elif gap_pct < -1.2:
        flags.append("GAP_DOWN")

    return flags


# ============================================================
# 5. PRE-RALLY DETECTION
# ============================================================

def prerally(df):
    """Detect possible volatility contraction setups."""

    data = clean_ohlcv(df)

    if len(data) < 50:
        return False

    t = analyze(data)

    if not t:
        return False

    ema20 = t.get("ema20")
    rsi_value = t.get("rsi")
    price = t.get("price")

    if (
        ema20 is None
        or rsi_value is None
        or price is None
        or price <= 0
    ):
        return False

    recent_range = (
        data["High"].iloc[-5:]
        - data["Low"].iloc[-5:]
    ).mean()

    previous_range = (
        data["High"].iloc[-20:-5]
        - data["Low"].iloc[-20:-5]
    ).mean()

    if previous_range <= 0:
        return False

    contraction = (
        recent_range < previous_range * 0.75
    )

    near_ema = (
        abs(price - ema20) / price < 0.015
    )

    momentum_building = 48 <= rsi_value <= 62

    return bool(
        contraction
        and near_ema
        and momentum_building
    )


# ============================================================
# 6. BTST CANDIDATE FILTER
# ============================================================

def btst_gate(df):
    """
    Identify possible BTST candidates.

    This is a watchlist filter, not an overnight
    execution recommendation.
    """

    data = clean_ohlcv(df)

    if len(data) < 11:
        return False

    curr = data.iloc[-1]

    day_range = curr["High"] - curr["Low"]

    if day_range <= 0:
        return False

    close_position = (
        curr["Close"] - curr["Low"]
    ) / day_range

    previous_average_volume = (
        data["Volume"].iloc[-11:-1].mean()
    )

    if previous_average_volume <= 0:
        return False

    volume_surge = (
        curr["Volume"] >= previous_average_volume * 1.3
    )

    return bool(
        close_position >= 0.80
        and volume_surge
    )


# ============================================================
# 7. SIGNAL GRADING
# ============================================================

def grade(score):
    """Classify a setup score."""

    if score >= 85:
        return "A+"

    if score >= 75:
        return "A"

    if score >= 65:
        return "B"

    if score >= 50:
        return "C"

    return "NO TRADE"
