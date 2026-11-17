
import os
import logging
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import yfinance as yf

from config import (
    MODE,
    CAPITAL,
    CAPITAL_CONFIGURED,
    RISK_PCT,
    MAX_ALLOCATION_PCT
)

from universe import STOCKS_UNIVERSE
from tech import clean_ohlcv, analyze
from setups import generate_setup
from market import (
    fetch_global_cues,
    compute_sector_strength,
    compute_point_contributors,
    SECTOR_MAP
)
from notify import send_alert


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

UPSTOX_TOKEN = os.getenv("UPSTOX_TOKEN", "").strip()


def fetch_yahoo_data():
    """
    Fetch one year of daily OHLCV data.
    Daily data is used for Swing / BTST only.
    """

    tickers = {
        "NIFTY": "^NSEI",
        "BANKNIFTY": "^NSEBANK",
        "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
        "SENSEX": "^BSESN"
    }

    for symbol in STOCKS_UNIVERSE:
        tickers[symbol] = f"{symbol}.NS"

    try:
        raw = yf.download(
            list(tickers.values()),
            period="1y",
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            threads=True,
            progress=False,
            timeout=30
        )
    except Exception as exc:
        logging.error("Yahoo download failed: %s", exc)
        return {}

    result = {}
    multi = isinstance(raw.columns, pd.MultiIndex)

    for symbol, ticker in tickers.items():
        try:
            if multi:
                if ticker not in raw.columns.get_level_values(0):
                    continue
                frame = raw[ticker].copy()
            else:
                frame = raw.copy()

            if frame.empty:
                continue

            frame = frame.dropna(how="all")
            frame = frame.reset_index()

            rename = {}
            for col in frame.columns:
                name = str(col).strip().lower()

                if name == "date" or name == "datetime":
                    rename[col] = "Date"
                elif name == "open":
                    rename[col] = "Open"
                elif name == "high":
                    rename[col] = "High"
                elif name == "low":
                    rename[col] = "Low"
                elif name == "close":
                    rename[col] = "Close"
                elif name == "volume":
                    rename[col] = "Volume"

            frame = frame.rename(columns=rename)

            cleaned = clean_ohlcv(frame)

            if len(cleaned) >= 200:
                result[symbol] = cleaned

        except Exception as exc:
            logging.warning(
                "Could not process %s: %s", symbol, exc
            )

    return result


def fetch_upstox_data():
    """
    Fetch daily historical candles from Upstox.
    Returns an empty dictionary if token/data is unavailable.
    """

    if not UPSTOX_TOKEN:
        return {}

    try:
        from universe import eq_keys
        import requests

        key_map = eq_keys()
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {UPSTOX_TOKEN}"
        }

        today = dt.date.today()
        start = today - dt.timedelta(days=420)

        def fetch_one(item):
            symbol, instrument_key = item

            url = (
                "https://api.upstox.com/v2/historical-candle/"
                f"{requests.utils.quote(instrument_key, safe='')}"
                f"/day/{today.isoformat()}/{start.isoformat()}"
            )

            try:
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=15
                )

                if response.status_code != 200:
                    return symbol, None

                candles = (
                    response.json()
                    .get("data", {})
                    .get("candles", [])
                )

                if not candles:
                    return symbol, None

                frame = pd.DataFrame(
                    candles,
                    columns=[
                        "Date", "Open", "High", "Low",
                        "Close", "Volume", "OI"
                    ]
                )

                frame = frame.iloc[::-1].reset_index(drop=True)
                frame = clean_ohlcv(frame)

                if len(frame) < 200:
                    return symbol, None

                return symbol, frame

            except Exception:
                return symbol, None

        output = {}

        with ThreadPoolExecutor(max_workers=8) as executor:
            for symbol, frame in executor.map(
                fetch_one, key_map.items()
            ):
                if frame is not None:
                    output[symbol] = frame

        return output

    except Exception as exc:
        logging.warning("Upstox fetch failed: %s", exc)
        return {}


def batch_fetch_candles():
    """
    Prefer Upstox historical data.
    Yahoo Finance is used only as a data-source fallback.
    """

    upstox = fetch_upstox_data()

    if len(upstox) >= 10:
        logging.info(
            "Using Upstox daily historical data: %s instruments",
            len(upstox)
        )
        return upstox, "Upstox Historical Daily"

    logging.warning(
        "Upstox data unavailable or incomplete. "
        "Trying Yahoo Finance daily data."
    )

    yahoo = fetch_yahoo_data()

    if yahoo:
        return yahoo, "Yahoo Finance Daily"

    return {}, "NO DATA"


def safe_change(frame):
    if frame is None or len(frame) < 2:
        return None

    previous = float(frame["Close"].iloc[-2])
    current = float(frame["Close"].iloc[-1])

    if previous <= 0:
        return None

    return round((current / previous - 1) * 100, 2)


