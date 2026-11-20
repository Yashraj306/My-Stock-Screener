import numpy as np
import pandas as pd


def clean_ohlcv(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None or getattr(df, "empty", True):
        return None
    work = df.copy()
    if isinstance(work.columns, pd.MultiIndex):
        work.columns = [c[0] for c in work.columns]
    work.columns = [str(c).strip().capitalize() for c in work.columns]

    if "Date" not in work.columns and isinstance(work.index, pd.DatetimeIndex):
        work = work.reset_index().rename(columns={work.columns[0]: "Date"})

    needed = ["High", "Low", "Close"]
    if not all(c in work.columns for c in needed):
        return None
    if "Open" not in work.columns:
        work["Open"] = work["Close"]
    if "Volume" not in work.columns:
        work["Volume"] = 0.0

    for col in ["Open", "High", "Low", "Close", "Volume"]:
        work[col] = pd.to_numeric(work[col], errors="coerce")

    work = work.dropna(subset=["High", "Low", "Close"])
    work = work[work["Close"] > 0]
    if "Date" in work.columns:
        parsed_dates = pd.to_datetime(work["Date"], errors="coerce")
        work = work.assign(_dt=parsed_dates).dropna(subset=["_dt"]).sort_values("_dt").drop_duplicates("_dt")
        work["Date"] = work["_dt"].dt.strftime("%Y-%m-%d")
        work = work.drop(columns=["_dt"])

    return work.reset_index(drop=True) if not work.empty else None


def pct(curr: float, prev: float) -> float:
    try:
        c = float(curr)
        p = float(prev)
        if p == 0.0:
            return 0.0
        return round(((c / p) - 1.0) * 100.0, 2)
    except (TypeError, ValueError):
        return 0.0


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=max(int(period), 1), adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    cleaned = clean_ohlcv(df)
    if cleaned is None or cleaned.empty:
        return pd.Series(dtype=float)
    high = cleaned["High"]
    low = cleaned["Low"]
    close = cleaned["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / max(int(period), 1), min_periods=min(int(period), len(tr)), adjust=False).mean()


def cumulative_vwap(df: pd.DataFrame) -> pd.Series:
    cleaned = clean_ohlcv(df)
    if cleaned is None or cleaned.empty:
        return pd.Series(dtype=float)
    typical = (cleaned["High"] + cleaned["Low"] + cleaned["Close"]) / 3.0
    vol = cleaned["Volume"].fillna(0.0).clip(lower=0.0)
    cum_pv = (typical * vol).cumsum()
    cum_vol = vol.cumsum()
    vwap_series = cum_pv / cum_vol.replace(0.0, np.nan)
    fallback_typical = typical.expanding().mean()
    return vwap_series.fillna(fallback_typical)


def compute_indicators(df: pd.DataFrame) -> dict | None:
    valid = clean_ohlcv(df)
    if valid is None or len(valid) < 20:
        return None

    c_last = float(valid["Close"].iloc[-1])
    c_prev = float(valid["Close"].iloc[-2]) if len(valid) >= 2 else c_last
    if c_last <= 0:
        return None

    chg_pct = pct(c_last, c_prev)

    ema20_s = _ema(valid["Close"], 20)
    ema50_s = _ema(valid["Close"], min(50, len(valid)))
    ema200_s = _ema(valid["Close"], min(200, len(valid)))
    rsi_s = _rsi(valid["Close"], 14)
    atr_s = atr(valid, 14)
    vwap_s = cumulative_vwap(valid)

    ema20 = float(ema20_s.iloc[-1])
    ema50 = float(ema50_s.iloc[-1])
    ema200 = float(ema200_s.iloc[-1])
    rsi_val = float(rsi_s.iloc[-1])
    atr_last = float(atr_s.iloc[-1]) if not atr_s.empty and pd.notna(atr_s.iloc[-1]) else 0.0
    atr_val = atr_last if atr_last > 0 else max(c_last * 0.015, 0.05)
    vwap_val = float(vwap_s.iloc[-1]) if not vwap_s.empty and pd.notna(vwap_s.iloc[-1]) else c_last

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
        "change_pct": chg_pct,
        "ema20": round(ema20, 2),
        "ema50": round(ema50, 2),
        "ema200": round(ema200, 2),
        "vwap": round(vwap_val, 2),
        "rsi": round(rsi_val, 1),
        "atr": round(atr_val, 2),
        "vol_ratio": round(vol_ratio, 2),
        "bull_score": bull_score,
        "bear_score": bear_score,
        "history_label": history_label,
    }


def analyze(df: pd.DataFrame) -> dict | None:
    return compute_indicators(df)
    
