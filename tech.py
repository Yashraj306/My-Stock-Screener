
import numpy as np
import pandas as pd


def pct(current, previous):
    """Percentage change from previous value to current value."""
    if previous is None or pd.isna(previous) or previous == 0:
        return 0.0
    return (float(current) / float(previous) - 1.0) * 100.0


def clean_ohlcv(df):
    """Validate and clean OHLCV candles."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame()

    required = ["Open", "High", "Low", "Close", "Volume"]
    if not all(col in df.columns for col in required):
        return pd.DataFrame()

    data = df.copy()

    for col in required:
        data[col] = pd.to_numeric(data[col], errors="coerce")

    data = data.dropna(subset=required)

    if data.empty:
        return data

    valid = (
        (data["Open"] > 0)
        & (data["High"] >= data["Low"])
        & (data["High"] >= data["Open"])
        & (data["High"] >= data["Close"])
        & (data["Low"] <= data["Open"])
        & (data["Low"] <= data["Close"])
        & (data["Volume"] >= 0)
    )

    data = data.loc[valid].copy()
    return data.reset_index(drop=True)


def rsi(series, period=14):
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))

    result = result.mask(
        (avg_loss == 0) & (avg_gain > 0), 100.0
    )
    result = result.mask(
        (avg_loss == 0) & (avg_gain == 0), 50.0
    )

    return result


def atr(df, period=14):
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    tr = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs()
        ],
        axis=1
    ).max(axis=1)

    return tr.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()


def adx(df, period=14):
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where(
            (up_move > down_move) & (up_move > 0),
            up_move,
            0.0
        ),
        index=df.index
    )

    minus_dm = pd.Series(
        np.where(
            (down_move > up_move) & (down_move > 0),
            down_move,
            0.0
        ),
        index=df.index
    )

    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ],
        axis=1
    ).max(axis=1)

    atr_value = tr.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    plus_di = (
        100 * plus_dm.ewm(
            alpha=1 / period,
            adjust=False,
            min_periods=period
        ).mean() / atr_value.replace(0, np.nan)
    )

    minus_di = (
        100 * minus_dm.ewm(
            alpha=1 / period,
            adjust=False,
            min_periods=period
        ).mean() / atr_value.replace(0, np.nan)
    )

    denominator = (plus_di + minus_di).replace(0, np.nan)

    dx = (
        100 * (plus_di - minus_di).abs()
        / denominator
    )

    adx_value = dx.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    return adx_value, plus_di, minus_di


def compute_vwap(df):
    """
    Session VWAP only.
    Daily candles are not treated as intraday VWAP.
    """
    if df is None or len(df) < 1:
        return None

    if "Datetime" in df.columns:
        timestamps = pd.to_datetime(
            df["Datetime"], errors="coerce"
        )
    elif "Date" in df.columns:
        timestamps = pd.to_datetime(
            df["Date"], errors="coerce"
        )
    else:
        timestamps = pd.to_datetime(
            df.index, errors="coerce"
        )

    if timestamps.isna().any():
        return None

    # Reject daily candles and midnight-only timestamps.
    if not (timestamps.dt.hour != 0).any():
        return None

    data = df.copy()
    data["_timestamp"] = timestamps
    data = data.sort_values("_timestamp")

    latest_date = data["_timestamp"].iloc[-1].date()
    session = data[
        data["_timestamp"].dt.date == latest_date
    ]

    volume = session["Volume"].sum()

    if volume <= 0:
        return None

    typical = (
        session["High"] +
        session["Low"] +
        session["Close"]
    ) / 3.0

    return float(
        (typical * session["Volume"]).sum() / volume
    )


def analyze(df):
    """Calculate technical indicators for the screener."""

    data = clean_ohlcv(df)

    if len(data) < 15:
        return {}

    close = data["Close"]

    rsi_values = rsi(close)
    atr_values = atr(data)
    adx_values, plus_di, minus_di = adx(data)

    ema20 = close.ewm(
        span=20, adjust=False, min_periods=20
    ).mean()

    ema50 = close.ewm(
        span=50, adjust=False, min_periods=50
    ).mean()

    ema200 = close.ewm(
        span=200, adjust=False, min_periods=200
    ).mean()

    # Compare latest volume with previous 20 completed candles.
    if len(data) >= 21:
        avg_volume = data["Volume"].iloc[-21:-1].mean()
    else:
        avg_volume = np.nan

    last_volume = float(data["Volume"].iloc[-1])

    volume_ratio = (
        last_volume / avg_volume
        if pd.notna(avg_volume) and avg_volume > 0
        else None
    )

    last = len(data) - 1

    def value(series):
        result = series.iloc[last]
        return float(result) if pd.notna(result) else None

    price = float(close.iloc[-1])

    return {
        "price": price,
        "rsi": value(rsi_values),
        "atr": value(atr_values),
        "adx": value(adx_values),
        "plus_di": value(plus_di),
        "minus_di": value(minus_di),
        "vr": volume_ratio,
        "ema20": value(ema20),
        "ema50": value(ema50),
        "ema200": value(ema200),
        "vwap": compute_vwap(data)
    }
