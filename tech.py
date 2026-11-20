import numpy as np
import pandas as pd


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def compute_indicators(df: pd.DataFrame) -> dict | None:
    if df is None or df.empty or len(df) < 20:
        return None

    work = df.copy()
    work.columns = [str(c).strip().capitalize() for c in work.columns]
    if not all(c in work.columns for c in ["High", "Low", "Close"]):
        return None

    high = pd.to_numeric(work["High"], errors="coerce")
    low = pd.to_numeric(work["Low"], errors="coerce")
    close = pd.to_numeric(work["Close"], errors="coerce")
    volume = (
        pd.to_numeric(work["Volume"], errors="coerce").fillna(0.0)
        if "Volume" in work.columns
        else pd.Series(0.0, index=work.index)
    )

    valid = pd.DataFrame({"High": high, "Low": low, "Close": close, "Volume": volume}).dropna(
        subset=["High", "Low", "Close"]
    )
    if len(valid) < 20:
        return None

    c_last = float(valid["Close"].iloc[-1])
    c_prev = float(valid["Close"].iloc[-2]) if len(valid) >= 2 else c_last
    if c_last <= 0:
        return None

    chg_pct = ((c_last / c_prev) - 1.0) * 100.0 if c_prev > 0 else 0.0

    ema20_s = _ema(valid["Close"], 20)
    ema50_s = _ema(valid["Close"], min(50, len(valid)))
    ema200_s = _ema(valid["Close"], min(200, len(valid)))
    rsi_s = _rsi(valid["Close"], 14)
    atr_s = _atr(valid["High"], valid["Low"], valid["Close"], 14)

    ema20 = float(ema20_s.iloc[-1])
    ema50 = float(ema50_s.iloc[-1])
    ema200 = float(ema200_s.iloc[-1])
    rsi_val = float(rsi_s.iloc[-1])
    atr_val = float(atr_s.iloc[-1]) if pd.notna(atr_s.iloc[-1]) and float(atr_s.iloc[-1]) > 0 else max(c_last * 0.015, 0.05)

    vol_last = float(valid["Volume"].iloc[-1])
    vol_avg20 = float(valid["Volume"].tail(20).mean())
    vol_ratio = (vol_last / vol_avg20) if vol_avg20 > 0 else 1.0

    bull_score = 45.0
    bear_score = 45.0

    if c_last >= ema20:
        bull_score += 15.0
    else:
        bear_score += 15.0

    if c_last >= ema50:
        bull_score += 12.0
    else:
        bear_score += 12.0

    if len(valid) >= 200:
        if c_last >= ema200:
            bull_score += 8.0
        else:
            bear_score += 8.0

    if chg_pct > 0:
        bull_score += min(12.0, chg_pct * 3.0)
    elif chg_pct < 0:
        bear_score += min(12.0, abs(chg_pct) * 3.0)

    if 52.0 <= rsi_val <= 78.0:
        bull_score += 8.0
    elif 22.0 <= rsi_val <= 48.0:
        bear_score += 8.0

    if vol_ratio >= 1.2:
        bull_score += 5.0
        bear_score += 5.0

    bull_score = round(max(0.0, min(100.0, bull_score)), 2)
    bear_score = round(max(0.0, min(100.0, bear_score)), 2)

    history_label = "1y/200+ bars" if len(valid) >= 200 else f"{len(valid)} bars"

    return {
        "close": round(c_last, 2),
        "prev_close": round(c_prev, 2),
        "change_pct": round(chg_pct, 2),
        "ema20": round(ema20, 2),
        "ema50": round(ema50, 2),
        "ema200": round(ema200, 2),
        "rsi": round(rsi_val, 1),
        "atr": round(atr_val, 2),
        "vol_ratio": round(vol_ratio, 2),
        "bull_score": bull_score,
        "bear_score": bear_score,
        "history_label": history_label,
    }
