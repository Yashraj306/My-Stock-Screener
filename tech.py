import numpy as np
import pandas as pd


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    res = 100 - (100 / (1 + rs))
    res = res.mask((loss == 0) & (gain > 0), 100.0)
    res = res.mask((loss == 0) & (gain == 0), 50.0)
    return res


def atr(df, period=14):
    high, low, close = df["High"].astype(float), df["Low"].astype(float), df["Close"].astype(float)
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def compute_vwap(df):
    if "Volume" not in df.columns or df["Volume"].sum() == 0:
        return float(df["Close"].iloc[-1])
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    close = df["Close"].astype(float)
    vol = df["Volume"].astype(float)
    typical_price = (high + low + close) / 3.0
    vol_sum = vol.tail(5).sum()
    if vol_sum <= 0:
        return float(close.iloc[-1])
    return float((typical_price * vol).tail(5).sum() / vol_sum)


def analyze(df):
    if df is None or len(df) < 15:
        return {}

    close = df["Close"].astype(float)
    c_rsi = rsi(close)
    c_atr = atr(df)

    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean() if len(df) >= 200 else ema50

    vol_col = df["Volume"].astype(float) if "Volume" in df.columns else pd.Series([1.0] * len(df))
    avg_vol = vol_col.tail(20).mean()
    vr = (vol_col.iloc[-1] / avg_vol) if avg_vol > 0 else 1.0

    return {
        "price": float(close.iloc[-1]),
        "rsi": float(c_rsi.iloc[-1]) if not pd.isna(c_rsi.iloc[-1]) else 50.0,
        "atr": float(c_atr.iloc[-1]) if not pd.isna(c_atr.iloc[-1]) else float(close.iloc[-1] * 0.018),
        "vr": float(vr),
        "ema20": float(ema20.iloc[-1]),
        "ema50": float(ema50.iloc[-1]),
        "ema200": float(ema200.iloc[-1]),
        "vwap": compute_vwap(df)
    }
