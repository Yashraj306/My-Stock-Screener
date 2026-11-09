
import numpy as np
import pandas as pd

from config import *


def rsi(s, n=14):
    d = s.diff()

    g = d.clip(lower=0).ewm(
        alpha=1 / n, adjust=False
    ).mean()

    l = (-d.clip(upper=0)).ewm(
        alpha=1 / n, adjust=False
    ).mean()

    rs = g / l.replace(0, np.nan)

    out = 100 - 100 / (1 + rs)

    out = out.mask((l == 0) & (g > 0), 100.0)
    out = out.mask((l == 0) & (g == 0), 50.0)

    return out


def pct(a, b):
    if b is None or pd.isna(b) or b == 0:
        return 0.0

    return (a / b - 1) * 100


def atr(d, period=14):
    h = d["High"]
    l = d["Low"]
    c = d["Close"]

    tr = pd.concat([
        h - l,
        (h - c.shift()).abs(),
        (l - c.shift()).abs()
    ], axis=1).max(axis=1)

    return tr.rolling(period).mean()


def adx(d, period=14):
    h = d["High"]
    l = d["Low"]

    tr = pd.concat([
        h - l,
        (h - d["Close"].shift()).abs(),
        (l - d["Close"].shift()).abs()
    ], axis=1).max(axis=1)

    up = h.diff()
    down = -l.diff()

    plus_dm = up.where(
        (up > down) & (up > 0), 0.0
    )

    minus_dm = down.where(
        (down > up) & (down > 0), 0.0
    )

    atr_s = tr.ewm(
        alpha=1 / period, adjust=False
    ).mean()

    plus_di = 100 * plus_dm.ewm(
        alpha=1 / period, adjust=False
    ).mean() / atr_s.replace(0, np.nan)

    minus_di = 100 * minus_dm.ewm(
        alpha=1 / period, adjust=False
    ).mean() / atr_s.replace(0, np.nan)

    dx = (
        100 * (plus_di - minus_di).abs()
        / (plus_di + minus_di).replace(0, np.nan)
    )

    return dx.ewm(
        alpha=1 / period, adjust=False
    ).mean()


def swings(h, l, w=2, look=90):
    H = h.iloc[-look:].values
    L = l.iloc[-look:].values

    sh, sl = [], []

    for i in range(w, len(H) - w):

        if H[i] > max(H[i-w:i]) and H[i] >= max(H[i+1:i+w+1]):
            sh.append((i, float(H[i])))

        if L[i] < min(L[i-w:i]) and L[i] <= min(L[i+1:i+w+1]):
            sl.append((i, float(L[i])))

    return sh, sl


def structure(d):
    sh, sl = swings(d["High"], d["Low"])

    last = float(d["Close"].iloc[-1])

    tags = []
    bull = 0
    bear = 0

    res = sh[-1][1] if sh else None
    sup = sl[-1][1] if sl else None

    if len(sh) >= 2 and len(sl) >= 2:

        up = (
            sh[-1][1] > sh[-2][1]
            and sl[-1][1] > sl[-2][1]
        )

        dn = (
            sh[-1][1] < sh[-2][1]
            and sl[-1][1] < sl[-2][1]
        )

        if last > sh[-1][1]:

            if dn:
                bull += 3
                tags.append("Bullish Change of Structure")

            else:
                bull += 2
                tags.append("Bullish Break of Structure")

        elif last < sl[-1][1]:

            if up:
                bear += 3
                tags.append("Bearish Change of Structure")

            else:
                bear += 2
                tags.append("Bearish Break of Structure")

    return {
        "tags": tags,
        "bull": bull,
        "bear": bear,
        "res": res,
        "sup": sup
    }


def analyze(d, is_index=False):

    required = {
        "Open", "High", "Low", "Close", "Volume"
    }

    if d is None or not required.issubset(d.columns):
        return None

    d = d.copy().sort_index()

    d = d[~d.index.duplicated(keep="last")]
    d = d.dropna(subset=list(required))

    if len(d) < 210:
        return None

    c = d["Close"]
    o = d["Open"]
    h = d["High"]
    l = d["Low"]
    v = d["Volume"]

    last = float(c.iloc[-1])

    ema20 = float(c.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(c.ewm(span=50, adjust=False).mean().iloc[-1])
    ema200 = float(c.ewm(span=200, adjust=False).mean().iloc[-1])

    r = float(rsi(c).iloc[-1])
    a = float(atr(d).iloc[-1])
    adx_value = float(adx(d).iloc[-1])

    if not np.isfinite([r, a, adx_value]).all() or last <= 0:
        return None

    chg = pct(last, float(c.iloc[-2]))

    avgv = v.iloc[-21:-1].mean()

    vr = (
        1.0 if is_index or avgv <= 0
        else float(v.iloc[-1] / avgv) / vol_factor()
    )

    hi20 = float(h.iloc[-21:-1].max())
    lo20 = float(l.iloc[-21:-1].min())

    hi52 = float(h.iloc[-252:].max())
    lo52 = float(l.iloc[-252:].min())

    rng = float(h.iloc[-1] - l.iloc[-1])

    close_pos = (
        float((last - l.iloc[-1]) / rng)
        if rng > 0 else 0.5
    )

    ret20 = pct(last, float(c.iloc[-21]))

    st = structure(d)

    bull = st["bull"]
    bear = st["bear"]

    why = list(st["tags"])

    level_ok = bool(st["tags"])

    if last > ema20 > ema50:
        bull += 2
        why.append("Bullish EMA trend")

    elif last < ema20 < ema50:
        bear += 2
        why.append("Bearish EMA trend")

    if 55 <= r <= 72:
        bull += 1
        why.append(f"RSI {r:.1f}: bullish momentum")

    elif 32 <= r <= 45:
        bear += 1
        why.append(f"RSI {r:.1f}: bearish momentum")

    if not is_index and vr >= 1.5:

        if last > o.iloc[-1]:
            bull += 2
            why.append("High volume buying")

        else:
            bear += 2
            why.append("High volume selling")

    if last > hi20:
        bull += 2
        level_ok = True
        why.append("20-day breakout")

    elif last < lo20:
        bear += 2
        level_ok = True
        why.append("20-day breakdown")

    if last >= hi52 * 0.98:
        bull += 1
        why.append("Near 52-week high")

    if last <= lo52 * 1.02:
        bear += 1
        why.append("Near 52-week low")

    if close_pos >= 0.8 and vr >= 1.2:
        bull += 1
        why.append("Strong closing candle")

    if close_pos <= 0.2 and vr >= 1.2:
        bear += 1
        why.append("Weak closing candle")

    if chg >= 1.5:
        bull += 1

    elif chg <= -1.5:
        bear += 1

    if close_pos >= 0.7 and vr >= 1.0:
        pressure = "BUYING"

    elif close_pos <= 0.3 and vr >= 1.0:
        pressure = "SELLING"

    else:
        pressure = "BALANCED"

    return {
        "price": last,
        "chg": chg,
        "rsi": r,
        "vr": vr,
        "atr": a,
        "atr_pct": a / last * 100,
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "adx": adx_value,
        "hi52": hi52,
        "lo52": lo52,
        "pressure": pressure,
        "diff": bull - bear,
        "why": why,
        "level_ok": level_ok,
        "lvl_res": st["res"],
        "lvl_sup": st["sup"],
        "close_pos": close_pos,
        "ret20": ret20,
        "asof": str(d.index[-1].date())
    }


def direction_of(t):

    if t["diff"] >= 3:
        return "BULLISH"

    if t["diff"] <= -3:
        return "BEARISH"

    return None
