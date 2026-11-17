
import os
import math
import logging
import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import requests
import pandas as pd
import numpy as np

from config import (
    MODE,
    CAPITAL,
    CAPITAL_CONFIGURED,
    RISK_PCT,
    MAX_ALLOCATION_PCT,
    MAX_DAILY_LOSS_PCT,
    ist_now
)

from universe import (
    STOCKS_UNIVERSE,
    ISIN_MAP,
    LOT_SIZES,
    eq_keys
)

from tech import clean_ohlcv, analyze
from setups import generate_setup
from options_data import chain_upstox
from option_strategy import select_option_strategy

from market import (
    fetch_global_cues,
    compute_sector_strength,
    compute_point_contributors,
    SECTOR_MAP
)

from notify import send_alert


# ============================================================
# CONFIGURATION
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

TOKEN = os.getenv("UPSTOX_TOKEN", "").strip()

BASE_URL = "https://api.upstox.com"

INTRADAY_INTERVAL = "5"
MAX_SIGNALS_PER_SIDE = 5

# Capital intentionally remains undefined.
# No quantity or leverage is assumed.
AUTO_EXECUTION = False

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def now_ist():
    return dt.datetime.now(IST)


def headers():
    if not TOKEN:
        return None

    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {TOKEN}"
    }


def safe_float(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


# ============================================================
# UPSTOX DATA
# ============================================================

def fetch_upstox_candles(symbol, interval="day"):
    """
    Fetch daily historical candles using Upstox V3.
    """

    if not TOKEN:
        return None

    isin = ISIN_MAP.get(symbol)

    if not isin:
        return None

    instrument_key = f"NSE_EQ|{isin}"

    today = now_ist().date()
    from_date = today - dt.timedelta(days=450)

    url = (
        f"{BASE_URL}/v3/historical-candle/"
        f"{quote(instrument_key, safe='')}/"
        f"days/1/{today.isoformat()}/{from_date.isoformat()}"
    )

    try:
        response = requests.get(
            url,
            headers=headers(),
            timeout=15
        )

        if response.status_code != 200:
            return None

        candles = (
            response.json()
            .get("data", {})
            .get("candles", [])
        )

        if not candles:
            return None

        frame = pd.DataFrame(
            candles,
            columns=[
                "Date", "Open", "High", "Low",
                "Close", "Volume", "OI"
            ]
        )

        frame["Date"] = pd.to_datetime(
            frame["Date"], errors="coerce"
        )

        frame = frame.sort_values("Date")
        frame = clean_ohlcv(frame)

        if len(frame) < 200:
            return None

        return frame.reset_index(drop=True)

    except Exception as exc:
        logging.warning(
            "Daily data error %s: %s", symbol, exc
        )
        return None


def fetch_intraday_candles(symbol):
    """
    Fetch current-session 5-minute candles from Upstox V3.
    """

    if not TOKEN:
        return None

    isin = ISIN_MAP.get(symbol)

    if not isin:
        return None

    instrument_key = f"NSE_EQ|{isin}"

    url = (
        f"{BASE_URL}/v3/historical-candle/intraday/"
        f"{quote(instrument_key, safe='')}/"
        f"minutes/{INTRADAY_INTERVAL}"
    )

    try:
        response = requests.get(
            url,
            headers=headers(),
            timeout=12
        )

        if response.status_code != 200:
            return None

        candles = (
            response.json()
            .get("data", {})
            .get("candles", [])
        )

        if not candles:
            return None

        frame = pd.DataFrame(
            candles,
            columns=[
                "Date", "Open", "High", "Low",
                "Close", "Volume", "OI"
            ]
        )

        frame["Date"] = pd.to_datetime(
            frame["Date"], errors="coerce"
        )

        frame = frame.sort_values("Date")
        frame = clean_ohlcv(frame)

        if len(frame) < 25:
            return None

        # Ignore candles from a different trading date.
        today = now_ist().date()

        frame = frame[
            frame["Date"].dt.date == today
        ]

        if len(frame) < 20:
            return None

        return frame.reset_index(drop=True)

    except Exception as exc:
        logging.warning(
            "Intraday data error %s: %s",
            symbol,
            exc
        )
        return None


def fetch_daily_universe():
    result = {}

    with ThreadPoolExecutor(max_workers=8) as executor:
        symbols = list(STOCKS_UNIVERSE)

        for symbol, frame in zip(
            symbols,
            executor.map(fetch_upstox_candles, symbols)
        ):
            if frame is not None:
                result[symbol] = frame

    return result


def fetch_intraday_universe():
    result = {}

    with ThreadPoolExecutor(max_workers=8) as executor:
        symbols = list(STOCKS_UNIVERSE)

        for symbol, frame in zip(
            symbols,
            executor.map(fetch_intraday_candles, symbols)
        ):
            if frame is not None:
                result[symbol] = frame

    return result


# ============================================================
# INTRADAY EQUITY ENGINE
# ============================================================

def intraday_analysis(frame):
    """
    Intraday indicators calculated only from 5-minute candles.
    """

    if frame is None or len(frame) < 25:
        return None

    close = frame["Close"]
    high = frame["High"]
    low = frame["Low"]
    volume = frame["Volume"]

    ema9 = close.ewm(
        span=9, adjust=False
    ).mean()

    ema20 = close.ewm(
        span=20, adjust=False
    ).mean()

    typical = (high + low + close) / 3

    cumulative_volume = volume.cumsum()

    if cumulative_volume.iloc[-1] <= 0:
        return None

    vwap = (
        (typical * volume).cumsum()
        / cumulative_volume
    )

    previous_volume = volume.iloc[-21:-1]

    if len(previous_volume) < 20:
        return None

    average_volume = previous_volume.mean()

    if average_volume <= 0:
        return None

    volume_ratio = (
        volume.iloc[-1] / average_volume
    )

    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ],
        axis=1
    ).max(axis=1)

    atr_value = tr.rolling(14).mean().iloc[-1]

    if pd.isna(atr_value) or atr_value <= 0:
        return None

    delta = close.diff()

    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()

    rs = gain / loss.replace(0, np.nan)

    rsi_series = 100 - (100 / (1 + rs))

    rsi_value = rsi_series.iloc[-1]

    if pd.isna(rsi_value):
        return None

    return {
        "price": float(close.iloc[-1]),
        "ema9": float(ema9.iloc[-1]),
        "ema20": float(ema20.iloc[-1]),
        "vwap": float(vwap.iloc[-1]),
        "rsi": float(rsi_value),
        "atr": float(atr_value),
        "volume_ratio": float(volume_ratio)
    }


