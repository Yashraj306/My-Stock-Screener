import time
import datetime as dt
import pandas as pd
import requests
import yfinance as yf

from config import UPSTOX_TOKEN, INDICES, MODE
from universe import INDEX_MAP

UPSTOX_BASE = "https://api.upstox.com"
NSE_BASE = "https://www.nseindia.com"


def ist_now():
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5, minutes=30)


def underlying_key(name):
    if name in INDEX_MAP:
        return INDEX_MAP[name]
    return f"NSE_EQ|{name}"


def upstox_get(path, params=None):
    if not UPSTOX_TOKEN:
        return None
    h = {"Accept": "application/json", "Authorization": f"Bearer {UPSTOX_TOKEN}"}
    for _ in range(2):
        try:
            r = requests.get(UPSTOX_BASE + path, params=params, headers=h, timeout=12)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(0.2)
    return None


def fetch_upstox_candles(symbol, interval="day", days=90):
    """
    Tries Upstox Historical API first. If Token is expired or missing, 
    seamlessly falls back to Yahoo Finance so the screener never halts.
    """
    # 1. Try Upstox API
    if UPSTOX_TOKEN:
        key = underlying_key(symbol)
        if key:
            to_date = ist_now().strftime("%Y-%m-%d")
            from_date = (ist_now() - dt.timedelta(days=days)).strftime("%Y-%m-%d")
            path = f"/v2/historical-candle/{key}/{interval}/{to_date}/{from_date}"
            res = upstox_get(path)
            if res and res.get("status") == "success" and res.get("data", {}).get("candles"):
                candles = res["data"]["candles"]
                df = pd.DataFrame(candles, columns=["Date", "Open", "High", "Low", "Close", "Volume", "OI"])
                df["Date"] = pd.to_datetime(df["Date"])
                df = df.sort_values(by="Date").reset_index(drop=True)
                for col in ["Open", "High", "Low", "Close", "Volume"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                return df

    # 2. Fast Fallback (Yahoo Finance)
    ticker_sym = "^NSEI" if symbol == "NIFTY" else ("^NSEBANK" if symbol == "BANKNIFTY" else ("^BSESN" if symbol == "SENSEX" else f"{symbol}.NS"))
    try:
        ydf = yf.download(ticker_sym, period="3mo", interval="1d", progress=False)
        if not ydf.empty and len(ydf) >= 15:
            if isinstance(ydf.columns, pd.MultiIndex):
                ydf.columns = ydf.columns.get_level_values(0)
            ydf = ydf.reset_index().rename(columns=lambda c: str(c).capitalize())
            return ydf
    except Exception:
        pass

    return None


def nearest_expiry(key, is_index=False):
    j = upstox_get("/v2/option/contract", {"instrument_key": key})
    if not j or not j.get("data"):
        return None
    gap = 0 if MODE == "MORNING" else 1
    start = (ist_now().date() + dt.timedelta(days=gap)).isoformat()
    ex = sorted({x["expiry"][:10] for x in j["data"] if x.get("expiry")})
    ex = [e for e in ex if e >= start]
    return ex[0] if ex else None


def chain_upstox(name):
    """
    Fetches option chain directly from Upstox.
    """
    key = underlying_key(name)
    if not key:
        return None
    exp = nearest_expiry(key, is_index=(name in INDEX_MAP))
    if not exp:
        return None
    j = upstox_get("/v2/option/chain", {"instrument_key": key, "expiry_date": exp})
    if not j or not j.get("data"):
        return None

    rows = []
    for x in j["data"]:
        ce, pe = x.get("call_options", {}), x.get("put_options", {})
        cm, pm = ce.get("market_data", {}), pe.get("market_data", {})
        cg, pg = ce.get("option_greeks", {}), pe.get("option_greeks", {})
        rows.append(dict(
            strike=x.get("strike_price"),
            spot=x.get("underlying_spot_price"),
            ce_ltp=cm.get("ltp", 0),
            pe_ltp=pm.get("ltp", 0),
            ce_oi=cm.get("oi", 0),
            pe_oi=pm.get("oi", 0),
            ce_poi=cm.get("prev_oi", 0),
            pe_poi=pm.get("prev_oi", 0),
            ce_vol=cm.get("volume", 0),
            pe_vol=pm.get("volume", 0),
            ce_iv=cg.get("iv", 0),
            pe_iv=pg.get("iv", 0),
            ce_delta=cg.get("delta", 0),
            pe_delta=pg.get("delta", 0)
        ))
    return pd.DataFrame(rows), exp, "Upstox"


def nse_session():
    """
    Prepares a requests session with proper headers for NSE API fallback.
    """
    try:
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://www.nseindia.com"
        })
        s.get(NSE_BASE, timeout=10)
        return s
    except Exception:
        return None


def chain_nse(name, sess):
    """
    Fallback option chain fetch via NSE India official API.
    """
    if not sess or name == "SENSEX":
        return None
    try:
        kind = "indices" if name in ["NIFTY", "BANKNIFTY"] else "equities"
        url = f"{NSE_BASE}/api/option-chain-{kind}"
        r = sess.get(url, params={"symbol": name}, timeout=10)
        if r.status_code != 200:
            return None
        rec = r.json().get("records", {})
        exp = rec.get("expiryDates", [])[0]
        spot = rec.get("underlyingValue", 0)

        rows = []
        for x in rec.get("data", []):
            if x.get("expiryDate") != exp:
                continue
            ce, pe = x.get("CE", {}), x.get("PE", {})
            rows.append(dict(
                strike=x.get("strikePrice"),
                spot=spot,
                ce_ltp=ce.get("lastPrice", 0),
                pe_ltp=pe.get("lastPrice", 0),
                ce_oi=ce.get("openInterest", 0),
                pe_oi=pe.get("openInterest", 0),
                ce_poi=ce.get("openInterest", 0) - ce.get("changeinOpenInterest", 0),
                pe_poi=pe.get("openInterest", 0) - pe.get("changeinOpenInterest", 0),
                ce_vol=ce.get("totalTradedVolume", 0),
                pe_vol=pe.get("totalTradedVolume", 0),
                ce_iv=ce.get("impliedVolatility", 0),
                pe_iv=pe.get("impliedVolatility", 0),
                ce_delta=0, pe_delta=0
            ))
        return pd.DataFrame(rows), exp, "NSE"
    except Exception:
        return None