def safe_price(frame):
    if frame is None or frame.empty:
        return None

    value = float(frame["Close"].iloc[-1])

    if value <= 0:
        return None

    return round(value, 2)


def make_candidate(symbol, frame):
    """
    Convert a valid daily Swing setup into report format.
    No option premium or fake option-chain data is generated.
    """

    setup = generate_setup(frame)

    if not setup:
        return None

    tech = analyze(frame)

    if not tech:
        return None

    price = safe_price(frame)

    if price is None:
        return None

    signal = setup["signal"]

    sector = SECTOR_MAP.get(symbol, "OTHER")

    checks = {
        "trend": (
            price > tech["ema20"] > tech["ema50"] > tech["ema200"]
            if signal == "BUY"
            else price < tech["ema20"] < tech["ema50"] < tech["ema200"]
        ),
        "rsi": (
            50 <= tech["rsi"] <= 72
            if signal == "BUY"
            else 28 <= tech["rsi"] <= 50
        ),
        "adx": tech["adx"] >= 18,
        "volume": (
            tech["vr"] is not None and tech["vr"] >= 1.2
        ),
        "capital": CAPITAL_CONFIGURED
    }

    passed = sum(bool(value) for value in checks.values())

    grade = "A" if passed >= 4 else "B"

    reasons = [
        f"Daily trend: {signal}",
        f"RSI: {tech['rsi']:.1f}",
        f"ADX: {tech['adx']:.1f}",
        f"EMA20: {tech['ema20']:.2f}",
        f"EMA50: {tech['ema50']:.2f}",
        f"EMA200: {tech['ema200']:.2f}",
        f"Volume ratio: {tech['vr']}"
    ]

    if not CAPITAL_CONFIGURED:
        reasons.append(
            "Capital not configured. Quantity is zero."
        )

    return {
        "symbol": symbol,
        "side": "BUY" if signal == "BUY" else "SELL",
        "setup_kind": "Swing / BTST Daily Setup",
        "grade": grade,
        "sector": sector,
        "close": price,
        "change_pct": safe_change(frame),
        "rsi": setup["rsi"],
        "vol_ratio": setup["volume_ratio"],
        "atr_pct": setup["atr_pct"],
        "entry": setup["entry"],
        "stop_loss": setup["stop_loss"],
        "target_1": setup["target_1"],
        "target_2": setup["target_2"],
        "rr": setup["rr_ratio"],
        "qty": setup["qty"],
        "capital_needed": setup["capital_needed"],
        "risk_amount": setup["risk_amount"],
        "sizing_status": setup["sizing_status"],
        "trade_execution": "SIGNAL_ONLY",
        "checks": checks,
        "reasons": reasons,
        "opt_chain": None,
        "strike": None,
        "hedge_plan": None
    }