def generate_intraday_signal(symbol, frame):
    tech = intraday_analysis(frame)

    if not tech:
        return None

    price = tech["price"]
    ema9 = tech["ema9"]
    ema20 = tech["ema20"]
    vwap = tech["vwap"]
    rsi_value = tech["rsi"]
    atr_value = tech["atr"]
    volume_ratio = tech["volume_ratio"]

    if price <= 0 or atr_value <= 0:
        return None

    if volume_ratio < 1.2:
        return None

    side = None

    if (
        price > ema9 > ema20
        and price > vwap
        and 52 <= rsi_value <= 70
    ):
        side = "BUY"

    elif (
        price < ema9 < ema20
        and price < vwap
        and 30 <= rsi_value <= 48
    ):
        side = "SELL"

    if side is None:
        return None

    risk = 1.5 * atr_value

    if side == "BUY":
        stop = price - risk
        target1 = price + (1.5 * risk)
        target2 = price + (2.5 * risk)
        action = "BUY (LONG)"
    else:
        stop = price + risk
        target1 = price - (1.5 * risk)
        target2 = price - (2.5 * risk)
        action = "SELL (SHORT)"

    return {
        "symbol": symbol,
        "action": action,
        "sector": SECTOR_MAP.get(symbol, "OTHER"),
        "index_tag": "NSE",
        "entry": round(price, 2),
        "stop_loss": round(stop, 2),
        "target_1": round(target1, 2),
        "target_2": round(target2, 2),
        "rsi": round(rsi_value, 1),
        "volume_ratio": round(volume_ratio, 2),
        "vwap": round(vwap, 2),
        "quantity": None,
        "leverage": "NOT ASSUMED",
        "capital_configured": False,
        "execution": "SIGNAL_ONLY"
    }


def scan_intraday(intraday_data):
    bullish = []
    bearish = []

    for symbol, frame in intraday_data.items():
        try:
            signal = generate_intraday_signal(
                symbol, frame
            )

            if not signal:
                continue

            if signal["action"] == "BUY (LONG)":
                bullish.append(signal)
            else:
                bearish.append(signal)

        except Exception as exc:
            logging.warning(
                "Intraday scan error %s: %s",
                symbol,
                exc
            )

    bullish.sort(
        key=lambda x: (
            x["volume_ratio"],
            x["rsi"]
        ),
        reverse=True
    )

    bearish.sort(
        key=lambda x: (
            x["volume_ratio"],
            -x["rsi"]
        ),
        reverse=True
    )

    return (
        bullish[:MAX_SIGNALS_PER_SIDE],
        bearish[:MAX_SIGNALS_PER_SIDE]
    )


