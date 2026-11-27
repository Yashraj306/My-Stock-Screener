import datetime as dt
from typing import Any
import pandas as pd

from config import (
    ALLOW_MIS_LEVERAGE,
    CAPITAL,
    CAPITAL_CONFIGURED,
    MAX_ALLOCATION_PCT,
    MIS_LEVERAGE,
    RISK_PCT,
)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def position_size(entry: float, stop_loss: float, lot_size: int = 1) -> dict[str, Any]:
    """Calculates position quantity and risk/allocation figures respecting configured risk cap."""
    eff_lot = max(int(lot_size or 1), 1)
    px = float(entry or 0.0)
    sl = float(stop_loss or 0.0)
    if px <= 0:
        return {
            "qty": eff_lot,
            "est_margin": 0.0,
            "exposure": 0.0,
            "max_risk": 0.0,
            "sizing_status": "INVALID_ENTRY",
        }

    risk_per_share = abs(px - sl) if sl > 0 else px
    if risk_per_share <= 0:
        risk_per_share = max(px * 0.01, 0.05)

    if not CAPITAL_CONFIGURED or float(CAPITAL or 0.0) <= 0:
        qty = eff_lot
        exposure = round(qty * px, 2)
        max_risk = round(qty * risk_per_share, 2)
        return {
            "qty": qty,
            "est_margin": exposure,
            "exposure": exposure,
            "max_risk": max_risk,
            "sizing_status": "CAPITAL_NOT_CONFIGURED",
        }

    cap = float(CAPITAL)
    max_risk_amt = cap * float(RISK_PCT)
    max_alloc_amt = cap * float(MAX_ALLOCATION_PCT)
    lev = float(MIS_LEVERAGE) if ALLOW_MIS_LEVERAGE else 1.0
    max_exposure_amt = max_alloc_amt * max(lev, 1.0)

    raw_qty_by_risk = int(max_risk_amt // risk_per_share)
    raw_qty_by_alloc = int(max_exposure_amt // px) if px > 0 else eff_lot
    target_qty = min(raw_qty_by_risk, raw_qty_by_alloc)

    lots = max(target_qty // eff_lot, 1)
    qty = lots * eff_lot
    exposure = round(qty * px, 2)
    margin_needed = round(exposure / max(lev, 1.0), 2)
    max_risk = round(qty * risk_per_share, 2)

    return {
        "qty": qty,
        "est_margin": margin_needed,
        "exposure": exposure,
        "max_risk": max_risk,
        "sizing_status": "RISK_CAPPED",
    }


def _has_upcoming_event(event_meta: dict[str, Any] | None) -> bool:
    ev = event_meta or {}
    if bool(ev.get("earnings_tomorrow")) or bool(ev.get("ex_date_tomorrow")):
        return True
    next_ev_date = str(ev.get("next_event_date") or "").strip()[:10]
    if next_ev_date:
        try:
            ev_dt = dt.date.fromisoformat(next_ev_date)
            today_ist = dt.datetime.now(IST).date()
            if (ev_dt - today_ist).days in {0, 1}:
                return True
        except ValueError:
            pass
    return False


def btst_rally_score(
    row: dict[str, Any],
    df: pd.DataFrame | None = None,
    nifty_5d_pct: float = 0.0,
    sector_chg_pct: float = 0.0,
    event_meta: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Computes 0-100 bullish BTST gap-up/rally score and BTST targets/SL.
    Returns None if rejected by overbought/streak/event filters.
    """
    close = float(row.get("close") or row.get("entry") or 0.0)
    atr = float(row.get("atr") or 0.0)
    rsi = row.get("rsi")
    if close <= 0 or atr <= 0:
        return None
    if rsi is not None and not pd.isna(rsi) and float(rsi) > 80.0:
        return None
    if _has_upcoming_event(event_meta):
        return None

    # Reject >= 3 consecutive up days
    if df is not None and not df.empty and len(df) >= 4:
        closes = pd.to_numeric(df["Close"], errors="coerce").dropna().tail(4).tolist()
        if len(closes) == 4 and closes[1] > closes[0] and closes[2] > closes[1] and closes[3] > closes[2]:
            return None

    reasons: list[str] = []
    score = 0.0

    # 1. close_position_ratio >= 0.8 (+20)
    cpr = float(row.get("close_pos_ratio") or 0.0)
    if cpr >= 0.8:
        score += 20.0
        reasons.append(f"CPR {cpr:.2f}>=0.8 (+20)")

    # 2. volume >= 1.5x 20d avg (+15)
    vol_ratio = float(row.get("vol_ratio") or 0.0)
    if vol_ratio >= 1.5:
        score += 15.0
        reasons.append(f"Vol {vol_ratio:.2f}x>=1.5x (+15)")

    # 3. close above 20-day high (+20) or NR7/ATR contraction then breakout (+15)
    broke_20d_high = False
    nr7_breakout = False
    if df is not None and not df.empty and len(df) >= 21:
        highs = pd.to_numeric(df["High"], errors="coerce").dropna()
        lows = pd.to_numeric(df["Low"], errors="coerce").dropna()
        if len(highs) >= 21:
            prior_20_high = float(highs.iloc[-21:-1].max())
            if close > prior_20_high:
                broke_20d_high = True
        if len(highs) >= 8 and len(lows) >= 8:
            ranges = (highs.tail(8) - lows.tail(8)).dropna()
            if len(ranges) == 8:
                prior_7_ranges = ranges.iloc[:-1]
                nr7 = float(ranges.iloc[-1]) < float(prior_7_ranges.min())
                atr_contracted = float(prior_7_ranges.mean()) <= atr * 1.05
                if nr7 and atr_contracted and close >= float(highs.iloc[-2]):
                    nr7_breakout = True

    if broke_20d_high:
        score += 20.0
        reasons.append("20d high breakout (+20)")
    elif nr7_breakout:
        score += 15.0
        reasons.append("NR7/ATR squeeze breakout (+15)")

    # 4. close > EMA20 > EMA50 (+10)
    ema20 = row.get("ema20")
    ema50 = row.get("ema50")
    if ema20 is not None and ema50 is not None and close > float(ema20) > float(ema50):
        score += 10.0
        reasons.append("Close>EMA20>EMA50 (+10)")

    # 5. relative strength vs Nifty over 5d (+10)
    stock_5d = float(row.get("ret_5d_pct") or 0.0)
    if (stock_5d - float(nifty_5d_pct or 0.0)) > 0.0:
        score += 10.0
        reasons.append("5d RS vs Nifty (+10)")

    # 6. sector index up (+10)
    if float(sector_chg_pct or 0.0) > 0.0:
        score += 10.0
        reasons.append(f"Sector up {float(sector_chg_pct):+.2f}% (+10)")

    day_low = float(row.get("day_low") or close)
    target_1 = round(close + 0.5 * atr, 2)
    target_2 = round(close + 0.8 * atr, 2)
    sl = round(max(close - 1.0 * atr, day_low), 2)

    return {
        "btst_score": round(min(100.0, score), 2),
        "btst_reasons": reasons,
        "entry": round(close, 2),
        "target_1": target_1,
        "target_2": target_2,
        "stop_loss": sl,
    }


def btst_bearish_score(
    row: dict[str, Any],
    df: pd.DataFrame | None = None,
    nifty_5d_pct: float = 0.0,
    sector_chg_pct: float = 0.0,
    event_meta: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Computes 0-100 bearish overnight/BTST weakness score and targets/SL.
    Returns None if rejected by extreme oversold/streak/event filters.
    """
    close = float(row.get("close") or row.get("entry") or 0.0)
    atr = float(row.get("atr") or 0.0)
    rsi = row.get("rsi")
    if close <= 0 or atr <= 0:
        return None
    if rsi is not None and not pd.isna(rsi) and float(rsi) < 20.0:
        return None
    if _has_upcoming_event(event_meta):
        return None

    # Reject >= 3 consecutive down days
    if df is not None and not df.empty and len(df) >= 4:
        closes = pd.to_numeric(df["Close"], errors="coerce").dropna().tail(4).tolist()
        if len(closes) == 4 and closes[1] < closes[0] and closes[2] < closes[1] and closes[3] < closes[2]:
            return None

    reasons: list[str] = []
    score = 0.0

    # 1. Weak close near low: close_position_ratio <= 0.25 (+20)
    cpr = float(row.get("close_pos_ratio") or 0.5)
    if cpr <= 0.25:
        score += 20.0
        reasons.append(f"Weak CPR {cpr:.2f}<=0.25 (+20)")

    # 2. Volume >= 1.3x 20d avg (+15)
    vol_ratio = float(row.get("vol_ratio") or 0.0)
    if vol_ratio >= 1.3:
        score += 15.0
        reasons.append(f"Vol {vol_ratio:.2f}x>=1.3x (+15)")

    # 3. 20-day low breakdown (+20) or NR7/ATR squeeze breakdown (+15)
    broke_20d_low = False
    nr7_breakdown = False
    if df is not None and not df.empty and len(df) >= 21:
        highs = pd.to_numeric(df["High"], errors="coerce").dropna()
        lows = pd.to_numeric(df["Low"], errors="coerce").dropna()
        if len(lows) >= 21:
            prior_20_low = float(lows.iloc[-21:-1].min())
            if close < prior_20_low:
                broke_20d_low = True
        if len(highs) >= 8 and len(lows) >= 8:
            ranges = (highs.tail(8) - lows.tail(8)).dropna()
            if len(ranges) == 8:
                prior_7_ranges = ranges.iloc[:-1]
                nr7 = float(ranges.iloc[-1]) < float(prior_7_ranges.min())
                atr_contracted = float(prior_7_ranges.mean()) <= atr * 1.05
                if nr7 and atr_contracted and close <= float(lows.iloc[-2]):
                    nr7_breakdown = True

    if broke_20d_low:
        score += 20.0
        reasons.append("20d low breakdown (+20)")
    elif nr7_breakdown:
        score += 15.0
        reasons.append("NR7/ATR squeeze breakdown (+15)")

    # 4. close < EMA20 < EMA50 (+15)
    ema20 = row.get("ema20")
    ema50 = row.get("ema50")
    if ema20 is not None and ema50 is not None and close < float(ema20) < float(ema50):
        score += 15.0
        reasons.append("Close<EMA20<EMA50 (+15)")

    # 5. Weak relative strength vs Nifty over 5d (+15)
    stock_5d = float(row.get("ret_5d_pct") or 0.0)
    if (stock_5d - float(nifty_5d_pct or 0.0)) < 0.0:
        score += 15.0
        reasons.append("5d weak RS vs Nifty (+15)")

    # 6. Sector index down (+15)
    if float(sector_chg_pct or 0.0) < 0.0:
        score += 15.0
        reasons.append(f"Sector down {float(sector_chg_pct):+.2f}% (+15)")

    if score < 30.0:
        return None

    day_high = float(row.get("day_high") or close)
    target_1 = round(max(close - 0.5 * atr, 0.05), 2)
    target_2 = round(max(close - 0.8 * atr, 0.05), 2)
    sl = round(min(close + 1.0 * atr, day_high if day_high > close else close + 1.0 * atr), 2)

    return {
        "btst_score": round(min(100.0, score), 2),
        "btst_reasons": reasons,
        "entry": round(close, 2),
        "target_1": target_1,
        "target_2": target_2,
        "stop_loss": sl,
    }
