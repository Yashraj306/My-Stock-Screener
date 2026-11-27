import datetime as dt
import io
import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
UPSTOX_ACCESS_TOKEN = (
    os.getenv("UPSTOX_ACCESS_TOKEN")
    or os.getenv("UPSTOX_TOKEN")
    or ""
).strip()
UPSTOX_BASE_V2 = "https://api.upstox.com/v2"
MASTER_CACHE_PATH = Path(".cache/upstox_instruments.json")

# Core NSE F&O / Option-Chain stocks & indices (auto-expanded from Upstox master when token available)
STOCKS_UNIVERSE: list[str] = [
    "NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX",
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "BHARTIARTL", "SBIN",
    "LICI", "ITC", "HINDUNILVR", "LT", "BAJFINANCE", "HCLTECH", "MARUTI", "SUNPHARMA",
    "AXISBANK", "KOTAKBANK", "TITAN", "M&M", "ULTRACEMCO", "ADANIENT", "ADANIPORTS",
    "POWERGRID", "NTPC", "COALINDIA", "ONGC", "BPCL", "IOC", "GAIL",
    "WIPRO", "TECHM", "LTIM", "PERSISTENT", "COFORGE",
    "BAJAJFINSV", "SBILIFE", "HDFCLIFE", "ICICIPRULI", "CHOLAFIN", "SHRIRAMFIN",
    "MUTHOOTFIN", "MANAPPURAM", "RECLTD", "PFC", "IRCTC", "HAL", "BEL", "BHEL",
    "TATAMOTORS", "EICHERMOT", "HEROMOTOCO", "TVSMOTOR", "ASHOKLEY", "MOTHERSON",
    "JSWSTEEL", "TATASTEEL", "HINDALCO", "VEDL", "JINDALSTEL", "NMDC", "NATIONALUM",
    "CIPLA", "DRREDDY", "DIVISLAB", "APOLLOHOSP", "LUPIN", "AUROPHARMA", "TORNTPHARM",
    "ZYDUSLIFE", "BIOCON", "LAURUSLABS", "MAXHEALTH", "FORTIS",
    "ASIANPAINT", "BERGEPAINT", "PIDILITIND", "HAVELLS", "VOLTAS", "DIXON", "POLYCAB",
    "CROMPTON", "AMBUJACEM", "ACC", "GRASIM", "SHREECEM", "DALBHARAT",
    "DABUR", "BRITANNIA", "NESTLEIND", "GODREJCP", "MARICO", "TATACONSUM", "COLPAL",
    "UBL", "VBL", "INDIGO", "CONCOR", "GMRINFRA", "DLF", "GODREJPROP", "OBEROIRLTY",
    "PRESTIGE", "PHOENIXLTD", "TRENT", "DMART", "ABFRL", "PAGEIND", "JUBLFOOD",
    "TATAPOWER", "SUZLON", "NHPC", "SJVN", "IEX", "MCX", "CDSL", "BSE", "ANGELONE",
    "CAMS", "KFINTECH", "MFSL", "NAUKRI", "ZOMATO", "PAYTM", "POLICYBZR", "DELHIVERY",
    "PIIND", "UPL", "SRF", "AARTIIND", "DEEPAKNTR", "NAVINFLUOR", "ATUL", "COROMANDEL",
    "BANDHANBNK", "IDFCFIRSTB", "PNB", "BANKBARODA", "CANBK", "UNIONBANK", "INDUSINDBK",
    "FEDERALBNK", "RBLBANK", "AUBANK", "CUB",
]

BSE_STOCKS_UNIVERSE: list[str] = ["BSE", "CDSL", "MCX", "ANGELONE", "CAMS"]

