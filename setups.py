import datetime as dt
from typing import Any
import numpy as np
import pandas as pd


def btst_rally_score(
    row: dict[str, Any],
    df: pd.DataFrame | None = None,
    nifty_5d_pct: float = 0.0,
    sector_chg_pct: float = 0.0,
    event_meta: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Computes 0-100 BTST gap-up/rally score and BTST targets/SL.
    Returns None if rejected by overbought/streak/event filters.
    """
    close = float(row.get("close") or row.get("entry") or 0.0)
    atr = float(row.get("atr") or 0.0)
    rsi = row.get("rsi")
    if close <= 0 or atr <= 0:
        return None
    if rsi is not None and not pd.isna(rsi) and float(rsi) > 80.0:
        return None

    reasons: list[str] = []
    score = 0.0

    # Reject >= 3 consecutive up days
    if df is not None and not df.empty and len(df) >= 4:
        closes = pd.to_numeric(df["Close"], errors="coerce").dropna().tail(4).tolist()
        if len(closes) == 4 and closes[1] > closes[0] and closes[2] > closes[1] and closes[3] > closes[2]:
            return None

    # Reject earnings or ex-date next day if available
    ev = event_meta or {}
    if bool(ev.get("earnings_tomorrow")) or bool(ev.get("ex_date_tomorrow")):
        return None
    next_ev_date = str(ev.get("next_event_date") or "").strip()[:10]
    if next_ev_date:
        try:
            ev_dt = dt.date.fromisoformat(next_ev_date)
            today_ist = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).date()
            if (ev_dt - today_ist).days in {0, 1}:
                return None
        except ValueError:
            pass

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
