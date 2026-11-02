import time, json, gzip
import datetime as dt
import numpy as np
import pandas as pd
import requests
from config import *

# ---------- UPSTOX ----------
_instr = None
def load_instruments():
    global _instr
    if _instr is not None: return _instr
    try:
        r = requests.get("https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz", timeout=60)
        data = json.loads(gzip.decompress(r.content))
        m = {}
        for x in data:
            if x.get("segment") == "NSE_EQ" and x.get("instrument_type") == "EQ":
                m[x.get("trading_symbol")] = x.get("instrument_key")
        _instr = m
    except Exception as e:
        print("instrument load failed:", e); _instr = {}
    return _instr

def underlying_key(name):
    if name == "NIFTY": return "NSE_INDEX|Nifty 50"
    if name == "BANKNIFTY": return "NSE_INDEX|Nifty Bank"
    return load_instruments().get(name)

def upstox_get(path, params):
    h = {"Accept": "application/json", "Authorization": f"Bearer {UPSTOX_TOKEN}"}
    for _ in range(2):
        try:
            r = requests.get("https://api.upstox.com" + path, params=params, headers=h, timeout=20)
            if r.status_code == 200: return r.json()
            print("upstox", path, r.status_code, r.text[:150])
        except Exception as e:
            print("upstox err", e)
        time.sleep(1)
    return None

def nearest_expiry(key):
    j = upstox_get("/v2/option/contract", {"instrument_key": key})
    if not j or not j.get("data"): return None
    today = (dt.date.today() + dt.timedelta(days=3 if MODE == "BTST" else 0)).isoformat()
    ex = sorted({x["expiry"][:10] for x in j["data"] if x.get("expiry")})
    ex = [e for e in ex if e >= today]
    return ex[0] if ex else None

def chain_upstox(name):
    if not UPSTOX_TOKEN: return None
    key = underlying_key(name)
    if not key: return None
    exp = nearest_expiry(key)
    if not exp: return None
    j = upstox_get("/v2/option/chain", {"instrument_key": key, "expiry_date": exp})
    if not j or not j.get("data"): return None
    rows = []
    for x in j["data"]:
        ce, pe = x.get("call_options", {}), x.get("put_options", {})
        cm, pm = ce.get("market_data", {}), pe.get("market_data", {})
        cg, pg = ce.get("option_greeks", {}), pe.get("option_greeks", {})
        rows.append(dict(strike=x["strike_price"], spot=x.get("underlying_spot_price"),
            ce_ltp=cm.get("ltp",0), pe_ltp=pm.get("ltp",0),
            ce_oi=cm.get("oi",0), pe_oi=pm.get("oi",0),
            ce_poi=cm.get("prev_oi",0), pe_poi=pm.get("prev_oi",0),
            ce_vol=cm.get("volume",0), pe_vol=pm.get("volume",0),
            ce_iv=cg.get("iv",0), pe_iv=pg.get("iv",0),
            ce_delta=cg.get("delta",0), pe_delta=pg.get("delta",0),
            ce_theta=cg.get("theta",0), pe_theta=pg.get("theta",0),
            ce_gamma=cg.get("gamma",0), pe_gamma=pg.get("gamma",0),
            ce_vega=cg.get("vega",0), pe_vega=pg.get("vega",0)))
    df = pd.DataFrame(rows)
    return df, exp, "Upstox"

# ---------- NSE FALLBACK (no greeks / ltp-level detail) ----------
def chain_nse(name, sess):
    try:
        kind = "indices" if name in INDICES else "equities"
        r = sess.get(f"https://www.nseindia.com/api/option-chain-{kind}", params={"symbol": name}, timeout=15)
        if r.status_code != 200: return None
        rec = r.json()["records"]
        exp = rec["expiryDates"][0]
        spot = rec["underlyingValue"]
        rows = []
        for x in rec["data"]:
            if x.get("expiryDate") != exp: continue
            ce, pe = x.get("CE", {}), x.get("PE", {})
            rows.append(dict(strike=x["strikePrice"], spot=spot,
                ce_ltp=ce.get("lastPrice",0), pe_ltp=pe.get("lastPrice",0),
                ce_oi=ce.get("openInterest",0), pe_oi=pe.get("openInterest",0),
                ce_poi=ce.get("openInterest",0)-ce.get("changeinOpenInterest",0),
                pe_poi=pe.get("openInterest",0)-pe.get("changeinOpenInterest",0),
                ce_vol=ce.get("totalTradedVolume",0), pe_vol=pe.get("totalTradedVolume",0),
                ce_iv=ce.get("impliedVolatility",0), pe_iv=pe.get("impliedVolatility",0),
                ce_delta=0, pe_delta=0, ce_theta=0, pe_theta=0, ce_gamma=0, pe_gamma=0, ce_vega=0, pe_vega=0))
        return pd.DataFrame(rows), exp, "NSE"
    except Exception:
        return None

# ---------- CHAIN ANALYSIS ----------
def buildup(price_chg, oi_chg_pct):
    if abs(oi_chg_pct) < 1: return "Neutral"
    if price_chg >= 0 and oi_chg_pct > 0: return "Long Buildup"
    if price_chg < 0 and oi_chg_pct > 0: return "Short Buildup"
    if price_chg >= 0 and oi_chg_pct < 0: return "Short Covering"
    return "Long Unwinding"

