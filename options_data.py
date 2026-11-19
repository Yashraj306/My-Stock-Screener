# options_data.py
import datetime as dt
import logging
import os
import pandas as pd
import requests
from universe import INDEX_MAP, ISIN_MAP

UPSTOX_TOKEN = os.getenv("UPSTOX_TOKEN", "").strip()


def get_instrument_key(symbol: str) -> str:
    if symbol in INDEX_MAP:
        return INDEX_MAP[symbol]
    if symbol in ISIN_MAP:
        return f"NSE_EQ|{ISIN_MAP[symbol]}"
    return f"NSE_EQ|{symbol}"


def get_nearest_expiry(inst_key: str, headers: dict) -> str | None:
    url = "https://api.upstox.com/v2/option/contract"
    try:
        r = requests.get(url, headers=headers, params={"instrument_key": inst_key}, timeout=8)
        if r.status_code == 200:
            contracts = r.json().get("data", [])
            expiries = sorted({c.get("expiry") for c in contracts if c.get("expiry")})
            today_str = dt.date.today().strftime("%Y-%m-%d")
            valid = [e for e in expiries if e >= today_str]
            if valid:
                return valid[0]
    except Exception as exc:
        logging.debug("Expiry fetch error for %s: %s", inst_key, exc)
    return None


def _extract_bid_ask(md: dict, leg_dict: dict) -> tuple[float, float]:
    bid = md.get("bid_price") or leg_dict.get("bid_price") or 0.0
    ask = md.get("ask_price") or leg_dict.get("ask_price") or 0.0
    depth = md.get("depth") or {}
    if (not bid or float(bid) <= 0) and isinstance(depth.get("buy"), list) and depth["buy"]:
        bid = depth["buy"][0].get("price") or 0.0
    if (not ask or float(ask) <= 0) and isinstance(depth.get("sell"), list) and depth["sell"]:
        ask = depth["sell"][0].get("price") or 0.0
    return float(bid or 0.0), float(ask or 0.0)


def chain_upstox(symbol: str):
    if not UPSTOX_TOKEN:
        return None
    headers = {"Accept": "application/json", "Authorization": f"Bearer {UPSTOX_TOKEN}"}
    inst_key = get_instrument_key(symbol)
    expiry = get_nearest_expiry(inst_key, headers)
    if not expiry:
        return None

    url = "https://api.upstox.com/v2/option/chain"
    try:
        r = requests.get(url, headers=headers, params={"instrument_key": inst_key, "expiry_date": expiry}, timeout=10)
        if r.status_code != 200:
            return None
        raw_data = r.json().get("data", [])
        if not raw_data:
            return None

        rows = []
        spot_price = 0.0
        for item in raw_data:
            strike = item.get("strike_price")
            spot_price = item.get("underlying_spot_price") or spot_price
            ce = item.get("call_options") or {}
            pe = item.get("put_options") or {}
            ce_md = ce.get("market_data") or {}
            pe_md = pe.get("market_data") or {}
            ce_gk = ce.get("option_greeks") or {}
            pe_gk = pe.get("option_greeks") or {}
            ce_bid, ce_ask = _extract_bid_ask(ce_md, ce)
            pe_bid, pe_ask = _extract_bid_ask(pe_md, pe)

            rows.append({
                "strike": float(strike),
                "spot": float(spot_price or 0.0),
                "ce_ltp": float(ce_md.get("ltp") or 0.0),
                "ce_bid": ce_bid,
                "ce_ask": ce_ask,
                "ce_oi": int(ce_md.get("oi") or 0),
                "ce_poi": int(ce_md.get("prev_oi") or 0),
                "ce_vol": int(ce_md.get("volume") or 0),
                "ce_iv": float(ce_gk.get("iv") or 0.0),
                "ce_delta": float(ce_gk.get("delta") or 0.0),
                "ce_theta": float(ce_gk.get("theta") or 0.0),
                "pe_ltp": float(pe_md.get("ltp") or 0.0),
                "pe_bid": pe_bid,
                "pe_ask": pe_ask,
                "pe_oi": int(pe_md.get("oi") or 0),
                "pe_poi": int(pe_md.get("prev_oi") or 0),
                "pe_vol": int(pe_md.get("volume") or 0),
                "pe_iv": float(pe_gk.get("iv") or 0.0),
                "pe_delta": float(pe_gk.get("delta") or 0.0),
                "pe_theta": float(pe_gk.get("theta") or 0.0),
            })

        return pd.DataFrame(rows), expiry, float(spot_price or 0.0)
    except Exception as exc:
        logging.debug("Upstox option chain error for %s: %s", symbol, exc)
        return None