# ============================================================
# BTST STOCK OPTION ENGINE
# ============================================================

def make_option_candidate(symbol, frame, setup):
    """
    Select real option strategy using live Upstox option data.
    """

    if not setup:
        return None

    try:
        chain_result = chain_upstox(symbol)

        if not chain_result:
            return None

        option_plan = select_option_strategy(
            chain_result,
            setup["signal"]
        )

        if not option_plan:
            return None

        tech = analyze(frame)

        if not tech:
            return None

        price = float(setup["entry"])

        signal = setup["signal"]

        side = (
            "CALL BUY / SPREAD"
            if signal == "BUY"
            else "PUT BUY / SPREAD"
        )

        strategy_name = option_plan.get(
            "strategy", "UNKNOWN"
        )

        reasons = [
            f"Daily signal: {signal}",
            f"RSI: {setup['rsi']}",
            f"ADX: {setup['adx']}",
            f"Volume ratio: {setup['volume_ratio']}",
            f"Option strategy: {strategy_name}",
            f"Expiry: {option_plan.get('expiry')}",
            f"Capital configured: {CAPITAL_CONFIGURED}",
            "Quantity not calculated because capital is undefined",
            "Signal only. No order placement."
        ]

        if strategy_name == "OPTION_BUYING":
            entry = option_plan["entry_premium"]
            max_loss = option_plan["maximum_loss_per_unit"]
            target1 = entry * 1.5
            target2 = entry * 2.0
        else:
            entry = option_plan["net_debit_per_unit"]
            max_loss = option_plan["maximum_loss_per_unit"]
            target1 = entry + (
                option_plan["maximum_profit_per_unit"] * 0.5
            )
            target2 = (
                entry + option_plan["maximum_profit_per_unit"]
            )

        if entry <= 0 or max_loss <= 0:
            return None

        return {
            "symbol": symbol,
            "side": side,
            "setup_kind": "BTST STOCK OPTIONS",
            "grade": "A",
            "sector": SECTOR_MAP.get(symbol, "OTHER"),
            "close": round(price, 2),
            "change_pct": 0.0,
            "rsi": setup["rsi"],
            "vol_ratio": setup["volume_ratio"],
            "atr_pct": setup["atr_pct"],
            "entry": round(entry, 2),
            "stop_loss": round(entry * 0.70, 2),
            "target_1": round(target1, 2),
            "target_2": round(target2, 2),
            "rr": 1.5,
            "qty": 0,
            "capital_needed": None,
            "risk_amount": None,
            "checks": {
                "trend": True,
                "level": False,
                "volume": True,
                "options": True,
                "sector": False,
                "rs": False
            },
            "reasons": reasons,
            "opt_chain": {
                "expiry": option_plan.get("expiry"),
                "strategy": strategy_name,
                "maximum_loss_per_unit": max_loss
            },
            "strike": None,
            "hedge_plan": None,
            "option_plan": option_plan,
            "execution": "SIGNAL_ONLY"
        }

    except Exception as exc:
        logging.warning(
            "Option analysis failed for %s: %s",
            symbol,
            exc
        )
        return None


def scan_stock_options(daily_data):
    bullish = []
    bearish = []

    for symbol, frame in daily_data.items():

        # Only symbols with an official lot-size entry
        # are considered for the stock-option universe.
        if symbol not in LOT_SIZES:
            continue

        try:
            setup = generate_setup(frame)

            if not setup:
                continue

            candidate = make_option_candidate(
                symbol,
                frame,
                setup
            )

            if not candidate:
                continue

            if setup["signal"] == "BUY":
                bullish.append(candidate)
            else:
                bearish.append(candidate)

        except Exception as exc:
            logging.warning(
                "BTST option scan error %s: %s",
                symbol,
                exc
            )

    bullish.sort(
        key=lambda x: (
            x["grade"],
            x["rsi"],
            x["vol_ratio"]
        ),
        reverse=True
    )

    bearish.sort(
        key=lambda x: (
            x["grade"],
            -x["rsi"],
            x["vol_ratio"]
        ),
        reverse=True
    )

    return (
        bullish[:MAX_SIGNALS_PER_SIDE],
        bearish[:MAX_SIGNALS_PER_SIDE]
    )


