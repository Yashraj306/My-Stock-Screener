"""
NSE equity and stock-F&O universe for My Stock Screener.

Instrument identifiers and F&O lot sizes are loaded from Upstox's official
daily BOD instrument master. They are never guessed or hardcoded.

Exports retained for compatibility:
- STOCKS_UNIVERSE: curated NSE equity watchlist
- ISIN_MAP: symbol -> ISIN
- INDEX_MAP: index alias -> Upstox instrument_key
- LOT_SIZES: F&O underlying symbol -> current lot size
- eq_keys(): equity symbol -> Upstox instrument_key

If the instrument master cannot be downloaded, maps remain empty and
the screener should skip instruments requiring unavailable identifiers.
"""

import gzip
import json
import logging
import os
from datetime import datetime, timezone

import requests


logger = logging.getLogger(__name__)

INSTRUMENT_MASTER_URL = (
    "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
)
REQUEST_TIMEOUT = int(os.getenv("INSTRUMENT_MASTER_TIMEOUT", "25"))

# Liquid large-cap and actively followed NSE symbols. Availability in
# Upstox's current instrument master is checked before an instrument is used.
STOCKS_UNIVERSE = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AUROPHARMA",
    "AXISBANK", "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BALKRISIND",
    "BANDHANBNK", "BANKBARODA", "BEL", "BHARTIARTL", "BOSCHLTD",
    "BPCL", "BRITANNIA", "CANBK", "CHOLAFIN", "CIPLA",
    "COALINDIA", "COFORGE", "COLPAL", "DLF", "DIVISLAB",
    "DRREDDY", "EICHERMOT", "FEDERALBNK", "GAIL", "GRASIM",
    "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "ICICIGI", "ICICIPRULI", "IDFCFIRSTB",
    "INDUSINDBK", "INFY", "IOC", "ITC", "JINDALSTEL",
    "JSWSTEEL", "KOTAKBANK", "LT", "M&M", "MARUTI",
    "MOTHERSON", "MPHASIS", "MUTHOOTFIN", "NESTLEIND", "NTPC",
    "ONGC", "PERSISTENT", "PETRONET", "PFC", "PIDILITIND",
    "PNB", "POWERGRID", "RECLTD", "RELIANCE", "SBILIFE",
    "SBIN", "SHRIRAMFIN", "SIEMENS", "SUNPHARMA", "TATACONSUM",
    "TATAMOTORS", "TATASTEEL", "TCS", "TECHM", "TITAN",
    "TORNTPHARM", "TRENT", "TVSMOTOR", "ULTRACEMCO", "UPL",
    "VEDL", "WIPRO", "ZYDUSLIFE",
]

# Filled only from the downloaded official master.
ISIN_MAP = {}
INDEX_MAP = {}
LOT_SIZES = {}
EQUITY_INSTRUMENT_KEYS = {}
INSTRUMENT_MASTER_UPDATED_AT = None
INSTRUMENT_MASTER_STATUS = "NOT_LOADED"


def _decode_master(response):
    """Decode Upstox's gzipped JSON, tolerating already-decoded JSON."""
    raw = response.content
    if not raw:
        raise ValueError("Instrument master response was empty")

    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)

    parsed = json.loads(raw.decode("utf-8"))

    # Current complete.json.gz is a JSON array. Keep envelope compatibility.
    if isinstance(parsed, dict):
        parsed = parsed.get("data") or parsed.get("instruments")

    if not isinstance(parsed, list):
        raise ValueError("Unexpected Upstox instrument master format")

    return parsed


