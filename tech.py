import numpy as np
import pandas as pd


def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    out.columns = [str(c).strip().capitalize() for c in out.columns]
    if not all(col in out.columns for col in ["Open", "High", "Low", "Close"]):
        return pd.DataFrame()
    if "Volume" not in out.columns:
        out["Volume"] = 0.0
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["Open", "High", "Low", "Close"])
    out["Volume"] = out["Volume"].fillna(0.0)
    if "Date" in out.columns:
        out = out.sort_values("Date").reset_index(drop=True)
    else:
        out = out.reset_index(drop=True)
    return out


def pct(curr: float, prev: float) -> float:
    if prev is None or prev == 0 or pd.isna(curr) or pd.isna(prev):
        return 0.0
    return float(((float(curr) - float(prev)) / float(prev)) * 100.0)


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce").astype(float)
    delta = s.diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1.0 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1.0 / period, adjust=False).mean()
    rs = gain / loss.replace(0.0, np.nan)
    res = 100.0 - (100.0 / (1.0 + rs))
    res = res.mask((loss == 0.0) & (gain > 0.0), 100.0)
    res = res.mask((loss == 0.0) & (gain == 0.0), 50.0)
    return res


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    c = clean_ohlcv(df)
    if c.empty:
        return pd.Series(dtype=float)
    high, low, close = c["High"], c["Low"], c["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    c = clean_ohlcv(df)
    if c.empty or len(c) < period + 2:
        return pd.Series(dtype=float)
    high, low, close = c["High"], c["Low"], c["Close"]
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    atr_s = tr.ewm(alpha=1.0 / period, adjust=False).mean().replace(0.0, np.nan)
    plus_di = 100.0 * pd.Series(plus_dm, index=c.index).ewm(alpha=1.0 / period, adjust=False).mean() / atr_s
    minus_di = 100.0 * pd.Series(minus_dm, index=c.index).ewm(alpha=1.0 / period, adjust=False).mean() / atr_s
    dx = (100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)).fillna(0.0)
    return dx.ewm(alpha=1.0 / period, adjust=False).mean()


def compute_vwap(df: pd.DataFrame) -> float:
    c = clean_ohlcv(df)
    if c.empty:
        return 0.0
    typical_price = (c["High"] + c["Low"] + c["Close"]) / 3.0
    cum_vol = c["Volume"].cumsum()
    cum_pv = (typical_price * c["Volume"]).cumsum()
    vwap_series = (cum_pv / cum_vol.replace(0.0, np.nan)).ffill()
    val = vwap_series.iloc[-1]
    if pd.isna(val) or val <= 0:
        return float(c["Close"].iloc[-1])
    return float(val)


def analyze(df: pd.DataFrame) -> dict:
    c = clean_ohlcv(df)
    if c.empty or len(c) < 15:
        return {}

    close = c["Close"]
    c_rsi = rsi(close)
    c_atr = atr(c)
    c_adx = adx(c)

    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean() if len(c) >= 200 else ema50

    avg_vol_20 = c["Volume"].tail(20).mean()
    last_vol = float(c["Volume"].iloc[-1])
    vr = float(last_vol / avg_vol_20) if avg_vol_20 and avg_vol_20 > 0 else 0.0

    price = float(close.iloc[-1])
    atr_val = float(c_atr.iloc[-1]) if not pd.isna(c_atr.iloc[-1]) else price * 0.015

    return {
        "price": price,
        "prev_close": float(close.iloc[-2]) if len(close) >= 2 else price,
        "rsi": float(c_rsi.iloc[-1]) if not pd.isna(c_rsi.iloc[-1]) else 50.0,
        "atr": atr_val,
        "adx": float(c_adx.iloc[-1]) if not c_adx.empty and not pd.isna(c_adx.iloc[-1]) else 20.0,
        "vr": vr,
        "ema20": float(ema20.iloc[-1]),
        "ema50": float(ema50.iloc[-1]),
        "ema200": float(ema200.iloc[-1]),
        "has_ema200_history": len(c) >= 200,
        "vwap": compute_vwap(c),
        "bars": len(c),
    }
