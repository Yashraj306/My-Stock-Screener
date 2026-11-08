import time
import datetime as dt
import pandas as pd
import requests
from config import *
from universe import eq_keys

def underlying_key(name):
    if name == "NIFTY": return "NSE_INDEX|Nifty 50"
    if name == "BANKNIFTY": return "NSE_INDEX|Nifty Bank"
    return eq_keys().get(name)

def upstox_get(path, params):
    h = {"Accept": "application/json", "Authorization": f"Bearer {UPSTOX_TOKEN}"}
    for _ in range(2):
        try:
            r = requests.get("https://api.upstox.com" + path, params=params, headers=h, timeout=20)
            if r.status_code == 200: return r.json()
            print("upstox", path, r.status_code, r.text[:120])
        except Exception as e:
            print("upstox err", e)
        time.sleep(1)
    return None

def nearest_expiry(key, is_index):
    j = upstox_get("/v2/option/contract", {"instrument_key": key})
    if not j or not j.get("data"): return None
    gap = 3 if MODE == "BTST" else (2 if is_index else 0)   # avoid expiries that decay too fast
    start = (ist_now().date() + dt.timedelta(days=gap)).isoformat()
    ex = sorted({x["expiry"][:10] for x in j["data"] if x.get("expiry")})
    ex = [e for e in ex if e >= start]
    return ex[0] if ex else None

def chain_upstox(name):
    if not UPSTOX_TOKEN: return None
    key = underlying_key(name)
    if not key: return None
    exp = nearest_expiry(key, name in INDICES)
    if not exp: return None
    j = upstox_get("/v2/option/chain", {"instrument_key": key, "expiry_date": exp})
    if not j or not j.get("data"): return None
    rows = []
    for x in j["data"]:
        ce, pe = x.get("call_options", {}), x.get("put_options", {})
        cm, pm = ce.get("market_data", {}), pe.get("market_data", {})
        cg, pg = ce.get("option_greeks", {}), pe.get("option_greeks", {})
        rows.append(dict(strike=x["strike_price"], spot=x.get("underlying_spot_price"),
            ce_ltp=cm.get("ltp", 0), pe_ltp=pm.get("ltp", 0), ce_oi=cm.get("oi", 0), pe_oi=pm.get("oi", 0),
            ce_poi=cm.get("prev_oi", 0), pe_poi=pm.get("prev_oi", 0), ce_vol=cm.get("volume", 0), pe_vol=pm.get("volume", 0),
            ce_iv=cg.get("iv", 0), pe_iv=pg.get("iv", 0), ce_delta=cg.get("delta", 0), pe_delta=pg.get("delta", 0),
            ce_theta=cg.get("theta", 0), pe_theta=pg.get("theta", 0), ce_gamma=cg.get("gamma", 0), pe_gamma=pg.get("gamma", 0),
            ce_vega=cg.get("vega", 0), pe_vega=pg.get("vega", 0)))
    return pd.DataFrame(rows), exp, "Upstox"

def nse_session():
    try:
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                          "Accept": "application/json", "Referer": "https://www.nseindia.com/option-chain"})
        s.get("https://www.nseindia.com", timeout=15)
        return s
    except Exception:
        return None

def chain_nse(name, sess):
    """Fallback only: no Greeks, and NSE often blocks cloud servers."""
    try:
        kind = "indices" if name in INDICES else "equities"
        r = sess.get(f"https://www.nseindia.com/api/option-chain-{kind}", params={"symbol": name}, timeout=15)
        if r.status_code != 200: return None
        rec = r.json()["records"]
        exp, spot = rec["expiryDates"][0], rec["underlyingValue"]
        rows = []
        for x in rec["data"]:
            if x.get("expiryDate") != exp: continue
            ce, pe = x.get("CE", {}), x.get("PE", {})
            rows.append(dict(strike=x["strikePrice"], spot=spot, ce_ltp=ce.get("lastPrice", 0), pe_ltp=pe.get("lastPrice", 0),
                ce_oi=ce.get("openInterest", 0), pe_oi=pe.get("openInterest", 0),
                ce_poi=ce.get("openInterest", 0) - ce.get("changeinOpenInterest", 0),
                pe_poi=pe.get("openInterest", 0) - pe.get("changeinOpenInterest", 0),
                ce_vol=ce.get("totalTradedVolume", 0), pe_vol=pe.get("totalTradedVolume", 0),
                ce_iv=ce.get("impliedVolatility", 0), pe_iv=pe.get("impliedVolatility", 0),
                ce_delta=0, pe_delta=0, ce_theta=0, pe_theta=0, ce_gamma=0, pe_gamma=0, ce_vega=0, pe_vega=0))
        return pd.DataFrame(rows), exp, "NSE"
    except Exception:
        return None
