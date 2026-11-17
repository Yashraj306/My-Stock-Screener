
import numpy as np
import pandas as pd


def pct(current, previous):
    """Calculate percentage change safely."""
    try:
        current = float(current)
        previous = float(previous)

        if not np.isfinite(current) or not np.isfinite(previous):
            return 0.0

        if previous == 0:
            return 0.0

        return ((current - previous) / abs(previous)) * 100.0

    except (TypeError, ValueError):
        return 0.0


def clean_ohlcv(df):
    """Validate and clean OHLCV candle data."""

    if df is None or not isinstance(df, pd.DataFrame):
        return pd.DataFrame()

    required = ["Open", "High", "Low", "Close"]

    if not all(col in df.columns for col in required):
        return pd.DataFrame()

    data = df.copy()

    for col in required:
        data[col] = pd.to_numeric(data[col], errors="coerce")

    if "Volume" not in data.columns:
        data["Volume"] = np.nan
    else:
        data["Volume"] = pd.to_numeric(
            data["Volume"], errors="coerce"
        )

    data = data.dropna(subset=required)

    data = data[
        (data["Open"] > 0)
        & (data["High"] > 0)
        & (data["Low"] > 0)
        & (data["Close"] > 0)
        & (data["High"] >= data["Low"])
    ]

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

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()

    true_range = pd.concat(
        [tr1, tr2, tr3], axis=1
    ).max(axis=1)

    return true_range.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()


def adx(df, period=14):
    """Calculate ADX, +DI and -DI."""

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
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs()
        ],
        axis=1
    ).max(axis=1)

    atr_value = tr.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    plus_smoothed = plus_dm.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    minus_smoothed = minus_dm.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    plus_di = 100 * plus_smoothed / atr_value.replace(0, np.nan)
    minus_di = 100 * minus_smoothed / atr_value.replace(0, np.nan)

    denominator = (plus_di + minus_di).replace(0, np.nan)

    dx = (
        100 * (plus_di - minus_di).abs() / denominator
    )

    adx_value = dx.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    return adx_value, plus_di, minus_di


def compute_vwap(df):
    """
    Calculate VWAP only when valid intraday timestamps exist.
    Daily candles are not treated as session VWAP.
    """

    if df is None or len(df) == 0:
        return None

    if "Volume" not in df.columns:
        return None

    if not isinstance(df.index, pd.DatetimeIndex):
        return None

    if len(df) < 2:
        return None

    # Daily candles usually have midnight timestamps.
    if not any(
        timestamp.hour != 0
        or timestamp.minute != 0
        for timestamp in df.index
    ):
        return None

    today = df.index[-1].date()

    session = df[df.index.date == today]

    volume = pd.to_numeric(
        session["Volume"], errors="coerce"
    ).fillna(0)

    if volume.sum() <= 0:
        return None

    typical_price = (
        session["High"] + session["Low"] + session["Close"]
    ) / 3.0

    value = (typical_price * volume).sum() / volume.sum()

    return float(value) if np.isfinite(value) else None


def analyze(df):
    """Calculate technical indicators for the screener."""

    data = clean_ohlcv(df)

    if len(data) < 200:
        return {}

    close = data["Close"]

    rsi_values = rsi(close, 14)
    atr_values = atr(data, 14)

    adx_values, plus_di_values, minus_di_values = adx(
        data, 14
    )

    ema20 = close.ewm(
        span=20, adjust=False
    ).mean()

    ema50 = close.ewm(
        span=50, adjust=False
    ).mean()

    ema200 = close.ewm(
        span=200, adjust=False
    ).mean()

    price = float(close.iloc[-1])
    atr_value = atr_values.iloc[-1]
    rsi_value = rsi_values.iloc[-1]
    adx_value = adx_values.iloc[-1]

    if (
        pd.isna(atr_value)
        or pd.isna(rsi_value)
        or pd.isna(adx_value)
    ):
        return {}

    volume_ratio = None

    if "Volume" in data.columns:
        volume = data["Volume"]

        previous_volume = volume.iloc[-21:-1]

        if len(previous_volume) == 20:
            average_volume = previous_volume.mean()

            if (
                pd.notna(average_volume)
                and average_volume > 0
            ):
                volume_ratio = float(
                    volume.iloc[-1] / average_volume
                )

    vwap_value = compute_vwap(data)

    return {
        "price": price,
        "rsi": float(rsi_value),
        "atr": float(atr_value),
        "adx": float(adx_value),
        "plus_di": float(plus_di_values.iloc[-1]),
        "minus_di": float(minus_di_values.iloc[-1]),
        "vr": volume_ratio,
        "ema20": float(ema20.iloc[-1]),
        "ema50": float(ema50.iloc[-1]),
        "ema200": float(ema200.iloc[-1]),
        "vwap": vwap_value
    }
