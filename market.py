"""
Market context utilities for My Stock Screener.

Provides:
- Global market cues via Yahoo Finance (yfinance)
- Sector strength from the supplied daily candle dictionary
- Optional index point-contributor estimates

Unavailable data is omitted or marked unavailable by the caller.
No synthetic market values are generated.
"""

import logging
import math

import pandas as pd
import yfinance as yf


logger = logging.getLogger(__name__)


# Keep this map aligned with the symbols in universe.py.
SECTOR_MAP = {
    # Banking
    "HDFCBANK": "BANK",
    "ICICIBANK": "BANK",
    "SBIN": "BANK",
    "KOTAKBANK": "BANK",
    "AXISBANK": "BANK",
    "INDUSINDBK": "BANK",
    "BANKBARODA": "BANK",
    "PNB": "BANK",
    "CANBK": "BANK",
    "FEDERALBNK": "BANK",
    "IDFCFIRSTB": "BANK",
    "AUBANK": "BANK",

    # Financial services
    "BAJFINANCE": "FINANCE",
    "BAJAJFINSV": "FINANCE",
    "SHRIRAMFIN": "FINANCE",
    "CHOLAFIN": "FINANCE",
    "MUTHOOTFIN": "FINANCE",
    "PFC": "FINANCE",
    "RECLTD": "FINANCE",
    "HDFCLIFE": "FINANCE",
    "SBILIFE": "FINANCE",
    "ICICIPRULI": "FINANCE",
    "ICICIGI": "FINANCE",

    # IT
    "TCS": "IT",
    "INFY": "IT",
    "HCLTECH": "IT",
    "WIPRO": "IT",
    "TECHM": "IT",

    # Pharma / healthcare
    "SUNPHARMA": "PHARMA",
    "CIPLA": "PHARMA",
    "DRREDDY": "PHARMA",
    "DIVISLAB": "PHARMA",
    "APOLLOHOSP": "PHARMA",

    # Auto
    "MARUTI": "AUTO",
    "M&M": "AUTO",
    "EICHERMOT": "AUTO",
    "HEROMOTOCO": "AUTO",

    # Energy and oil
    "RELIANCE": "ENERGY",
    "NTPC": "ENERGY",
    "POWERGRID": "ENERGY",
    "BPCL": "ENERGY",
    "ONGC": "ENERGY",
    "COALINDIA": "ENERGY",

    # Metals
    "TATASTEEL": "METAL",
    "JSWSTEEL": "METAL",
    "HINDALCO": "METAL",

    # FMCG
    "ITC": "FMCG",
    "HINDUNILVR": "FMCG",
    "NESTLEIND": "FMCG",
    "BRITANNIA": "FMCG",

    # Infrastructure and other sectors
    "LT": "INFRA",
    "BHARTIARTL": "TELECOM",
    "ADANIENT": "OTHER",
    "ADANIPORTS": "OTHER",
    "ULTRACEMCO": "OTHER",
    "GRASIM": "OTHER",
    "TITAN": "OTHER",
    "TRENT": "OTHER",
    "BEL": "OTHER",
    "ASIANPAINT": "OTHER",
}


GLOBAL_CUE_TICKERS = {
    "GIFT Nifty": "IN=F",
    "Nasdaq": "^IXIC",
    "Dow Jones": "^DJI",
    "S&P 500": "^GSPC",
    "Nikkei": "^N225",
    "Hang Seng": "^HSI",
    "Crude Oil": "CL=F",
    "US Dollar Idx": "DX-Y.NYB",
    "US 10Y Yield": "^TNX",
    "India VIX": "^INDIAVIX",
}


