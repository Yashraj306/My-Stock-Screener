import datetime as dt
import logging
import os
from urllib.parse import quote

import pandas as pd
import requests

from universe import INDEX_MAP, ISIN_MAP

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
UPSTOX_BASE_V2 = "https://api.upstox.com/v2"


def _get_upstox_token() -> str:
    return (
        os.getenv("UPSTOX_ACCESS_TOKEN")
        or os.getenv("UPSTOX_TOKEN")
        or ""
    ).strip()


def _instrument_key_for_symbol(symbol: str) -> str:
    sym = str(symbol or "").strip().upper()
    if sym in INDEX_MAP:
        return INDEX_MAP[sym]
    isin = ISIN_MAP.get(sym)
    if isin:
        return f"NSE_EQ|{isin}"
    return ""


def _fetch_expiries(instrument_key: str, headers: dict) -> list[str]:
    encoded = quote(instrument_key, safe="")
    url = f"{UPSTOX_BASE_V2}/option/contract"
    params = {"instrument_key": instrument_key}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code == 200:
            data = (resp.json() or {}).get("data") or []
            expiries = sorted({
                str(item.get("expiry_date") or item.get("expiry") or "")[:10]
                for item in data
                if (item.get("expiry_date") or item.get("expiry"))
            })
            today_str = dt.datetime.now(IST).date().isoformat()
            valid_exp = [e for e in expiries if e >= today_str]
            return valid_exp or expiries
    except Exception as exc:
        logging.debug("Expiry fetch failed for %s: %s", instrument_key, exc)

    # Fallback endpoint attempt
    try:
        url2 = f"{UPSTOX_BASE_V2}/expired-instruments/option/contract"
        resp2 = requests.get(url2, headers=headers, params={"instrument_key": encoded}, timeout=10)
        if resp2.status_code == 200:
            data2 = (resp2.json() or {}).get("data") or []
            expiries2 = sorted({
                str(item.get("expiry_date") or item.get("expiry") or "")[:10]
                for item in data2
                if (item.get("expiry_date") or item.get("expiry"))
            })
            return expiries2
    except Exception:
        pass
    return []


def chain_upstox(symbol: str, spot: float = 0.0, expiry: str | None = None):
    """Fetches Upstox v2 option chain and returns (df, expiry)."""
    token = _get_upstox_token()
    inst_key = _instrument_key_for_symbol(symbol)
    if not token or not inst_key:
        return pd.DataFrame(), expiry

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    target_expiry = expiry
    if not target_expiry:
        expiries = _fetch_expiries(inst_key, headers)
        if expiries:
            target_expiry = expiries[0]

    params = {"instrument_key": inst_key}
    if target_expiry:
        params["expiry_date"] = target_expiry

    try:
        resp = requests.get(f"{UPSTOX_BASE_V2}/option/chain", headers=headers, params=params, timeout=12)
        if resp.status_code != 200:
            logging.debug("Upstox option chain status %s for %s", resp.status_code, symbol)
            return pd.DataFrame(), target_expiry

        payload_data = (resp.json() or {}).get("data") or []
        if not payload_data:
            return pd.DataFrame(), target_expiry

        rows = []
        resolved_expiry = target_expiry
        for item in payload_data:
            if not resolved_expiry and item.get("expiry"):
                resolved_expiry = str(item.get("expiry"))[:10]

            strike = item.get("strike_price")
            if strike is None:
                continue

            ce = item.get("call_options") or {}
            pe = item.get("put_options") or {}
            ce_md = ce.get("market_data") or {}
            pe_md = pe.get("market_data") or {}
            ce_gk = ce.get("option_greeks") or {}
            pe_gk = pe.get("option_greeks") or {}

            rows.append({
                "strike": float(strike),
                "ce_ltp": float(ce_md.get("ltp") or 0.0),
                "ce_bid": float(ce_md.get("bid_price") or 0.0),
                "ce_ask": float(ce_md.get("ask_price") or 0.0),
                "ce_oi": int(ce_md.get("oi") or 0),
                "ce_prev_oi": int(ce_md.get("prev_oi") or 0),
                "ce_vol": int(ce_md.get("volume") or 0),
                "ce_iv": float(ce_gk.get("iv") or ce.get("iv") or 0.0),
                "ce_delta": float(ce_gk.get("delta") or 0.0),
                "ce_theta": float(ce_gk.get("theta") or 0.0),
                "pe_ltp": float(pe_md.get("ltp") or 0.0),
                "pe_bid": float(pe_md.get("bid_price") or 0.0),
                "pe_ask": float(pe_md.get("ask_price") or 0.0),
                "pe_oi": int(pe_md.get("oi") or 0),
                "pe_prev_oi": int(pe_md.get("prev_oi") or 0),
                "pe_vol": int(pe_md.get("volume") or 0),
                "pe_iv": float(pe_gk.get("iv") or pe.get("iv") or 0.0),
                "pe_delta": float(pe_gk.get("delta") or 0.0),
                "pe_theta": float(pe_gk.get("theta") or 0.0),
            })

        if not rows:
            return pd.DataFrame(), resolved_expiry

        df = pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)
        return df, resolved_expiry
    except Exception as exc:
        logging.debug("Upstox option chain exception for %s: %s", symbol, exc)
        return pd.DataFrame(), target_expiry
