import gzip, json
import requests
from config import *

_raw = None

def raw():
    global _raw
    if _raw is None:
        try:
            r = requests.get("https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz", timeout=90)
            _raw = json.loads(gzip.decompress(r.content))
        except Exception as e:
            print("instrument file failed:", e)
            _raw = []
    return _raw

def eq_keys():
    return {x["trading_symbol"]: x["instrument_key"] for x in raw()
            if x.get("segment") == "NSE_EQ" and x.get("instrument_type") == "EQ"}

def fo_stocks():
    """All stocks that have F&O contracts. Falls back to a fixed list if the file is unusable."""
    eq = eq_keys()
    under = {x.get("underlying_symbol") for x in raw()
             if x.get("segment") == "NSE_FO" and x.get("instrument_type") in ("CE", "PE")}
    out = sorted(s for s in under if s in eq)
    if len(out) >= 100:
        return out, f"Universe: {len(out)} F&O stocks (auto, from Upstox)"
    return FALLBACK_STOCKS, f"Universe: fallback list of {len(FALLBACK_STOCKS)} stocks (auto-list unavailable)"

def lot_sizes():
    out = {}
    for x in raw():
        if x.get("segment") == "NSE_FO" and x.get("instrument_type") in ("CE", "PE"):
            u, ls = x.get("underlying_symbol"), x.get("lot_size")
            if u and ls and u not in out: out[u] = int(ls)
    return out
