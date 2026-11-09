
import time
import datetime as dt

import pandas as pd
import requests

from config import *
from universe import eq_keys


# ==========================================
# CONSTANTS
# ==========================================

UPSTOX_BASE = "https://api.upstox.com"
NSE_BASE = "https://www.nseindia.com"

TIMEOUT = 20
RETRIES = 3


# ==========================================
# SAFE CONVERSION
# ==========================================

def safe_float(value, default=0.0):
    try:
        if value is None:
            return default

        result = float(value)

        if pd.isna(result):
            return default

        return result

    except (ValueError, TypeError):
        return default


def normalize_expiry(value):
    """
    Convert expiry dates into YYYY-MM-DD.
    Supports:
    10-Nov-2026
    2026-11-10
    2026-11-10T00:00:00
    """

    if not value:
        return None

    try:
        value = str(value).strip()

        try:
            return dt.date.fromisoformat(
                value[:10]
            ).isoformat()

        except ValueError:
            pass

        for fmt in (
            "%d-%b-%Y",
            "%d-%B-%Y",
            "%d-%m-%Y",
            "%Y/%m/%d"
        ):
            try:
                return dt.datetime.strptime(
                    value, fmt
                ).date().isoformat()

            except ValueError:
                continue

        return None

    except Exception:
        return None


def valid_expiry(expiry):
    normalized = normalize_expiry(expiry)

    if not normalized:
        return False

    try:
        expiry_date = dt.date.fromisoformat(normalized)
        return expiry_date >= ist_now().date()

    except ValueError:
        return False


# ==========================================
# UNDERLYING INSTRUMENT KEY
# ==========================================

def underlying_key(name):

    name = str(name).upper().strip()

    if name == "NIFTY":
        return "NSE_INDEX|Nifty 50"

    if name == "BANKNIFTY":
        return "NSE_INDEX|Nifty Bank"

    try:
        keys = eq_keys() or {}
        return keys.get(name)

    except Exception as e:
        print("Instrument key error:", e)
        return None


# ==========================================
# UPSTOX API
# ==========================================

def upstox_get(path, params=None):

    if not UPSTOX_TOKEN:
        return None

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {UPSTOX_TOKEN}",
        "Content-Type": "application/json"
    }

    for attempt in range(RETRIES):

        try:

            response = requests.get(
                UPSTOX_BASE + path,
                params=params or {},
                headers=headers,
                timeout=TIMEOUT
            )

            if response.status_code == 200:

                data = response.json()

                if isinstance(data, dict):
                    return data

                return None

            print(
                "Upstox API error:",
                response.status_code,
                path,
                response.text[:150]
            )

            if response.status_code in (400, 401, 403, 404):
                break

        except requests.RequestException as e:
            print("Upstox request error:", e)

        except ValueError as e:
            print("Upstox JSON error:", e)
            break

        if attempt < RETRIES - 1:
            time.sleep(1 + attempt)

    return None


# ==========================================
# NEAREST EXPIRY
# ==========================================

def nearest_expiry(key, is_index):

    if not key:
        return None

    response = upstox_get(
        "/v2/option/contract",
        {"instrument_key": key}
    )

    if not response:
        return None

    contracts = response.get("data") or []

    if not isinstance(contracts, list):
        return None

    gap = (
        3 if MODE == "BTST"
        else (2 if is_index else 0)
    )

    start_date = (
        ist_now().date()
        + dt.timedelta(days=gap)
    )

    expiries = set()

    for contract in contracts:

        if not isinstance(contract, dict):
            continue

        normalized = normalize_expiry(
            contract.get("expiry")
        )

        if not normalized:
            continue

        expiry_date = dt.date.fromisoformat(normalized)

        if expiry_date >= start_date:
            expiries.add(normalized)

    if not expiries:
        return None

    return sorted(expiries)[0]


# ==========================================
# NORMALIZE OPTION ROW
# ==========================================

