import math
import numpy as np
import pandas as pd

# Safe fallback imports from config
try:
    from config import SWING, CAPITAL, RISK_PCT, MAX_ALLOCATION_PCT
except ImportError:
    SWING = {"min_adx": 20, "min_rsi": 45, "max_rsi": 70}
    CAPITAL = 100000
    RISK_PCT = 1.0
    MAX_ALLOCATION_PCT = 20.0

from tech import analyze, atr, pct


# =====================================================================
# 1. POSITION SIZING & SWING ENGINE
# =====================================================================

def position_size(entry, stop, capital=CAPITAL):
    """Calculates quantity based on capital risk and max capital allocation."""
    if entry <= 0 or stop <= 0:
        return 0
    risk_amount = capital * (RISK_PCT / 100.0)
    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0:
        return 0
    risk_qty = int(risk_amount / risk_per_share)
    allocation_amount = capital * (MAX_ALLOCATION_PCT / 100.0)
    allocation_qty = int(allocation_amount / entry)
    return max(0, min(risk_qty, allocation_qty))


def generate_setup(df, market_data=None, sector_data=None):
    """
    Evaluates Swing Trading BUY / SELL setups using trend, momentum, volume,
    and volatility targets.
    """
    t = analyze(df)
    if not t or len(df) < 20:
        return None

    price = t.get("price", 0.0)
    c_atr = t.get("atr", 0.0)
    c_rsi = t.get("rsi", 50.0)
    c_adx = t.get("adx", 0.0)
    vr = t.get("vr", 1.0)
    ema20 = t.get("ema20", price)
    ema50 = t.get("ema50", price)
    ema200 = t.get("ema200", price)

    if price <= 0 or c_atr <= 0:
        return None

    # Trend structure
    bullish_trend = price > ema20 > ema50 and price > ema200
    bearish_trend = price < ema20 < ema50 and price < ema200

    signal = None
    stop_loss = 0.0
    target_1 = 0.0
    target_2 = 0.0
    rr_ratio = 0.0

    min_adx = SWING.get("min_adx", 20) if isinstance(SWING, dict) else 20
    min_rsi = SWING.get("min_rsi", 45) if isinstance(SWING, dict) else 45
    max_rsi = SWING.get("max_rsi", 70) if isinstance(SWING, dict) else 70

    # Bullish Swing Setup
    if bullish_trend and (min_rsi <= c_rsi <= max_rsi) and (c_adx >= min_adx) and (vr >= 1.0):
        signal = "BUY"
        stop_loss = round(max(ema50, price - (1.5 * c_atr)), 2)
        risk = price - stop_loss
        if risk > 0:
            target_1 = round(price + (1.5 * risk), 2)
            target_2 = round(price + (2.5 * risk), 2)
            rr_ratio = round((target_1 - price) / risk, 2)

    # Bearish Swing Setup
    elif bearish_trend and (c_rsi <= 40) and (c_adx >= min_adx) and (vr >= 1.0):
        signal = "SELL"
        stop_loss = round(min(ema50, price + (1.5 * c_atr)), 2)
        risk = stop_loss - price
        if risk > 0:
            target_1 = round(price - (1.5 * risk), 2)
            target_2 = round(price - (2.5 * risk), 2)
            rr_ratio = round((price - target_1) / risk, 2)

    if not signal or rr_ratio <= 0:
        return None

    qty = position_size(price, stop_loss)
    return {
        "signal": signal,
        "entry": round(price, 2),
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "rr_ratio": rr_ratio,
        "qty": qty,
        "capital_needed": round(qty * price, 2),
        "rsi": round(c_rsi, 1),
        "adx": round(c_adx, 1),
        "volume_ratio": round(vr, 2)
    }


# =====================================================================
# 2. BACKWARD-COMPATIBLE WORKFLOW ENGINE (PRE/POST, BTST, FLAGS)
# =====================================================================

def levels(df):
    """Calculates key pivot points and support/resistance levels."""
    if df is None or len(df) < 2:
        return {}
    prev = df.iloc[-2]
    h, l, c = prev["High"], prev["Low"], prev["Close"]
    p = (h + l + c) / 3.0
    r1 = (2 * p) - l
    s1 = (2 * p) - h
    r2 = p + (h - l)
    s2 = p - (h - l)
    return {
        "pivot": round(p, 2),
        "r1": round(r1, 2),
        "s1": round(s1, 2),
        "r2": round(r2, 2),
        "s2": round(s2, 2)
    }


def flags_for(df):
    """Detects technical signals like momentum spikes, breakouts, reversals."""
    flags = []
    if df is None or len(df) < 5:
        return flags
    curr = df.iloc[-1]
    prev = df.iloc[-2]

    # Volume spike
    avg_vol = df["Volume"].tail(20).mean()
    if curr["Volume"] > 2 * avg_vol and avg_vol > 0:
        flags.append("HIGH_VOL")

    # Inside Bar Breakout
    if prev["High"] < df.iloc[-3]["High"] and prev["Low"] > df.iloc[-3]["Low"]:
        if curr["Close"] > prev["High"]:
            flags.append("INSIDE_BAR_BULLISH_BO")
        elif curr["Close"] < prev["Low"]:
            flags.append("INSIDE_BAR_BEARISH_BD")

    # Gap open
    gap_pct = pct(curr["Open"], prev["Close"])
    if gap_pct > 1.2:
        flags.append("GAP_UP")
    elif gap_pct < -1.2:
        flags.append("GAP_DOWN")

    return flags


def prerally(df):
    """Identifies stocks consolidating right before a rally."""
    if df is None or len(df) < 20:
        return False
    t = analyze(df)
    if not t:
        return False
    # Low ATR contraction + near 20 EMA + RSI building
    recent_atr = df["High"].tail(5) - df["Low"].tail(5)
    prior_atr = df["High"].tail(20).head(15) - df["Low"].tail(20).head(15)
    contraction = recent_atr.mean() < (prior_atr.mean() * 0.75)
    near_ema = abs(t["price"] - t["ema20"]) / t["price"] < 0.015
    return contraction and near_ema and (48 <= t["rsi"] <= 62)


def btst_gate(df):
    """Filter candidate stocks suitable for BTST execution."""
    if df is None or len(df) < 5:
        return False
    curr = df.iloc[-1]
    day_range = curr["High"] - curr["Low"]
    if day_range <= 0:
        return False
    close_near_high = (curr["Close"] - curr["Low"]) / day_range > 0.80
    vol_surge = curr["Volume"] > df["Volume"].tail(10).mean() * 1.3
    return close_near_high and vol_surge


def grade(score):
    """Categorizes qualitative setup score."""
    if score >= 80:
        return "A+"
    if score >= 65:
        return "A"
    if score >= 50:
        return "B"
    return "C"
