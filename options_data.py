
import os
import logging
import datetime as dt

import requests
import pandas as pd

from universe import INDEX_MAP, ISIN_MAP


UPSTOX_TOKEN = os.getenv("UPSTOX_TOKEN", "").strip()

BASE_URL = "https://api.upstox.com/v2"

TIMEOUT = 12

logging.basicConfig(level=logging.INFO)


def get_headers():
    if not UPSTOX_TOKEN:
        return None

    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {UPSTOX_TOKEN}"
    }


def get_instrument_key(symbol):
    """Return the Upstox underlying instrument key."""

    symbol = str(symbol).strip().upper()

    if symbol in INDEX_MAP:
        return INDEX_MAP[symbol]

    isin = ISIN_MAP.get(symbol)

    if isin:
        return f"NSE_EQ|{isin}"

    return None


def _request(url, headers, params=None):
    """Make a safe Upstox API request."""

    if not headers:
        return None

    try:
        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=TIMEOUT
        )

        if response.status_code != 200:
            logging.warning(
                "Upstox API returned HTTP %s",
                response.status_code
            )
            return None

        payload = response.json()

        if not isinstance(payload, dict):
            return None

        if payload.get("status") not in (None, "success"):
            return None

        return payload.get("data")

    except requests.RequestException as exc:
        logging.warning("Upstox request failed: %s", exc)
        return None

    except (ValueError, TypeError) as exc:
        logging.warning("Invalid Upstox response: %s", exc)
        return None


def get_nearest_expiry(inst_key, headers=None):
    """Return the nearest valid expiry date from Upstox."""

    headers = headers or get_headers()

    if not headers or not inst_key:
        return None

    data = _request(
        f"{BASE_URL}/option/contract",
        headers,
        {"instrument_key": inst_key}
    )

    if not isinstance(data, list):
        return None

    today = dt.date.today()
    expiries = set()

    for contract in data:
        if not isinstance(contract, dict):
            continue

        raw_expiry = contract.get("expiry")

        if not raw_expiry:
            continue

        try:
            expiry = dt.date.fromisoformat(
                str(raw_expiry)[:10]
            )

            if expiry >= today:
                expiries.add(expiry)

        except ValueError:
            continue

    if not expiries:
        return None

    return min(expiries).isoformat()


def _safe_float(value):
    try:
        number = float(value)

        if pd.notna(number):
            return number

    except (TypeError, ValueError):
        pass

    return None


def _extract_option(option, prefix):
    """Extract actual market data and Greeks without fabricated defaults."""

    option = option if isinstance(option, dict) else {}

    market = option.get("market_data") or {}
    greeks = option.get("option_greeks") or {}

    if not isinstance(market, dict):
        market = {}

    if not isinstance(greeks, dict):
        greeks = {}

    return {
        f"{prefix}_ltp": _safe_float(market.get("ltp")),
        f"{prefix}_bid": _safe_float(market.get("bid_price")),
        f"{prefix}_ask": _safe_float(market.get("ask_price")),
        f"{prefix}_oi": _safe_float(market.get("oi")),
        f"{prefix}_poi": _safe_float(market.get("prev_oi")),
        f"{prefix}_vol": _safe_float(market.get("volume")),
        f"{prefix}_iv": _safe_float(greeks.get("iv")),
        f"{prefix}_delta": _safe_float(greeks.get("delta")),
        f"{prefix}_theta": _safe_float(greeks.get("theta")),
        f"{prefix}_gamma": _safe_float(greeks.get("gamma")),
        f"{prefix}_vega": _safe_float(greeks.get("vega")),
        f"{prefix}_instrument_key": option.get("instrument_key")
    }


def chain_upstox(symbol):
    """
    Fetch real Upstox option-chain data.

    Returns:
        (dataframe, expiry, spot)

    Returns None if live option data is unavailable.
    No synthetic premium, OI, IV or Greeks are generated.
    """

    headers = get_headers()

    if not headers:
        logging.warning("UPSTOX_TOKEN is not configured.")
        return None

    inst_key = get_instrument_key(symbol)

    if not inst_key:
        logging.warning(
            "No Upstox instrument key available for %s",
            symbol
        )
        return None

    expiry = get_nearest_expiry(inst_key, headers)

    if not expiry:
        logging.warning(
            "No valid option expiry found for %s",
            symbol
        )
        return None

    raw_data = _request(
        f"{BASE_URL}/option/chain",
        headers,
        {
            "instrument_key": inst_key,
            "expiry_date": expiry
        }
    )

    if not isinstance(raw_data, list) or not raw_data:
        return None

    rows = []
    spot_values = []

    for item in raw_data:
        if not isinstance(item, dict):
            continue

        strike = _safe_float(item.get("strike_price"))

        if strike is None or strike <= 0:
            continue

        spot = _safe_float(
            item.get("underlying_spot_price")
        )

        if spot is not None and spot > 0:
            spot_values.append(spot)

        ce = item.get("call_options") or {}
        pe = item.get("put_options") or {}

        row = {
            "strike": strike,
            "spot": spot
        }

        row.update(_extract_option(ce, "ce"))
        row.update(_extract_option(pe, "pe"))

        rows.append(row)

    if not rows:
        return None

    df = pd.DataFrame(rows)

    df = df.drop_duplicates(
        subset=["strike"]
    ).sort_values("strike").reset_index(drop=True)

    if df.empty:
        return None

    spot_price = (
        float(spot_values[-1])
        if spot_values else None
    )

    # Require real spot and at least some usable option prices.
    has_ce_price = (
        pd.to_numeric(df["ce_ltp"], errors="coerce")
        .gt(0).any()
    )

    has_pe_price = (
        pd.to_numeric(df["pe_ltp"], errors="coerce")
        .gt(0).any()
    )

    if spot_price is None or not (has_ce_price or has_pe_price):
        logging.warning(
            "Incomplete option-chain market data for %s",
            symbol
        )
        return None

    return df, expiry, spot_price


def nse_session():
    """
    Create an NSE session for optional future public-data support.
    """

    session = requests.Session()

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 14) "
            "AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-IN,en;q=0.9"
    })

    try:
        session.get(
            "https://www.nseindia.com",
            timeout=8
        )
    except requests.RequestException:
        pass

    return session


def chain_nse(symbol, sess=None):
    """
    NSE fallback intentionally disabled.

    Do not treat missing NSE data as live option-chain data.
    """

    logging.info(
        "NSE option-chain fallback is disabled for %s.",
        symbol
    )

    return None