LOT_SIZES: dict[str, int] = {
    "NIFTY": 75,
    "BANKNIFTY": 30,
    "FINNIFTY": 65,
    "SENSEX": 20,
    "RELIANCE": 250,
    "TCS": 175,
    "HDFCBANK": 550,
    "ICICIBANK": 700,
    "INFY": 400,
    "BHARTIARTL": 475,
    "SBIN": 750,
    "ITC": 1600,
    "HINDUNILVR": 300,
    "LT": 175,
    "BAJFINANCE": 125,
    "HCLTECH": 350,
    "MARUTI": 125,
    "SUNPHARMA": 350,
    "AXISBANK": 625,
    "KOTAKBANK": 400,
    "TITAN": 175,
    "M&M": 200,
    "ULTRACEMCO": 100,
    "ADANIENT": 300,
    "ADANIPORTS": 400,
    "POWERGRID": 1800,
    "NTPC": 1500,
    "COALINDIA": 1350,
    "ONGC": 1925,
    "BPCL": 1800,
    "WIPRO": 1500,
    "TECHM": 600,
    "LTIM": 150,
    "PERSISTENT": 100,
    "COFORGE": 150,
    "BAJAJFINSV": 500,
    "TATAMOTORS": 550,
    "JSWSTEEL": 675,
    "TATASTEEL": 5500,
    "HINDALCO": 700,
    "VEDL": 1150,
    "HAL": 150,
    "BEL": 2850,
    "MCX": 125,
    "CDSL": 350,
    "BSE": 375,
    "ANGELONE": 200,
}

DEFAULT_EQ_KEYS: dict[str, str] = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY": "NSE_INDEX|Fin Nifty",
    "SENSEX": "BSE_INDEX|SENSEX",
    "RELIANCE": "NSE_EQ|INE002A01018",
    "TCS": "NSE_EQ|INE467B01029",
    "HDFCBANK": "NSE_EQ|INE040A01034",
    "ICICIBANK": "NSE_EQ|INE090A01021",
    "INFY": "NSE_EQ|INE009A01021",
    "BHARTIARTL": "NSE_EQ|INE397D01024",
    "SBIN": "NSE_EQ|INE062A01020",
    "ITC": "NSE_EQ|INE154A01025",
    "HINDUNILVR": "NSE_EQ|INE030A01027",
    "LT": "NSE_EQ|INE018A01030",
    "BAJFINANCE": "NSE_EQ|INE296A01024",
    "HCLTECH": "NSE_EQ|INE860A01027",
    "MARUTI": "NSE_EQ|INE585B01010",
    "SUNPHARMA": "NSE_EQ|INE044A01036",
    "AXISBANK": "NSE_EQ|INE238A01034",
    "KOTAKBANK": "NSE_EQ|INE237A01028",
    "TITAN": "NSE_EQ|INE280A01028",
    "M&M": "NSE_EQ|INE101A01026",
    "ULTRACEMCO": "NSE_EQ|INE481G01011",
    "ADANIENT": "NSE_EQ|INE423A01024",
    "ADANIPORTS": "NSE_EQ|INE742F01042",
    "POWERGRID": "NSE_EQ|INE752E01010",
    "NTPC": "NSE_EQ|INE733E01010",
    "COALINDIA": "NSE_EQ|INE522F01014",
    "ONGC": "NSE_EQ|INE213A01029",
    "BPCL": "NSE_EQ|INE029A01011",
    "WIPRO": "NSE_EQ|INE075A01022",
    "TECHM": "NSE_EQ|INE669C01036",
    "LTIM": "NSE_EQ|INE214T01019",
    "PERSISTENT": "NSE_EQ|INE262H01021",
    "COFORGE": "NSE_EQ|INE591G01017",
    "BAJAJFINSV": "NSE_EQ|INE918I01026",
    "TATAMOTORS": "NSE_EQ|INE155A01022",
    "JSWSTEEL": "NSE_EQ|INE019A01038",
    "TATASTEEL": "NSE_EQ|INE081A01020",
    "HINDALCO": "NSE_EQ|INE038A01020",
    "VEDL": "NSE_EQ|INE205A01025",
    "HAL": "NSE_EQ|INE066F01020",
    "BEL": "NSE_EQ|INE263A01024",
    "MCX": "NSE_EQ|INE745G01035",
    "CDSL": "NSE_EQ|INE736A01011",
    "BSE": "NSE_EQ|INE118H01025",
    "ANGELONE": "NSE_EQ|INE732I01013",
    "CAMS": "NSE_EQ|INE596I01012",
}


