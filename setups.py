import numpy as np
import pandas as pd
from config import *

BUILDUP_OK = {"BULLISH": ("Long Buildup", "Short Covering"), "BEARISH": ("Short Buildup", "Long Unwinding")}

def prerally(d, t, is_index=False):
    """Coiling setups: compression + price parked next to a key level, before the break."""
    d = d.dropna()
    c, h, l, v = d["Close"], d["High"], d["Low"], d["Volume"]
    last = t["price"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    ratio = float(tr.rolling(5).mean().iloc[-1] / tr.rolling(20).mean().iloc[-1])
    nr7 = bool((h - l).iloc[-1] <= (h - l).iloc[-7:].min())
    ma, sd = c.rolling(20).mean(), c.rolling(20).std()
    bw = (4 * sd / ma).dropna()
    squeeze = len(bw) >= 100 and bool((bw.iloc[-120:] <= bw.iloc[-1]).mean() <= 0.2)
    v20 = v.iloc[-20:].mean()
    vdry = float(v.iloc[-5:].mean() / v20) if (v20 > 0 and not is_index) else 1.0
    pts, tags = 0, []
    if ratio < 0.75: pts += 2; tags.append(f"Range contraction: 5-day ATR is {ratio * 100:.0f}% of 20-day ATR")
    if nr7: pts += 1; tags.append("Narrowest daily range in 7 sessions (NR7)")
    if squeeze: pts += 1; tags.append("Bollinger squeeze: bandwidth in lowest 20% of last 6 months")
    if vdry < 0.85: pts += 1; tags.append(f"Volume drying up (5-day avg {vdry:.2f}x of 20-day avg)")
    res, sup = float(h.iloc[-20:].max()), float(l.iloc[-20:].min())
    d_res, d_sup = (res - last) / last * 100, (last - sup) / last * 100
    ups = int((c.diff().iloc[-5:] > 0).sum())
    side, sp, trig = None, 0, None
    if 0 < d_res <= 1.5 and last > t["ema20"]:
        side, trig, sp = "BULLISH", res, 2
        tags.append(f"Closing {d_res:.1f}% below 20-day high {res:.0f} (coiling under resistance)")
        if t["ema20"] > t["ema50"]: sp += 1; tags.append("Trend aligned: EMA20 above EMA50")
        if ups >= 3: sp += 1; tags.append("Quiet accumulation: 3+ up-closes in last 5 sessions")
    elif 0 < d_sup <= 1.5 and last < t["ema20"]:
        side, trig, sp = "BEARISH", sup, 2
        tags.append(f"Closing {d_sup:.1f}% above 20-day low {sup:.0f} (pressing on support)")
        if t["ema20"] < t["ema50"]: sp += 1; tags.append("Trend aligned: EMA20 below EMA50")
        if ups <= 2: sp += 1; tags.append("Steady distribution: 3+ down-closes in last 5 sessions")
    if not side or pts < 2 or pts + sp < 5: return None
    return dict(direction=side, pre_score=pts + sp, tags=tags, trigger=float(trig))

def levels(r):
    """ATR-based entry / stop / targets on the underlying. A plan, not a prediction."""
    px, atr = r["price"], r["atr"]
    s = 1 if r["direction"] == "BULLISH" else -1
    k_stop, k1, k2 = (0.8, 1.0, 1.6) if MODE == "BTST" else (1.0, 1.5, 2.5)
    entry = r["trigger"] * (1 + 0.002 * s) if r["setup"] == "PRE" else px
    stop = entry - s * k_stop * atr
    sw = r.get("lvl_sup") if s == 1 else r.get("lvl_res")
    if sw:
        cand = sw * (1 - 0.002 * s)
        if 0.5 * atr <= abs(entry - cand) <= 1.5 * atr and (entry - cand) * s > 0: stop = cand
    t1, t2 = entry + s * k1 * atr, entry + s * k2 * atr
    risk = abs(entry - stop)
    return dict(entry=entry, stop=stop, t1=t1, t2=t2, risk=risk, rr=abs(t1 - entry) / risk if risk else 0)

def flags_for(r):
    f = []
    if abs(r["chg"]) > 8 or (not r["is_index"] and r["vr"] > 6):
        f.append("Unusually large move/volume: verify on NSE first (corporate action or data error possible)")
    if r["oc"] and r["oc"].get("dte", 99) <= 2:
        f.append(f"Expiry in {r['oc']['dte']} day(s): premium decays very fast")
    b = r["oc"]["best"] if r["oc"] else None
    if b and not b.get("iv_ok", True): f.append("Strike IV looks unreliable; check the live option quote")
    return f

def btst_gate(r, nifty_chg, nifty_up):
    """Extra filters for overnight trades. Returns (ok, why_not)."""
    if MODE != "BTST": return True, ""
    bull = r["direction"] == "BULLISH"
    if abs(r["chg"]) > 4.5: return False, "already extended today"
    rs = r["chg"] - nifty_chg
    if bull:
        if r["close_pos"] < 0.7: return False, "not holding near day high"
        if not r["is_index"] and rs < 0.5: return False, "no relative strength vs Nifty"
        if r["rsi"] > 72: return False, "overbought"
        return True, ""
    if r["rsi"] < 35: return False, "oversold: bounce risk"
    if r["close_pos"] > 0.3: return False, "not holding near day low"
    if not r["is_index"] and rs > (-1.5 if nifty_up else -1.0): return False, "not weak enough vs Nifty"
    oc = r.get("oc")
    if not oc or oc["buildup"] != "Short Buildup": return False, "no fresh short buildup in options"
    return True, ""

def grade(r, nifty_chg, nifty_ret20, sec_avg):
    bull = r["direction"] == "BULLISH"
    s = 1 if bull else -1
    oc = r.get("oc")
    chk = {}
    chk["Trend"] = (r["price"] > r["ema20"] > r["ema50"]) if bull else (r["price"] < r["ema20"] < r["ema50"])
    chk["Key level"] = r["setup"] == "PRE" or r["level_ok"]
    if r["setup"] == "PRE": chk["Compression"] = r["pre_score"] >= 6
    elif r["is_index"]: chk["Momentum"] = (r["rsi"] >= 55) if bull else (r["rsi"] <= 45)
    else: chk["Volume"] = r["vr"] >= 1.3 and r["pressure"] in (("BUYING",) if bull else ("SELLING",))
    chk["Options"] = bool(oc) and oc["bias"] == r["direction"] and oc["buildup"] in BUILDUP_OK[r["direction"]]
    chk["Sector"] = True if (r["is_index"] or sec_avg is None) else sec_avg * s > 0.3
    if r["is_index"]: chk["Rel. strength"] = True
    elif r["setup"] == "PRE": chk["Rel. strength"] = (r["ret20"] - nifty_ret20) * s > 0
    else: chk["Rel. strength"] = (r["chg"] - nifty_chg) * s >= 0.5
    n = sum(chk.values())
    cap = 3 if r["flags"] else 6
    if (bull and r["rsi"] > 75) or (not bull and r["rsi"] < 30): cap = min(cap, 4)
    n = min(n, cap)
    g = "A" if n >= 5 else "B" if n == 4 else "C" if n == 3 else None
    return g, chk, n

def premium_plan(b, lv):
    """Approximate option SL/T1 from delta. Ignores theta, gamma and IV change."""
    if not b or not b["delta"]: return None
    d = abs(b["delta"])
    sl = max(b["ltp"] - d * lv["risk"], 0.05)
    t1 = b["ltp"] + d * abs(lv["t1"] - lv["entry"])
    risk = b["ltp"] - sl
    return dict(sl=sl, t1=t1, rr=(t1 - b["ltp"]) / risk if risk > 0 else 0)

def position_size(b, plan, lot):
    if not (b and plan and lot): return None
    per_lot = (b["ltp"] - plan["sl"]) * lot
    max_loss = CAPITAL * RISK_PCT / 100
    lots = int(max_loss // per_lot) if per_lot > 0 else 0
    return dict(lot=lot, lots=lots, per_lot=per_lot, outlay=lots * lot * b["ltp"], max_loss=max_loss, one_lot=lot * b["ltp"])
