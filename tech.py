import numpy as np
import pandas as pd


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    res = 100 - (100 / (1 + rs))
    res = res.mask((loss == 0) & (gain > 0), 100.0)
    res = res.mask((loss == 0) & (gain == 0), 50.0)
    return res


def atr(df, period=14):
    high, low, close = df["High"], df["Low"], df["Close"]
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def adx(df, period=14):
    high, low, close = df["High"], df["Low"], df["Close"]
    up_move = high.diff()
    down_move = -low.diff()
    
    pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    
    tr = atr(df, period)
    pos_di = 100 * pd.Series(pos_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean() / tr.replace(0, np.nan)
    neg_di = 100 * pd.Series(neg_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean() / tr.replace(0, np.nan)
    
    dx = 100 * (pos_di - neg_di).abs() / (pos_di + neg_di).replace(0, np.nan)
    return dx.ewm(alpha=1/period, adjust=False).mean()


def pct(a, b):
    if b is None or pd.isna(b) or b == 0:
        return 0.0
    return ((a / b) - 1.0) * 100.0


def analyze(df):
    if df is None or len(df) < 15:
        return {}
    
    close = df["Close"]
    c_rsi = rsi(close)
    c_atr = atr(df)
    c_adx = adx(df)
    
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean() if len(df) >= 200 else ema50
    
    avg_vol = df["Volume"].tail(20).mean() if "Volume" in df.columns else 1.0
    last_vol = df["Volume"].iloc[-1] if "Volume" in df.columns else 1.0
    vr = (last_vol / avg_vol) if avg_vol > 0 else 1.0
    
    return {
        "price": float(close.iloc[-1]),
        "rsi": float(c_rsi.iloc[-1]) if not pd.isna(c_rsi.iloc[-1]) else 50.0,
        "atr": float(c_atr.iloc[-1]) if not pd.isna(c_atr.iloc[-1]) else float(close.iloc[-1] * 0.015),
        "adx": float(c_adx.iloc[-1]) if not pd.isna(c_adx.iloc[-1]) else 20.0,
        "vr": float(vr),
        "ema20": float(ema20.iloc[-1]),
        "ema50": float(ema50.iloc[-1]),
        "ema200": float(ema200.iloc[-1])
    }
