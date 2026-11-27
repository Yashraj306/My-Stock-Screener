import datetime as dt
import logging
import os
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

UNDERLYING_KEYS: dict[str, str] = {
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


def _get_expiries(instrument_key: str, headers: dict[str, str]) -> list[str]:
    url = f"{UPSTOX_BASE_V2}/option/contract?instrument_key={quote(instrument_key, safe='')}"
    try:
        resp = requests.get(url, headers=headers, timeout=6)
        if resp.status_code != 200:
            return []
        items = (resp.json() or {}).get("data") or []
        today_str = dt.datetime.now(IST).date().isoformat()
        exps = sorted({
            str(it.get("expiry") or "")[:10]
            for it in items
            if str(it.get("expiry") or "")[:10] >= today_str
        })
        return exps
    except Exception:
        return []


def chain_upstox(symbol: str, spot: float = 0.0) -> tuple[pd.DataFrame | None, str | None]:
    sym_u = str(symbol or "").strip().upper()
    ikey = UNDERLYING_KEYS.get(sym_u) or (f"NSE_EQ|{sym_u}" if sym_u else "")
    if not UPSTOX_ACCESS_TOKEN or not ikey:
        return None, None

    headers = {"Authorization": f"Bearer {UPSTOX_ACCESS_TOKEN}", "Accept": "application/json"}
    expiries = _get_expiries(ikey, headers)
    if not expiries:
        return None, None

    chosen_expiry = expiries[0]
    url = (
        f"{UPSTOX_BASE_V2}/option/chain"
        f"?instrument_key={quote(ikey, safe='')}&expiry_date={quote(chosen_expiry, safe='')}"
    )
    try:
        resp = requests.get(url, headers=headers, timeout=6)
        if resp.status_code != 200:
            return None, chosen_expiry
        raw_list = (resp.json() or {}).get("data") or []
        if not raw_list:
            return None, chosen_expiry

        rows: list[dict[str, Any]] = []
        for entry in raw_list:
            strike = float(entry.get("strike_price") or 0.0)
            if strike <= 0:
                continue
            ce = entry.get("call_options") or {}
            pe = entry.get("put_options") or {}
            ce_md = ce.get("market_data") or {}
            pe_md = pe.get("market_data") or {}
            ce_gk = ce.get("option_greeks") or {}
            pe_gk = pe.get("option_greeks") or {}

            ce_oi = int(ce_md.get("oi") or 0)
            ce_prev_oi = int(ce_md.get("prev_oi") or ce_oi)
            pe_oi = int(pe_md.get("oi") or 0)
            pe_prev_oi = int(pe_md.get("prev_oi") or pe_oi)

            rows.append({
                "strike": strike,
                "ce_ltp": float(ce_md.get("ltp") or 0.0),
                "ce_oi": ce_oi,
                "ce_prev_oi": ce_prev_oi,
                "ce_oi_chg": ce_oi - ce_prev_oi,
                "ce_vol": int(ce_md.get("volume") or 0),
                "ce_iv": float(ce_gk.get("iv") or 0.0),
                "ce_delta": float(ce_gk.get("delta") or 0.0),
                "pe_ltp": float(pe_md.get("ltp") or 0.0),
                "pe_oi": pe_oi,
                "pe_prev_oi": pe_prev_oi,
                "pe_oi_chg": pe_oi - pe_prev_oi,
                "pe_vol": int(pe_md.get("volume") or 0),
                "pe_iv": float(pe_gk.get("iv") or 0.0),
                "pe_delta": float(pe_gk.get("delta") or 0.0),
            })

        if not rows:
            return None, chosen_expiry
        return pd.DataFrame(rows).sort_values("strike").reset_index(drop=True), chosen_expiry
    except Exception as exc:
        logging.debug("chain_upstox error for %s: %s", sym_u, exc)
        return None, chosen_expiry


def build_upstox_smartlists_and_movers(
    candles: dict[str, pd.DataFrame],
    live_overrides: dict[str, dict[str, float]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """
    Builds Upstox-inspired screener buckets from NSE/BSE/Upstox candle & live quote data:
    - top_gainers / top_losers (Trending Stocks)
    - intraday_recovery (Day Low -> LTP recovery %)
    - breakout_52w / near_52w_high (52-week high/low proximity)
    """
    ov_map = live_overrides or {}
    gainers: list[dict[str, Any]] = []
    losers: list[dict[str, Any]] = []
    recovery_list: list[dict[str, Any]] = []
    high_52w_list: list[dict[str, Any]] = []

    for sym, df in candles.items():
        if sym in {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"} or df is None or df.empty:
            continue
        ov = ov_map.get(sym) or {}
        close_s = pd.to_numeric(df["Close"], errors="coerce").dropna()
        high_s = pd.to_numeric(df["High"], errors="coerce").dropna()
        low_s = pd.to_numeric(df["Low"], errors="coerce").dropna()
        if len(close_s) < 2:
            continue

        ltp = float(ov.get("close") or close_s.iloc[-1])
        prev_c = float(ov.get("prev_close") or close_s.iloc[-2])
        day_low = float(ov.get("low") or (low_s.iloc[-1] if not low_s.empty else ltp))
        chg_pct = float(ov.get("change_pct")) if "change_pct" in ov else (
            ((ltp - prev_c) / prev_c) * 100.0 if prev_c > 0 else 0.0
        )
        recovery_pct = round(((ltp - day_low) / day_low) * 100.0, 2) if day_low > 0 and ltp >= day_low else 0.0

        hi_252 = float(high_s.tail(252).max()) if not high_s.empty else ltp
        pct_from_52w_high = round(((ltp - hi_252) / hi_252) * 100.0, 2) if hi_252 > 0 else 0.0

        rec = {
            "symbol": sym,
            "ltp": round(ltp, 2),
            "change_pct": round(chg_pct, 2),
            "recovery_pct": recovery_pct,
            "high_52w": round(hi_252, 2),
            "pct_from_52w_high": pct_from_52w_high,
        }
        gainers.append(rec)
        losers.append(rec)
        if recovery_pct >= 2.5 and chg_pct > 0:
            recovery_list.append(rec)
        if pct_from_52w_high >= -2.5:
            high_52w_list.append(rec)

    gainers.sort(key=lambda x: x["change_pct"], reverse=True)
    losers.sort(key=lambda x: x["change_pct"])
    recovery_list.sort(key=lambda x: x["recovery_pct"], reverse=True)
    high_52w_list.sort(key=lambda x: x["pct_from_52w_high"], reverse=True)

    return {
        "top_gainers": gainers[:10],
        "top_losers": losers[:10],
        "intraday_recovery": recovery_list[:10],
        "near_52w_high": high_52w_list[:10],
    }