def analyze_chain(df, exp, src, price_chg, direction):
    df = df.copy()
    spot = float(df["spot"].dropna().iloc[0]) if df["spot"].notna().any() else None
    if spot is None or df["ce_oi"].sum() == 0: return None
    pcr = df["pe_oi"].sum() / max(df["ce_oi"].sum(), 1)
    res = df.loc[df["ce_oi"].idxmax(), "strike"]
    sup = df.loc[df["pe_oi"].idxmax(), "strike"]
    ce_chg = (df["ce_oi"] - df["ce_poi"]).sum()
    pe_chg = (df["pe_oi"] - df["pe_poi"]).sum()
    tot_oi = df["ce_oi"].sum() + df["pe_oi"].sum()
    tot_poi = df["ce_poi"].sum() + df["pe_poi"].sum()
    oi_chg_pct = (tot_oi / tot_poi - 1) * 100 if tot_poi else 0
    bu = buildup(price_chg, oi_chg_pct)
    k = df["strike"].values
    pain = [(df["ce_oi"].values * np.maximum(kk - k, 0)).sum() + (df["pe_oi"].values * np.maximum(k - kk, 0)).sum() for kk in k]
    max_pain = k[int(np.argmin(pain))]
    atm_i = int(np.argmin(np.abs(k - spot)))
    atm = k[atm_i]
    iv = np.mean([x for x in (df.iloc[atm_i]["ce_iv"], df.iloc[atm_i]["pe_iv"]) if x > 0] or [0])
    if pcr >= 1.1 and pe_chg > ce_chg: bias = "BULLISH"
    elif pcr <= 0.9 and ce_chg > pe_chg: bias = "BEARISH"
    else: bias = "NEUTRAL"

    # ---- best strike: score ATM-1 .. ATM+2 on the side of the trade ----
    side = "CE" if direction == "BULLISH" else "PE"
    best = None
    cands = df.iloc[max(0, atm_i - 1): atm_i + 3].copy()
    scored = []
    for _, row in cands.iterrows():
        p = "ce" if side == "CE" else "pe"
        ltp, oi, poi, vol = row[f"{p}_ltp"], row[f"{p}_oi"], row[f"{p}_poi"], row[f"{p}_vol"]
        if ltp <= 0 or oi <= 0: continue
        oi_up = (oi / poi - 1) * 100 if poi else 0
        delta = abs(row[f"{p}_delta"])
        theta = abs(row[f"{p}_theta"])
        liq = np.log1p(vol) + np.log1p(oi)
        decay = theta / ltp * 100 if ltp else 0          # % of premium lost per day
        sc = liq * 1.0 + (3 if oi_up > 5 else 0)
        if delta: sc += 4 - abs(delta - 0.5) * 8          # prefer delta near 0.5
        sc -= decay * 0.5
        scored.append((sc, row, oi_up, delta, theta, decay))
    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        sc, row, oi_up, delta, theta, decay = scored[0]
        p = "ce" if side == "CE" else "pe"
        best = dict(strike=row["strike"], side=side, ltp=row[f"{p}_ltp"], oi=row[f"{p}_oi"],
                    oi_up=oi_up, vol=row[f"{p}_vol"], iv=row[f"{p}_iv"], delta=row[f"{p}_delta"],
                    theta=row[f"{p}_theta"], gamma=row[f"{p}_gamma"], vega=row[f"{p}_vega"], decay=decay)
    return dict(src=src, expiry=exp, spot=spot, pcr=pcr, support=sup, resistance=res, max_pain=max_pain,
                atm=atm, iv=iv, bias=bias, buildup=bu, oi_chg_pct=oi_chg_pct, best=best)

# ---------- SIGNAL ----------
def make_signal(r, gtone):
    d = r["direction"]
    score = abs(r["diff"]) * 10
    oc = r.get("oc")
    reasons = list(r["why"])
    if oc:
        if oc["bias"] == d: score += 25; reasons.append(f"Option chain agrees: PCR {oc['pcr']:.2f}, {oc['buildup']}")
        elif oc["bias"] == "NEUTRAL": reasons.append(f"Option chain neutral: PCR {oc['pcr']:.2f}, {oc['buildup']}")
        else: score -= 20; reasons.append(f"Option chain disagrees: PCR {oc['pcr']:.2f}")
        if d == "BULLISH" and oc["buildup"] in ("Long Buildup", "Short Covering"): score += 10
        if d == "BEARISH" and oc["buildup"] in ("Short Buildup", "Long Unwinding"): score += 10
        px = r["price"]
        if d == "BULLISH" and oc["resistance"] and oc["resistance"] <= px * 1.01:
            score -= 10; reasons.append(f"Heavy call OI wall at {oc['resistance']:.0f} just above price")
        if d == "BEARISH" and oc["support"] and oc["support"] >= px * 0.99:
            score -= 10; reasons.append(f"Heavy put OI floor at {oc['support']:.0f} just below price")
    if gtone == "POSITIVE" and d == "BULLISH": score += 8
    if gtone == "NEGATIVE" and d == "BEARISH": score += 8
    if gtone == "POSITIVE" and d == "BEARISH": score -= 8
    if gtone == "NEGATIVE" and d == "BULLISH": score -= 8
    action = "BUY CALL (CE)" if d == "BULLISH" else "BUY PUT (PE)"
    conf = "HIGH" if score >= 90 else "MEDIUM" if score >= 60 else "LOW"
    r["score"], r["reasons"], r["action"], r["conf"] = score, reasons, action, conf