# ============================================================
# MARKET SUMMARY
# ============================================================

def market_summary(daily_data):
    advances = 0
    declines = 0
    above20 = 0
    above50 = 0
    count = 0

    for symbol, frame in daily_data.items():
        if len(frame) < 200:
            continue

        count += 1

        current = float(frame["Close"].iloc[-1])
        previous = float(frame["Close"].iloc[-2])

        if previous > 0:
            change = (current / previous - 1) * 100

            if change > 0:
                advances += 1
            elif change < 0:
                declines += 1

        tech = analyze(frame)

        if not tech:
            continue

        if tech["price"] > tech["ema20"]:
            above20 += 1

        if tech["price"] > tech["ema50"]:
            above50 += 1

    breadth = (
        "BULLISH"
        if advances > declines
        else "BEARISH"
        if declines > advances
        else "MIXED"
    )

    return {
        "data_source": "UPSTOX V3",
        "breadth_ratio": (
            f"{advances} Adv / {declines} Dec"
        ),
        "above_ema20": (
            round(above20 / count * 100)
            if count else 0
        ),
        "above_ema50": (
            round(above50 / count * 100)
            if count else 0
        ),
        "breadth_tone": breadth,
        "ideas_count": 0,
        "best_rated": "None",
        "nifty_spot": "Unavailable",
        "nifty_chg": "Unavailable",
        "bnf_spot": "Unavailable",
        "bnf_chg": "Unavailable",
        "fin_spot": "Unavailable",
        "fin_chg": "Unavailable",
        "vix_alert": None
    }


# ============================================================
# MAIN SCREENER
# ============================================================

def run_screener():

    logging.info("Starting Upstox Conservative Screener")

    if not TOKEN:
        logging.error(
            "UPSTOX_TOKEN missing. No market signals generated."
        )

        return None

    daily_data = fetch_daily_universe()

    if not daily_data:
        logging.error(
            "No valid daily candles. Screener stopped."
        )
        return None

    intraday_data = fetch_intraday_universe()

    logging.info(
        "Daily instruments: %s | Intraday instruments: %s",
        len(daily_data),
        len(intraday_data)
    )

    # Intraday equity
    intraday_bull, intraday_bear = scan_intraday(
        intraday_data
    )

    equity_intraday = (
        intraday_bull + intraday_bear
    )

    # BTST stock options
    option_bull, option_bear = scan_stock_options(
        daily_data
    )

    summary = market_summary(daily_data)

    total_ideas = (
        len(intraday_bull)
        + len(intraday_bear)
        + len(option_bull)
        + len(option_bear)
    )

    summary["ideas_count"] = total_ideas

    if option_bull:
        summary["best_rated"] = (
            option_bull[0]["symbol"]
        )

    try:
        global_cues = fetch_global_cues() or []
    except Exception:
        global_cues = []

    try:
        sectors = compute_sector_strength(
            daily_data
        ) or []
    except Exception:
        sectors = []

    payload = {
        "mode": "INTRADAY + BTST",
        "summary": summary,
        "global_cues": global_cues,
        "index_view": {},
        "index_trades": [],
        "sectors": sectors,

        "setups": {
            "bullish": option_bull,
            "bearish": option_bear
        },

        "equity_intraday": equity_intraday,

        "intraday_bullish": intraday_bull,
        "intraday_bearish": intraday_bear,

        "options_bullish": option_bull,
        "options_bearish": option_bear,

        "capital": None,
        "capital_configured": False,

        "risk_pct": RISK_PCT,
        "max_allocation_pct": MAX_ALLOCATION_PCT,
        "max_daily_loss_pct": MAX_DAILY_LOSS_PCT,

        "execution": "SIGNAL_ONLY",
        "automatic_orders": False,

        "intraday_status": (
            "UPSTOX V3 5-MINUTE DATA"
            if intraday_data
            else "NO INTRADAY DATA"
        ),

        "option_strategy": (
            "OPTION BUYING OR HEDGED DEBIT SPREAD"
        )
    }

    try:
        send_alert(payload)
    except Exception as exc:
        logging.error(
            "Report delivery failed: %s", exc
        )

    logging.info(
        "Completed: Intraday %s BUY / %s SELL | "
        "Options %s Bullish / %s Bearish",
        len(intraday_bull),
        len(intraday_bear),
        len(option_bull),
        len(option_bear)
    )

    return payload


if __name__ == "__main__":
    run_screener()