def refresh_instrument_maps(force=False):
    """
    Download and parse the current Upstox BOD instrument master.

    Returns True on success. On failure, returns False and leaves existing
    maps untouched. No guessed ISINs or F&O lot sizes are inserted.
    """
    global INSTRUMENT_MASTER_UPDATED_AT, INSTRUMENT_MASTER_STATUS

    try:
        response = requests.get(
            INSTRUMENT_MASTER_URL,
            timeout=REQUEST_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        instruments = _decode_master(response)

        new_isin = {}
        new_indexes = {}
        new_lots = {}
        new_equity_keys = {}

        for item in instruments:
            if not isinstance(item, dict):
                continue

            segment = str(item.get("segment") or "").upper()
            instrument_type = str(item.get("instrument_type") or "").upper()
            symbol = str(
                item.get("trading_symbol")
                or item.get("underlying_symbol")
                or ""
            ).strip().upper()

            if segment == "NSE_EQ" and instrument_type in {"EQ", "BE"}:
                isin = str(item.get("isin") or "").strip().upper()
                instrument_key = str(item.get("instrument_key") or "").strip()

                if symbol and isin and instrument_key.startswith("NSE_EQ|"):
                    new_isin[symbol] = isin
                    new_equity_keys[symbol] = instrument_key

            elif segment == "NSE_INDEX":
                name = str(item.get("name") or item.get("trading_symbol") or "").upper()
                instrument_key = str(item.get("instrument_key") or "").strip()
                if instrument_key.startswith("NSE_INDEX|"):
                    if "NIFTY BANK" in name or "BANK NIFTY" in name:
                        new_indexes["BANKNIFTY"] = instrument_key
                    elif "NIFTY FIN SERVICE" in name or "FIN NIFTY" in name:
                        new_indexes["FINNIFTY"] = instrument_key
                    elif "NIFTY 50" in name:
                        new_indexes["NIFTY"] = instrument_key

            elif segment == "NSE_FO" and instrument_type in {"CE", "PE", "FUT"}:
                underlying = str(item.get("underlying_symbol") or "").strip().upper()
                lot = item.get("lot_size")
                try:
                    lot = int(lot)
                except (TypeError, ValueError):
                    continue

                if underlying and lot > 0:
                    # Only retain a lot size when current contracts agree.
                    previous = new_lots.get(underlying)
                    if previous is None:
                        new_lots[underlying] = lot
                    elif previous != lot:
                        # Different contract generations can temporarily
                        # coexist. Mark ambiguous rather than guess.
                        new_lots[underlying] = None

        new_lots = {
            symbol: lot
            for symbol, lot in new_lots.items()
            if isinstance(lot, int) and lot > 0
        }

        if not new_isin:
            raise ValueError("No NSE equity ISIN records found in instrument master")

        ISIN_MAP.clear()
        ISIN_MAP.update(new_isin)
        INDEX_MAP.clear()
        INDEX_MAP.update(new_indexes)
        LOT_SIZES.clear()
        LOT_SIZES.update(new_lots)
        EQUITY_INSTRUMENT_KEYS.clear()
        EQUITY_INSTRUMENT_KEYS.update(new_equity_keys)

        INSTRUMENT_MASTER_UPDATED_AT = datetime.now(timezone.utc).isoformat()
        INSTRUMENT_MASTER_STATUS = "LOADED"
        logger.info(
            "Upstox instrument master loaded: %d NSE equities, %d stock-F&O lot sizes, %d indices",
            len(ISIN_MAP), len(LOT_SIZES), len(INDEX_MAP),
        )
        return True

    except Exception as exc:
        INSTRUMENT_MASTER_STATUS = "UNAVAILABLE"
        logger.warning("Upstox instrument master unavailable: %s", exc)
        return False


def eq_keys():
    """Return a copy of NSE equity symbol -> Upstox instrument_key mapping."""
    return dict(EQUITY_INSTRUMENT_KEYS)


def get_isin(symbol):
    """Return the current official ISIN, or None if unavailable."""
    return ISIN_MAP.get(str(symbol).strip().upper())


def get_lot_size(symbol):
    """Return current official stock-option lot size, or None if unavailable."""
    return LOT_SIZES.get(str(symbol).strip().upper())


def is_fno_stock(symbol):
    """True only when a current stock-F&O lot size is available."""
    return get_lot_size(symbol) is not None


def get_universe_status():
    """Return diagnostic metadata about the instrument master."""
    return {
        "status": INSTRUMENT_MASTER_STATUS,
        "updated_at": INSTRUMENT_MASTER_UPDATED_AT,
        "equity_symbols_loaded": len(ISIN_MAP),
        "fno_symbols_loaded": len(LOT_SIZES),
        "indices_loaded": len(INDEX_MAP),
        "watchlist_symbols": len(STOCKS_UNIVERSE),
    }


# Load once at module import so existing screener imports receive populated
# maps. If the network is unavailable, the screener safely sees empty maps.
refresh_instrument_maps()
