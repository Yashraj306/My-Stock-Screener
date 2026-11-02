import numpy as np
import pandas as pd
import yfinance as yf
from config import *

# ---------- helpers ----------
def rsi(s, n=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))

def pct(a, b):
    return (a / b - 1) * 100 if b else 0.0

# ---------- GLOBAL CUES ----------
def global_cues():
    out, score = [], 0.0
    for name, (tk, w) in GLOBAL.items():
        try:
            h = yf.Ticker(tk).history(period="5d")["Close"].dropna()
            if len(h) < 2:
                continue
            ch = pct(h.iloc[-1], h.iloc[-2])
            out.append((name, h.iloc[-1], ch))
            cap = max(-2.5, min(2.5, ch))
            score += cap * w if name != "India VIX" else cap * w * 0.3
        except Exception:
            continue
    if score >= 1.5:   tone, note = "POSITIVE", "Gap-up / firm open likely"
    elif score <= -1.5: tone, note = "NEGATIVE", "Gap-down / weak open likely"
    else:              tone, note = "MIXED", "Flat to mildly directional open"
    return out, score, tone, note

# ---------- MARKET STRUCTURE ----------
def swings(h, l, w=2, look=90):
    H, L = h.iloc[-look:].values, l.iloc[-look:].values
    sh, sl = [], []
    for i in range(w, len(H) - w):
        if H[i] > max(H[i - w:i]) and H[i] >= max(H[i + 1:i + w + 1]): sh.append((i, float(H[i])))
        if L[i] < min(L[i - w:i]) and L[i] <= min(L[i + 1:i + w + 1]): sl.append((i, float(L[i])))
    return sh, sl

def structure(d):
    sh, sl = swings(d["High"], d["Low"])
    last = float(d["Close"].iloc[-1])
    tags, bull, bear = [], 0, 0
    res = sh[-1][1] if sh else None
    sup = sl[-1][1] if sl else None
    if len(sh) >= 2 and len(sl) >= 2:
        up = sh[-1][1] > sh[-2][1] and sl[-1][1] > sl[-2][1]
        dn = sh[-1][1] < sh[-2][1] and sl[-1][1] < sl[-2][1]
        if last > sh[-1][1]:
            if dn:
                bull += 3; tags.append(f"Change of structure (bullish): downtrend broken, closed above swing high {sh[-1][1]:.0f}")
            elif up:
                bull += 2; tags.append(f"Break of structure (bullish): new higher high above {sh[-1][1]:.0f}")
            else:
                bull += 2; tags.append(f"Resistance break: closed above swing high {sh[-1][1]:.0f}")
        elif last < sl[-1][1]:
            if up:
                bear += 3; tags.append(f"Change of structure (bearish): uptrend broken, closed below swing low {sl[-1][1]:.0f}")
            elif dn:
                bear += 2; tags.append(f"Break of structure (bearish): new lower low below {sl[-1][1]:.0f}")
            else:
                bear += 2; tags.append(f"Support break: closed below swing low {sl[-1][1]:.0f}")
    return dict(tags=tags, bull=bull, bear=bear, res=res, sup=sup)

# ---------- TECHNICALS ----------
def analyze(d):
    d = d.dropna()
    if len(d) < 60:
        return None
    c, o, h, l, v = d["Close"], d["Open"], d["High"], d["Low"], d["Volume"]
    last = c.iloc[-1]
    ema20 = c.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = c.ewm(span=50, adjust=False).mean().iloc[-1]
    r = rsi(c).iloc[-1]
    chg = pct(last, c.iloc[-2])
    avgv = v.iloc[-21:-1].mean()
    vr = v.iloc[-1] / avgv if avgv > 0 else 1.0
    hi20, lo20 = h.iloc[-21:-1].max(), l.iloc[-21:-1].min()
    hi52, lo52 = h.iloc[-252:].max(), l.iloc[-252:].min()
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    rng = h.iloc[-1] - l.iloc[-1]
    close_pos = (last - l.iloc[-1]) / rng if rng > 0 else 0.5   # 1 = closed at high

    bull = bear = 0
    why = []
    st = structure(d)
    bull += st["bull"]; bear += st["bear"]; why += st["tags"]
    if last > ema20 > ema50: bull += 2; why.append("Trend up: price above EMA20 and EMA50")
    elif last < ema20 < ema50: bear += 2; why.append("Trend down: price below EMA20 and EMA50")
    if 55 <= r <= 72: bull += 1; why.append(f"RSI {r:.0f} (strong momentum)")
    elif 28 <= r <= 45: bear += 1; why.append(f"RSI {r:.0f} (weak momentum)")
    if vr >= 1.5:
        if last > o.iloc[-1]: bull += 2; why.append(f"Volume {vr:.1f}x average on green candle (buying)")
        else: bear += 2; why.append(f"Volume {vr:.1f}x average on red candle (selling)")
    if last > hi20: bull += 2; why.append("Resistance breakout: closed above 20-day high")
    elif last < lo20: bear += 2; why.append("Support breakdown: closed below 20-day low")
    if last >= hi52 * 0.98:
        bull += 1; why.append("At/near 52-week high" + (" (fresh high)" if last >= hi52 else ""))
    if last <= lo52 * 1.02:
        bear += 1; why.append("At/near 52-week low" + (" (fresh low)" if last <= lo52 else ""))
    if close_pos >= 0.8 and vr >= 1.2: bull += 1; why.append("Closed near day high with volume (buying pressure)")
    if close_pos <= 0.2 and vr >= 1.2: bear += 1; why.append("Closed near day low with volume (selling pressure)")
    if chg >= 1.5: bull += 1
    elif chg <= -1.5: bear += 1

    if close_pos >= 0.7 and vr >= 1.0: pressure = "BUYING"
    elif close_pos <= 0.3 and vr >= 1.0: pressure = "SELLING"
    else: pressure = "BALANCED"

    return dict(price=last, chg=chg, rsi=r, vr=vr, atr=atr, atr_pct=atr/last*100,
                hi52=hi52, lo52=lo52, pressure=pressure, bull=bull, bear=bear,
                diff=bull-bear, why=why, lvl_res=st["res"], lvl_sup=st["sup"],
                asof=str(d.index[-1].date()))

def direction_of(t):
    if t["diff"] >= 3: return "BULLISH"
    if t["diff"] <= -3: return "BEARISH"
    return None

def direction_weak(t):
    if t["diff"] >= 1: return "BULLISH"
    if t["diff"] <= -1: return "BEARISH"
    return None

# ---------- SECTORS + INDEX CONTRIBUTION ----------
def sector_table(tech):
    rows = []
    for sec, syms in SECTORS.items():
        ch = [tech[s]["chg"] for s in syms if s in tech]
        if not ch: continue
        up = sum(1 for x in ch if x > 0)
        rows.append((sec, float(np.mean(ch)), up, len(ch)))
    rows.sort(key=lambda x: x[1], reverse=True)
    return rows

def contribution(tech, weights, index_level):
    items = []
    for s, w in weights.items():
        if s in tech:
            pts = tech[s]["chg"] / 100 * w / 100 * index_level
            items.append((s, tech[s]["chg"], w, pts))
    items.sort(key=lambda x: x[3], reverse=True)
    return items