def _finite_float(value):
    """Convert a value to a finite float, otherwise return None."""
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _extract_ticker_frame(data, ticker):
    """Handle both single-index and yfinance MultiIndex download results."""
    if data is None or getattr(data, "empty", True):
        return None

    try:
        if isinstance(data.columns, pd.MultiIndex):
            # yfinance may return either (Ticker, Price) or (Price, Ticker).
            if ticker in data.columns.get_level_values(0):
                frame = data[ticker].copy()
            elif ticker in data.columns.get_level_values(-1):
                frame = data.xs(ticker, axis=1, level=-1).copy()
            else:
                return None
        else:
            frame = data.copy()

        if "Close" not in frame.columns:
            return None
        return frame.dropna(subset=["Close"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def fetch_global_cues():
    """
    Fetch latest available daily global cue data.

    Returns a list of dictionaries with name, val, chg and raw_val.
    Missing tickers are skipped; an empty list means data was unavailable.
    """
    tickers = list(GLOBAL_CUE_TICKERS.values())

    try:
        data = yf.download(
            tickers=tickers,
            period="5d",
            interval="1d",
            group_by="ticker",
            threads=True,
            progress=False,
            auto_adjust=False,
            timeout=15,
        )
    except Exception as exc:
        logger.warning("Global market cues unavailable: %s", exc)
        return []

    results = []
    for name, ticker in GLOBAL_CUE_TICKERS.items():
        frame = _extract_ticker_frame(data, ticker)
        if frame is None or len(frame) < 2:
            continue

        close = _finite_float(frame["Close"].iloc[-1])
        previous = _finite_float(frame["Close"].iloc[-2])
        if close is None or previous is None or previous == 0:
            continue

        change_pct = ((close / previous) - 1.0) * 100.0
        if not math.isfinite(change_pct):
            continue

        value_text = f"{close:,.1f}" if abs(close) >= 100 else f"{close:.2f}"
        results.append({
            "name": name,
            "val": value_text,
            "chg": f"{change_pct:+.2f}%",
            "raw_val": close,
        })

    return results


def compute_sector_strength(candles):
    """
    Calculate equal-weighted one-session sector moves from supplied candles.

    `candles` must be a dict: symbol -> DataFrame with a Close column.
    Symbols without valid observations are excluded, not filled or estimated.
    """
    if not isinstance(candles, dict):
        return []

    sector_moves = {}

    for symbol, sector in SECTOR_MAP.items():
        frame = candles.get(symbol)
        if frame is None or not isinstance(frame, pd.DataFrame):
            continue
        if "Close" not in frame.columns or len(frame) < 2:
            continue

        current = _finite_float(frame["Close"].iloc[-1])
        previous = _finite_float(frame["Close"].iloc[-2])
        if current is None or previous is None or previous <= 0:
            continue

        change_pct = ((current / previous) - 1.0) * 100.0
        if math.isfinite(change_pct):
            sector_moves.setdefault(sector, []).append(change_pct)

    sector_summary = []
    for sector, moves in sector_moves.items():
        if not moves:
            continue

        average_move = sum(moves) / len(moves)
        advancing = sum(1 for move in moves if move > 0)
        total = len(moves)
        advance_ratio = advancing / total

        if average_move >= 0.5 and advance_ratio >= 0.6:
            reading = "Strong"
        elif average_move <= -0.5 and advance_ratio <= 0.4:
            reading = "Weak"
        else:
            reading = "Mixed"

        sector_summary.append({
            "name": sector,
            "avg_move": f"{average_move:+.2f}%",
            "up_down": f"{advancing}/{total}",
            "read": reading,
            "raw_avg": average_move,
        })

    return sorted(
        sector_summary,
        key=lambda item: item["raw_avg"],
        reverse=True,
    )


def compute_point_contributors(candles, nifty_spot, bnf_spot):
    """
    Estimate point contributors from supplied stock moves and weights.

    This is an approximation based on fixed weights, not an official index
    contribution feed. Returns empty contributor lists when inputs are absent.
    """
    nifty_weights = {
        "HDFCBANK": 11.0,
        "RELIANCE": 9.5,
        "ICICIBANK": 8.0,
        "INFY": 5.5,
        "ITC": 4.0,
        "TCS": 3.8,
        "LT": 3.6,
        "SBIN": 3.2,
        "BHARTIARTL": 3.0,
    }
    banknifty_weights = {
        "HDFCBANK": 29.0,
        "ICICIBANK": 24.0,
        "SBIN": 10.0,
        "KOTAKBANK": 9.0,
        "AXISBANK": 9.0,
    }

    def get_contributors(weights, index_spot):
        gains = []
        losses = []
        spot = _finite_float(index_spot)

        if spot is None or spot <= 0 or not isinstance(candles, dict):
            return {"lifting": [], "dragging": []}

        for symbol, weight in weights.items():
            frame = candles.get(symbol)
            if frame is None or not isinstance(frame, pd.DataFrame):
                continue
            if "Close" not in frame.columns or len(frame) < 2:
                continue

            current = _finite_float(frame["Close"].iloc[-1])
            previous = _finite_float(frame["Close"].iloc[-2])
            if current is None or previous is None or previous <= 0:
                continue

            stock_return = (current / previous) - 1.0
            points = round(stock_return * (weight / 100.0) * spot)

            if points > 0:
                gains.append((symbol, points))
            elif points < 0:
                losses.append((symbol, points))

        return {
            "lifting": sorted(gains, key=lambda item: item[1], reverse=True)[:3],
            "dragging": sorted(losses, key=lambda item: item[1])[:3],
        }

    return {
        "NIFTY": get_contributors(nifty_weights, nifty_spot),
        "BANKNIFTY": get_contributors(banknifty_weights, bnf_spot),
    }