def eq_keys() -> dict[str, str]:
    out = dict(DEFAULT_EQ_KEYS)
    for sym in STOCKS_UNIVERSE:
        u = str(sym).strip().upper()
        if u and u not in out:
            ex = "BSE_EQ" if u in BSE_STOCKS_UNIVERSE else "NSE_EQ"
            out[u] = f"{ex}|{u}"
    return out


def _download_upstox_master_records() -> list[dict[str, Any]]:
    today_str = dt.datetime.now(IST).date().isoformat()
    if MASTER_CACHE_PATH.exists():
        try:
            cached = json.loads(MASTER_CACHE_PATH.read_text(encoding="utf-8"))
            if cached.get("date") == today_str and isinstance(cached.get("records"), list) and cached["records"]:
                return cached["records"]
        except Exception:
            pass

    urls = [
        "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz",
        "https://assets.upstox.com/market-quote/instruments/exchange/BSE.json.gz",
    ]
    records: list[dict[str, Any]] = []
    for url in urls:
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                df = pd.read_json(io.BytesIO(resp.content), compression="gzip")
                if not df.empty:
                    records.extend(df.to_dict(orient="records"))
        except Exception as exc:
            logging.debug("Upstox master download failed (%s): %s", url, exc)

    if records:
        try:
            MASTER_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            MASTER_CACHE_PATH.write_text(
                json.dumps({"date": today_str, "records": records}),
                encoding="utf-8",
            )
        except Exception:
            pass
    return records


def resolve_upstox_keys_from_master(symbols: list[str]) -> dict[str, str]:
    target = {str(s).strip().upper() for s in (symbols or []) if str(s).strip()}
    if not target:
        return {}
    records = _download_upstox_master_records()
    resolved: dict[str, str] = {}
    for rec in records:
        seg = str(rec.get("segment") or "").upper()
        if seg not in {"NSE_EQ", "BSE_EQ", "NSE_INDEX", "BSE_INDEX"}:
            continue
        sym = str(rec.get("trading_symbol") or rec.get("symbol") or "").strip().upper()
        ikey = str(rec.get("instrument_key") or "").strip()
        if sym in target and ikey:
            if sym not in resolved or seg.startswith("NSE_"):
                resolved[sym] = ikey
                lot = rec.get("lot_size")
                if lot:
                    try:
                        LOT_SIZES[sym] = int(lot)
                    except (TypeError, ValueError):
                        pass
    return resolved


def get_nse_fo_option_stocks(candles: dict[str, Any] | None = None) -> list[str]:
    """
    Returns all NSE F&O / option-chain stocks (excluding pure indices).
    Merges LOT_SIZES multi-lot symbols with Upstox NSE_FO underlying symbols when available.
    """
    indices = {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "MIDCPNIFTY"}
    fo_syms: set[str] = {
        str(k).strip().upper()
        for k, sz in LOT_SIZES.items()
        if str(k).strip().upper() not in indices and int(sz or 0) > 1
    }

    records = _download_upstox_master_records()
    for rec in records:
        seg = str(rec.get("segment") or "").upper()
        if seg == "NSE_FO":
            und = str(rec.get("underlying_symbol") or rec.get("name") or rec.get("trading_symbol") or "").strip().upper()
            if und and und not in indices and und.isalpha():
                fo_syms.add(und)
                lot = rec.get("lot_size")
                if lot and und not in LOT_SIZES:
                    try:
                        LOT_SIZES[und] = int(lot)
                    except (TypeError, ValueError):
                        pass

    if candles:
        fo_syms = {s for s in fo_syms if s in candles}

    return sorted(fo_syms)
