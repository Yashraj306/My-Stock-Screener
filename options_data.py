import datetime as dt
import logging
import os

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


def _safe_float(val, default: float = 0.0) -> float:
    if val is None or val == "":
        return default
    try:
        fv = float(val)
        return default if pd.isna(fv) else fv
    except (TypeError, ValueError):
        return default


def _safe_int(val, default: int = 0) -> int:
    fv = _safe_float(val, float(default))
    return int(round(fv))


def _fetch_expiries(instrument_key: str, headers: dict) -> list[str]:
    today_str = dt.datetime.now(IST).date().isoformat()
    for endpoint in (
        f"{UPSTOX_BASE_V2}/option/contract",
        f"{UPSTOX_BASE_V2}/expired-instruments/option/contract",
    ):
        try:
            resp = requests.get(
                endpoint,
                headers=headers,
                params={"instrument_key": instrument_key},
                timeout=10,
            )
            if resp.status_code == 200:
                data = (resp.json() or {}).get("data") or []
                if isinstance(data, dict):
                    data = data.get("contracts") or data.get("data") or []
                expiries = sorted({
                    str(item.get("expiry_date") or item.get("expiry") or "")[:10]
                    for item in (data if isinstance(data, list) else [])
                    if isinstance(item, dict) and (item.get("expiry_date") or item.get("expiry"))
                })
                valid_exp = [e for e in expiries if len(e) == 10 and e >= today_str]
                if valid_exp:
                    return valid_exp
                if expiries:
                    return expiries
        except Exception as exc:
            logging.debug("Expiry fetch failed on %s for %s: %s", endpoint, instrument_key, exc)
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

    target_expiry = str(expiry).strip()[:10] if expiry else None
    if not target_expiry:
        expiries = _fetch_expiries(inst_key, headers)
        if expiries:
            target_expiry = expiries[0]

    params = {"instrument_key": inst_key}
    if target_expiry:
        params["expiry_date"] = target_expiry

    try:
        resp = requests.get(
            f"{UPSTOX_BASE_V2}/option/chain",
            headers=headers,
            params=params,
            timeout=12,
        )
        if resp.status_code != 200:
            logging.debug("Upstox option chain HTTP %s for %s: %s", resp.status_code, symbol, (resp.text or "")[:120])
            return pd.DataFrame(), target_expiry

        raw_json = resp.json() or {}
        payload_data = raw_json.get("data")
        if isinstance(payload_data, dict):
            payload_data = payload_data.get("chain") or payload_data.get("strikes") or payload_data.get("data") or []
        if not isinstance(payload_data, list) or not payload_data:
            return pd.DataFrame(), target_expiry

        rows = []
        resolved_expiry = target_expiry
        for item in payload_data:
            if not isinstance(item, dict):
                continue
            if not resolved_expiry:
                exp_val = item.get("expiry") or item.get("expiry_date")
                if exp_val:
                    resolved_expiry = str(exp_val)[:10]

            strike = _safe_float(item.get("strike_price") if item.get("strike_price") is not None else item.get("strike"), 0.0)
            if strike <= 0:
                continue

            ce = item.get("call_options") if isinstance(item.get("call_options"), dict) else {}
            pe = item.get("put_options") if isinstance(item.get("put_options"), dict) else {}
            ce_md = ce.get("market_data") if isinstance(ce.get("market_data"), dict) else {}
            pe_md = pe.get("market_data") if isinstance(pe.get("market_data"), dict) else {}
            ce_gk = ce.get("option_greeks") if isinstance(ce.get("option_greeks"), dict) else {}
            pe_gk = pe.get("option_greeks") if isinstance(pe.get("option_greeks"), dict) else {}

            ce_oi = _safe_int(ce_md.get("oi"), 0)
            pe_oi = _safe_int(pe_md.get("oi"), 0)
            ce_prev_oi = _safe_int(ce_md.get("prev_oi"), 0)
            pe_prev_oi = _safe_int(pe_md.get("prev_oi"), 0)

            rows.append({
                "strike": strike,
                "ce_ltp": _safe_float(ce_md.get("ltp"), 0.0),
                "ce_bid": _safe_float(ce_md.get("bid_price"), 0.0),
                "ce_ask": _safe_float(ce_md.get("ask_price"), 0.0),
                "ce_oi": ce_oi,
                "ce_prev_oi": ce_prev_oi,
                "ce_chg_oi": ce_oi - ce_prev_oi if ce_prev_oi > 0 else 0,
                "ce_vol": _safe_int(ce_md.get("volume"), 0),
                "ce_iv": _safe_float(ce_gk.get("iv") if ce_gk.get("iv") is not None else ce.get("iv"), 0.0),
                "ce_delta": _safe_float(ce_gk.get("delta"), 0.0),
                "ce_theta": _safe_float(ce_gk.get("theta"), 0.0),
                "pe_ltp": _safe_float(pe_md.get("ltp"), 0.0),
                "pe_bid": _safe_float(pe_md.get("bid_price"), 0.0),
                "pe_ask": _safe_float(pe_md.get("ask_price"), 0.0),
                "pe_oi": pe_oi,
                "pe_prev_oi": pe_prev_oi,
                "pe_chg_oi": pe_oi - pe_prev_oi if pe_prev_oi > 0 else 0,
                "pe_vol": _safe_int(pe_md.get("volume"), 0),
                "pe_iv": _safe_float(pe_gk.get("iv") if pe_gk.get("iv") is not None else pe.get("iv"), 0.0),
                "pe_delta": _safe_float(pe_gk.get("delta"), 0.0),
                "pe_theta": _safe_float(pe_gk.get("theta"), 0.0),
            })

        if not rows:
            return pd.DataFrame(), resolved_expiry

        df = pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)
        return df, resolved_expiry
    except Exception as exc:
        logging.debug("Upstox option chain exception for %s: %s", symbol, exc)
        return pd.DataFrame(), target_expiry