def run_screener():
    logging.info("Starting conservative %s screener", MODE)

    candles, data_source = batch_fetch_candles()

    if not candles:
        logging.error("No valid market data. No signals generated.")

        payload = {
            "mode": MODE,
            "summary": {
                "data_source": data_source,
                "ideas_count": 0,
                "best_rated": "None",
                "breadth_tone": "UNKNOWN",
                "breadth_ratio": "Unavailable",
                "above_ema20": 0,
                "above_ema50": 0,
                "nifty_spot": "Unavailable",
                "nifty_chg": "Unavailable",
                "nifty_rsi": "Unavailable",
                "bnf_spot": "Unavailable",
                "bnf_chg": "Unavailable",
                "fin_spot": "Unavailable",
                "fin_chg": "Unavailable",
                "vix_alert": "No valid market data"
            },
            "global_cues": [],
            "index_view": {},
            "index_trades": [],
            "sectors": [],
            "setups": {
                "bullish": [],
                "bearish": []
            },
            "equity_intraday": [],
            "execution": "SIGNAL_ONLY"
        }

        send_alert(payload)
        return payload

    # Market index summary
    nifty = candles.get("NIFTY")
    bank = candles.get("BANKNIFTY")
    fin = candles.get("FINNIFTY")

    nifty_price = safe_price(nifty)
    bank_price = safe_price(bank)
    fin_price = safe_price(fin)

    nifty_tech = analyze(nifty) if nifty is not None else {}
    bank_tech = analyze(bank) if bank is not None else {}

    # Breadth
    advances = 0
    declines = 0
    above20 = 0
    above50 = 0
    scanned = 0

    for symbol in STOCKS_UNIVERSE:
        frame = candles.get(symbol)

        if frame is None or len(frame) < 200:
            continue

        scanned += 1

        change = safe_change(frame)

        if change is not None and change > 0:
            advances += 1
        elif change is not None and change < 0:
            declines += 1

        tech = analyze(frame)

        if tech:
            price = tech["price"]

            if price > tech["ema20"]:
                above20 += 1

            if price > tech["ema50"]:
                above50 += 1

    if advances > declines * 1.25:
        breadth = "BULLISH"
    elif declines > advances * 1.25:
        breadth = "BEARISH"
    else:
        breadth = "MIXED"

    # Stock setup scan
    bullish = []
    bearish = []

    for symbol in STOCKS_UNIVERSE:
        frame = candles.get(symbol)

        if frame is None or len(frame) < 200:
            continue

        try:
            candidate = make_candidate(symbol, frame)

            if candidate is None:
                continue

            if candidate["side"] == "BUY":
                bullish.append(candidate)
            else:
                bearish.append(candidate)

        except Exception as exc:
            logging.warning(
                "Setup failed for %s: %s", symbol, exc
            )

    bullish.sort(
        key=lambda item: (
            item["grade"],
            item["rsi"]
        ),
        reverse=True
    )

    bearish.sort(
        key=lambda item: (
            item["grade"],
            -item["rsi"]
        ),
        reverse=True
    )

    bullish = bullish[:10]
    bearish = bearish[:10]

    # Global and sector context
    try:
        global_cues = fetch_global_cues() or []
    except Exception:
        global_cues = []

    try:
        sectors = compute_sector_strength(candles) or []
    except Exception:
        sectors = []

    contributors = {}

    if nifty_price is not None and bank_price is not None:
        try:
            contributors = compute_point_contributors(
                candles,
                nifty_price,
                bank_price
            ) or {}
        except Exception:
            contributors = {}

    def index_info(name, frame, tech):
        if frame is None or tech is None:
            return None

        price = safe_price(frame)
        change = safe_change(frame)

        if price is None:
            return None

        return {
            "spot": f"{price:,.2f}",
            "chg": (
                f"{change:+.2f}%"
                if change is not None else "Unavailable"
            ),
            "rsi": round(tech.get("rsi", 0), 1),
            "lifting": contributors.get(
                name, {}
            ).get("lifting", []),
            "dragging": contributors.get(
                name, {}
            ).get("dragging", [])
        }

    index_view = {}

    info = index_info("NIFTY", nifty, nifty_tech)
    if info:
        index_view["NIFTY"] = info

    info = index_info("BANKNIFTY", bank, bank_tech)
    if info:
        index_view["BANKNIFTY"] = info

    # No intraday signals from daily candles.
    # No synthetic options or invented option-chain values.
    index_trades = []
    equity_intraday = []

    total_ideas = len(bullish) + len(bearish)

    best_rated = (
        f"{bullish[0]['symbol']} (Swing)"
        if bullish else "None"
    )

    nifty_change = safe_change(nifty)
    bank_change = safe_change(bank)
    fin_change = safe_change(fin)

    summary = {
        "data_source": data_source,
        "nifty_spot": (
            f"{nifty_price:,.2f}"
            if nifty_price is not None else "Unavailable"
        ),
        "nifty_chg": (
            f"{nifty_change:+.2f}%"
            if nifty_change is not None else "Unavailable"
        ),
        "nifty_rsi": (
            round(nifty_tech["rsi"], 1)
            if nifty_tech else "Unavailable"
        ),
        "bnf_spot": (
            f"{bank_price:,.2f}"
            if bank_price is not None else "Unavailable"
        ),
        "bnf_chg": (
            f"{bank_change:+.2f}%"
            if bank_change is not None else "Unavailable"
        ),
        "fin_spot": (
            f"{fin_price:,.2f}"
            if fin_price is not None else "Unavailable"
        ),
        "fin_chg": (
            f"{fin_change:+.2f}%"
            if fin_change is not None else "Unavailable"
        ),
        "breadth_ratio": f"{advances} Adv / {declines} Dec",
        "above_ema20": (
            round(above20 / scanned * 100)
            if scanned else 0
        ),
        "above_ema50": (
            round(above50 / scanned * 100)
            if scanned else 0
        ),
        "breadth_tone": breadth,
        "ideas_count": total_ideas,
        "best_rated": best_rated,
        "vix_alert": None
    }

    payload = {
        "mode": MODE,
        "summary": summary,
        "global_cues": global_cues,
        "index_view": index_view,
        "index_trades": index_trades,
        "sectors": sectors,
        "setups": {
            "bullish": bullish,
            "bearish": bearish
        },
        "equity_intraday": equity_intraday,
        "execution": "SIGNAL_ONLY",
        "capital_configured": CAPITAL_CONFIGURED,
        "capital": CAPITAL,
        "risk_pct": RISK_PCT,
        "max_allocation_pct": MAX_ALLOCATION_PCT,
        "intraday_status": (
            "NOT_GENERATED: intraday candle feed required"
        )
    }

    try:
        send_alert(payload)
    except Exception as exc:
        logging.error("Alert delivery failed: %s", exc)

    logging.info(
        "Completed. %s bullish and %s bearish setups.",
        len(bullish),
        len(bearish)
    )

    return payload


if __name__ == "__main__":
    run_screener()