def make_option_row(
    strike,
    spot,
    ce=None,
    pe=None,
    nse=False
):

    ce = ce or {}
    pe = pe or {}

    if nse:

        cm = ce
        pm = pe

        cg = {}
        pg = {}

        ce_oi = safe_float(cm.get("openInterest"))
        pe_oi = safe_float(pm.get("openInterest"))

        ce_change = safe_float(
            cm.get("changeinOpenInterest")
        )

        pe_change = safe_float(
            pm.get("changeinOpenInterest")
        )

        ce_poi = max(0, ce_oi - ce_change)
        pe_poi = max(0, pe_oi - pe_change)

    else:

        cm = ce.get("market_data") or {}
        pm = pe.get("market_data") or {}

        cg = ce.get("option_greeks") or {}
        pg = pe.get("option_greeks") or {}

        ce_oi = safe_float(cm.get("oi"))
        pe_oi = safe_float(pm.get("oi"))

        ce_poi = safe_float(cm.get("prev_oi"))
        pe_poi = safe_float(pm.get("prev_oi"))

    return {

        "strike": safe_float(strike),
        "spot": safe_float(spot),

        "ce_ltp": safe_float(cm.get(
            "lastPrice" if nse else "ltp"
        )),

        "pe_ltp": safe_float(pm.get(
            "lastPrice" if nse else "ltp"
        )),

        "ce_oi": ce_oi,
        "pe_oi": pe_oi,

        "ce_poi": ce_poi,
        "pe_poi": pe_poi,

        "ce_vol": safe_float(cm.get(
            "totalTradedVolume" if nse else "volume"
        )),

        "pe_vol": safe_float(pm.get(
            "totalTradedVolume" if nse else "volume"
        )),

        "ce_iv": safe_float(cg.get(
            "iv" if not nse else "impliedVolatility"
        )),

        "pe_iv": safe_float(pg.get(
            "iv" if not nse else "impliedVolatility"
        )),

        "ce_delta": safe_float(cg.get("delta")),
        "pe_delta": safe_float(pg.get("delta")),

        "ce_theta": safe_float(cg.get("theta")),
        "pe_theta": safe_float(pg.get("theta")),

        "ce_gamma": safe_float(cg.get("gamma")),
        "pe_gamma": safe_float(pg.get("gamma")),

        "ce_vega": safe_float(cg.get("vega")),
        "pe_vega": safe_float(pg.get("vega"))
    }


# ==========================================
# UPSTOX OPTION CHAIN
# ==========================================

def chain_upstox(name):

    if not UPSTOX_TOKEN:
        return None

    name = str(name).upper().strip()

    key = underlying_key(name)

    if not key:
        print("Missing underlying key:", name)
        return None

    expiry = nearest_expiry(
        key,
        name in INDICES
    )

    if not expiry:
        print("No valid Upstox expiry:", name)
        return None

    response = upstox_get(
        "/v2/option/chain",
        {
            "instrument_key": key,
            "expiry_date": expiry
        }
    )

    if not response:
        return None

    contracts = response.get("data") or []

    if not isinstance(contracts, list) or not contracts:
        return None

    rows = []

    for item in contracts:

        if not isinstance(item, dict):
            continue

        ce = item.get("call_options") or {}
        pe = item.get("put_options") or {}

        row = make_option_row(
            item.get("strike_price"),
            item.get("underlying_spot_price"),
            ce,
            pe
        )

        if row["strike"] <= 0:
            continue

        rows.append(row)

    if not rows:
        return None

    df = pd.DataFrame(rows)

    df = df.sort_values(
        "strike"
    ).reset_index(drop=True)

    return df, expiry, "Upstox"


# ==========================================
# NSE SESSION
# ==========================================

def nse_session():

    try:

        session = requests.Session()

        session.headers.update({

            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124 Safari/537.36"
            ),

            "Accept": "application/json, text/plain, */*",

            "Accept-Language": "en-US,en;q=0.9",

            "Referer": NSE_BASE + "/option-chain"

        })

        response = session.get(
            NSE_BASE,
            timeout=15
        )

        response.raise_for_status()

        return session

    except requests.RequestException as e:

        print("NSE session error:", e)

        return None


# ==========================================
# NSE OPTION CHAIN FALLBACK
# ==========================================

def chain_nse(name, sess):

    if sess is None:
        return None

    try:

        name = str(name).upper().strip()

        kind = (
            "indices"
            if name in INDICES
            else "equities"
        )

        url = (
            f"{NSE_BASE}/api/option-chain-{kind}"
        )

        response = sess.get(
            url,
            params={"symbol": name},
            timeout=15
        )

        if response.status_code != 200:
            print("NSE status:", response.status_code)
            return None

        payload = response.json()

        records = payload.get("records") or {}

        expiry_dates = records.get("expiryDates") or []

        data = records.get("data") or []

        spot = safe_float(
            records.get("underlyingValue")
        )

        if not expiry_dates or not data or spot <= 0:
            return None

        # Select nearest valid expiry.
        expiry_map = {}

        for raw_expiry in expiry_dates:

            normalized = normalize_expiry(raw_expiry)

            if normalized and valid_expiry(normalized):
                expiry_map[normalized] = raw_expiry

        if not expiry_map:
            return None

        expiry = sorted(expiry_map)[0]

        original_expiry = expiry_map[expiry]

        rows = []

        for item in data:

            if item.get("expiryDate") != original_expiry:
                continue

            ce = item.get("CE") or {}
            pe = item.get("PE") or {}

            row = make_option_row(
                item.get("strikePrice"),
                spot,
                ce,
                pe,
                nse=True
            )

            if row["strike"] <= 0:
                continue

            rows.append(row)

        if not rows:
            return None

        df = pd.DataFrame(rows)

        df = df.sort_values(
            "strike"
        ).reset_index(drop=True)

        # Standardized expiry format.
        return df, expiry, "NSE"

    except requests.RequestException as e:

        print("NSE request error:", e)
        return None

    except (ValueError, KeyError, TypeError) as e:

        print("NSE response error:", e)
        return None

    except Exception as e:

        print("NSE chain error:", e)
        return None
