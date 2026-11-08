import numpy as np
import yfinance as yf
from config import *
from tech import pct

def global_cues():
    out, score = [], 0.0
    for name, (tk, w) in GLOBAL.items():
        try:
            h = yf.Ticker(tk).history(period="5d")["Close"].dropna()
            if len(h) < 2: continue
            ch = pct(h.iloc[-1], h.iloc[-2])
            out.append((name, h.iloc[-1], ch))
            cap = max(-2.5, min(2.5, ch))
            score += cap * w * (0.3 if name == "India VIX" else 1)
        except Exception:
            continue
    if score >= 1.5: tone, note = "POSITIVE", "Gap-up / firm open likely"
    elif score <= -1.5: tone, note = "NEGATIVE", "Gap-down / weak open likely"
    else: tone, note = "MIXED", "Flat to mildly directional open"
    return out, score, tone, note

def sector_table(tech):
    groups = {}
    for s, t in tech.items():
        if s in INDICES: continue
        groups.setdefault(SECTOR_OF.get(s, "OTHER"), []).append(t["chg"])
    rows = [(k, float(np.mean(v)), sum(1 for x in v if x > 0), len(v)) for k, v in groups.items() if v]
    rows.sort(key=lambda x: x[1], reverse=True)
    return rows

def contribution(tech, weights, index_level):
    items = [(s, tech[s]["chg"], w, tech[s]["chg"] / 100 * w / 100 * index_level) for s, w in weights.items() if s in tech]
    items.sort(key=lambda x: x[3], reverse=True)
    return items

_hol = None

def holidays():
    """NSE full-closure dates for the year, from Upstox (no login needed). Empty set if unavailable."""
    global _hol
    if _hol is None:
        _hol = set()
        try:
            r = requests.get("https://api.upstox.com/v2/market/holidays", headers={"Accept": "application/json"}, timeout=20)
            for x in r.json().get("data", []):
                closed = x.get("closed_exchanges") or []
                if "NSE" in closed or "NFO" in closed: _hol.add(str(x["date"])[:10])
        except Exception as e:
            print("holiday list unavailable:", e)
    return _hol

def is_trading_day(d):
    return d.weekday() < 5 and d.isoformat() not in holidays()

def expected_session(ist):
    d = ist.date()
    if is_trading_day(d) and ist.hour * 60 + ist.minute >= 15 * 60 + 45: return d
    d -= dt.timedelta(days=1)
    while not is_trading_day(d): d -= dt.timedelta(days=1)
    return d

def breadth(tech):
    xs = [t for s, t in tech.items() if s not in INDICES]
    n = max(len(xs), 1)
    adv, dec = sum(t["chg"] > 0 for t in xs), sum(t["chg"] < 0 for t in xs)
    a20 = 100 * sum(t["price"] > t["ema20"] for t in xs) / n
    a50 = 100 * sum(t["price"] > t["ema50"] for t in xs) / n
    ratio = adv / max(dec, 1)
    tone = "BULLISH" if a20 >= 60 and ratio >= 1.3 else "BEARISH" if a20 <= 40 and ratio <= 0.77 else "MIXED"
    return dict(n=len(xs), adv=adv, dec=dec, a20=a20, a50=a50, tone=tone)

def freshness_note(asof):
    """Returns (text, level) with level in ok / warn / stale."""
    ist = ist_now()
    exp = expected_session(ist)
    got = dt.date.fromisoformat(asof)
    if MODE == "BTST":
        exp = ist.date() if is_trading_day(ist.date()) else exp
        if got == exp: return f"Live prices as of {asof} ~{ist:%H:%M} IST (about 15 min delay)", "ok"
        return f"Latest price data is {asof} but {exp} was expected (data lag)", "stale"
    if got < exp: return f"Latest price data is {asof} but {exp} was expected (data lag)", "stale"
    if got > exp: return f"Price data as of {asof}, includes a live unfinished candle: levels may change", "warn"
    return f"Price data as of {asof}", "ok"
