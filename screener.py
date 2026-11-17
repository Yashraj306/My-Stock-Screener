"""
My Stock Screener - main runner.

Modes:
- MORNING: intraday equity scan + report
- BTST: BTST stock-options scan + report
- SWING: both engines (manual/default compatibility mode)

Signals only. This file never places orders.
Missing/invalid market data is rejected; no synthetic prices or option metrics.
"""

import datetime as dt
import logging
import math
import os
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import pandas as pd
import requests

from config import (
    MODE,
    CAPITAL,
    CAPITAL_CONFIGURED,
    RISK_PCT,
    MAX_ALLOCATION_PCT,
    MAX_DAILY_LOSS_PCT,
    MAX_INTRADAY_BUY,
    MAX_INTRADAY_SELL,
    MAX_BTST_BULLISH,
    MAX_BTST_BEARISH,
)
from universe import STOCKS_UNIVERSE, ISIN_MAP, LOT_SIZES
from tech import clean_ohlcv, analyze
from setups import generate_setup
from options_data import chain_upstox
from option_strategy import select_option_strategy
from market import fetch_global_cues, compute_sector_strength, SECTOR_MAP
from notify import send_alert


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

TOKEN = os.getenv("UPSTOX_TOKEN", "").strip()
BASE_URL = "https://api.upstox.com"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
AUTO_EXECUTION = False


def now_ist():
    return dt.datetime.now(IST)


def api_headers():
    if not TOKEN:
        return None
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {TOKEN}",
    }


def _safe_number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _candles_to_frame(candles):
    if not candles:
        return None
    try:
        frame = pd.DataFrame(
            candles,
            columns=[
                "Date", "Open", "High", "Low",
                "Close", "Volume", "OI",
            ],
        )
        frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
        frame = frame.sort_values("Date")
        frame = clean_ohlcv(frame)
        return frame.reset_index(drop=True) if frame is not None and not frame.empty else None
    except Exception:
        return None


def fetch_daily_candles(symbol):
    """Fetch 450 calendar days of daily candles from Upstox V3."""
    if not TOKEN:
        return None
    isin = ISIN_MAP.get(symbol)
    if not isin:
        return None

    instrument_key = f"NSE_EQ|{isin}"
    today = now_ist().date()
    start = today - dt.timedelta(days=450)
    url = (
        f"{BASE_URL}/v3/historical-candle/"
        f"{quote(instrument_key, safe='')}/days/1/"
        f"{today.isoformat()}/{start.isoformat()}"
    )
    try:
        response = requests.get(url, headers=api_headers(), timeout=20)
        if response.status_code != 200:
            logging.warning("Daily API status %s for %s", response.status_code, symbol)
            return None
        candles = response.json().get("data", {}).get("candles", [])
        frame = _candles_to_frame(candles)
        if frame is None or len(frame) < 200:
            return None
        return frame
    except Exception as exc:
        logging.warning("Daily data error %s: %s", symbol, exc)
        return None


def fetch_intraday_candles(symbol):
    """Fetch current-session 5-minute candles from Upstox V3."""
    if not TOKEN:
        return None
    isin = ISIN_MAP.get(symbol)
    if not isin:
        return None

    instrument_key = f"NSE_EQ|{isin}"
    url = (
        f"{BASE_URL}/v3/historical-candle/intraday/"
        f"{quote(instrument_key, safe='')}/minutes/5"
    )
    try:
        response = requests.get(url, headers=api_headers(), timeout=20)
        if response.status_code != 200:
            logging.warning("Intraday API status %s for %s", response.status_code, symbol)
            return None
        candles = response.json().get("data", {}).get("candles", [])
        frame = _candles_to_frame(candles)
        if frame is None:
            return None
        frame = frame[frame["Date"].dt.date == now_ist().date()]
        if len(frame) < 25:
            return None
        return frame.reset_index(drop=True)
    except Exception as exc:
        logging.warning("Intraday data error %s: %s", symbol, exc)
        return None


def fetch_universe(fetcher):
    symbols = list(STOCKS_UNIVERSE)
    results = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for symbol, frame in zip(symbols, pool.map(fetcher, symbols)):
            if frame is not None:
                results[symbol] = frame
    return results


def intraday_indicators(frame):
    if frame is None or len(frame) < 25:
        return None

    close = frame["Close"]
    high = frame["High"]
    low = frame["Low"]
    volume = frame["Volume"]

    ema9 = close.ewm(span=9, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    typical = (high + low + close) / 3
    cumulative_volume = volume.cumsum()
    if cumulative_volume.iloc[-1] <= 0:
        return None

    vwap = (typical * volume).cumsum() / cumulative_volume
    previous_volume = volume.iloc[-21:-1]
    if len(previous_volume) < 20:
        return None
    average_volume = previous_volume.mean()
    if pd.isna(average_volume) or average_volume <= 0:
        return None

    volume_ratio = volume.iloc[-1] / average_volume
    true_range = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr_value = true_range.rolling(14).mean().iloc[-1]

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi_value = (100 - 100 / (1 + rs)).iloc[-1]

    values = [
        close.iloc[-1], ema9.iloc[-1], ema20.iloc[-1],
        vwap.iloc[-1], atr_value, rsi_value, volume_ratio,
    ]
    if any(pd.isna(value) for value in values):
        return None
    if float(atr_value) <= 0 or float(volume_ratio) < 1.2:
        return None

    return {
        "price": float(close.iloc[-1]),
        "ema9": float(ema9.iloc[-1]),
        "ema20": float(ema20.iloc[-1]),
        "vwap": float(vwap.iloc[-1]),
        "atr": float(atr_value),
        "rsi": float(rsi_value),
        "volume_ratio": float(volume_ratio),
    }


def make_intraday_signal(symbol, frame):
    tech = intraday_indicators(frame)
    if not tech:
        return None

    price = tech["price"]
    side = None
    if (
        price > tech["ema9"] > tech["ema20"]
        and price > tech["vwap"]
        and 52 <= tech["rsi"] <= 70
    ):
        side = "BUY"
    elif (
        price < tech["ema9"] < tech["ema20"]
        and price < tech["vwap"]
        and 30 <= tech["rsi"] <= 48
    ):
        side = "SELL"

    if side is None:
        return None

    risk = 1.5 * tech["atr"]
    if side == "BUY":
        stop = price - risk
        target1 = price + 1.5 * risk
        target2 = price + 2.5 * risk
        action = "BUY (LONG)"
    else:
        stop = price + risk
        target1 = price - 1.5 * risk
        target2 = price - 2.5 * risk
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
        "rsi": round(tech["rsi"], 1),
        "volume_ratio": round(tech["volume_ratio"], 2),
        "vwap": round(tech["vwap"], 2),
        "quantity": None,
        "leverage": "NOT ASSUMED",
        "capital_configured": CAPITAL_CONFIGURED,
        "execution": "SIGNAL_ONLY",
    }


def scan_intraday(intraday_data):
    bullish, bearish = [], []
    for symbol, frame in intraday_data.items():
        try:
            signal = make_intraday_signal(symbol, frame)
            if not signal:
                continue
            if signal["action"] == "BUY (LONG)":
                bullish.append(signal)
            else:
                bearish.append(signal)

        except Exception as exc:
            logging.warning("Intraday scan error %s: %s", symbol, exc)

    bullish.sort(key=lambda item: (item["volume_ratio"], item["rsi"]), reverse=True)
    bearish.sort(key=lambda item: (item["volume_ratio"], -item["rsi"]), reverse=True)
    return bullish[:MAX_INTRADAY_BUY], bearish[:MAX_INTRADAY_SELL]


def make_option_candidate(symbol, frame, setup):
    if not setup:
        return None
    try:
        chain_result = chain_upstox(symbol)
        if not chain_result:
            return None

        option_plan = select_option_strategy(chain_result, setup["signal"])
        if not option_plan:
            return None

        strategy = option_plan.get("strategy")
        if strategy == "OPTION_BUYING":
            entry = _safe_number(option_plan.get("entry_premium"))
            max_loss = _safe_number(option_plan.get("maximum_loss_per_unit"))
            if not entry or not max_loss:
                return None
            target1, target2 = entry * 1.5, entry * 2.0
        else:
            entry = _safe_number(option_plan.get("net_debit_per_unit"))
            max_loss = _safe_number(option_plan.get("maximum_loss_per_unit"))
            max_profit = _safe_number(option_plan.get("maximum_profit_per_unit"))
            if not entry or not max_loss or not max_profit:
                return None
            target1 = entry + max_profit * 0.5
            target2 = entry + max_profit

        if entry <= 0 or max_loss <= 0:
            return None

        side = "CALL BUY / SPREAD" if setup["signal"] == "BUY" else "PUT BUY / SPREAD"
        return {
            "symbol": symbol,
            "side": side,
            "setup_kind": "BTST STOCK OPTIONS",
            "grade": "A",
            "sector": SECTOR_MAP.get(symbol, "OTHER"),
            "close": round(float(setup["entry"]), 2),
            "change_pct": None,
            "rsi": setup.get("rsi"),
            "vol_ratio": setup.get("volume_ratio"),
            "atr_pct": setup.get("atr_pct"),
            "entry": round(entry, 2),
            "stop_loss": round(entry * 0.70, 2),
            "target_1": round(target1, 2),
            "target_2": round(target2, 2),
            "rr": None,
            "qty": None,
            "capital_needed": None,
            "risk_amount": None,
            "checks": {
                "trend": True, "level": None, "volume": True,
                "options": True, "sector": None, "rs": None,
            },
            "reasons": [
                f"Daily signal: {setup['signal']}",
                f"Option strategy: {strategy}",
                f"Expiry: {option_plan.get('expiry')}",
                "Option metrics must come from live chain data",
                "Quantity not calculated; capital is not configured",
                "Signal only; no order placement",
            ],
            "opt_chain": {
                "expiry": option_plan.get("expiry"),
                "strategy": strategy,
                "maximum_loss_per_unit": max_loss,
            },
            "strike": option_plan.get("strike"),
            "hedge_plan": option_plan.get("hedge_plan"),
            "option_plan": option_plan,
            "execution": "SIGNAL_ONLY",
        }
    except Exception as exc:
        logging.warning("Option analysis failed for %s: %s", symbol, exc)
        return None


def scan_stock_options(daily_data):
    bullish, bearish = [], []
    for symbol, frame in daily_data.items():
        if symbol not in LOT_SIZES:
            continue
        try:
            setup = generate_setup(frame)
            if not setup:
                continue
            candidate = make_option_candidate(symbol, frame, setup)
            if not candidate:
                continue
            if setup.get("signal") == "BUY":
                bullish.append(candidate)
            elif setup.get("signal") == "SELL":
                bearish.append(candidate)
        except Exception as exc:
            logging.warning("BTST option scan error %s: %s", symbol, exc)

    bullish.sort(
        key=lambda item: (
            _safe_number(item.get("rsi")) or 0,
            _safe_number(item.get("vol_ratio")) or 0,
        ),
        reverse=True,
    )
    bearish.sort(
        key=lambda item: (
            -(_safe_number(item.get("rsi")) or 100),
            _safe_number(item.get("vol_ratio")) or 0,
        ),
        reverse=True,
    )
    return bullish[:MAX_BTST_BULLISH], bearish[:MAX_BTST_BEARISH]


def market_summary(daily_data):
    advances = declines = above20 = above50 = count = 0
    for _, frame in daily_data.items():
        if frame is None or len(frame) < 200:
            continue
        count += 1
        current = _safe_number(frame["Close"].iloc[-1])
        previous = _safe_number(frame["Close"].iloc[-2])
        if current is not None and previous and previous > 0:
            change = (current / previous - 1) * 100
            if change > 0:
                advances += 1
            elif change < 0:
                declines += 1

        indicators = analyze(frame)
        if not indicators:
            continue
        if indicators["price"] > indicators["ema20"]:
            above20 += 1
        if indicators["price"] > indicators["ema50"]:
            above50 += 1

    breadth = "BULLISH" if advances > declines else "BEARISH" if declines > advances else "MIXED"
    return {
        "data_source": "UPSTOX V3",
        "breadth_ratio": f"{advances} Adv / {declines} Dec",
        "above_ema20": round(above20 / count * 100) if count else None,
        "above_ema50": round(above50 / count * 100) if count else None,
        "breadth_tone": breadth,
        "ideas_count": 0,
        "best_rated": "None",
        "nifty_spot": "Unavailable",
        "nifty_chg": "Unavailable",
        "bnf_spot": "Unavailable",
        "bnf_chg": "Unavailable",
        "fin_spot": "Unavailable",
        "fin_chg": "Unavailable",
        "vix_alert": None,
    }


def run_screener():
    logging.info("Starting screener in MODE=%s", MODE)

    if not TOKEN:
        logging.error("UPSTOX_TOKEN missing. No market signals generated.")
        return None

    daily_data = fetch_universe(fetch_daily_candles)
    if not daily_data:
        logging.error("No valid daily candles. Screener stopped.")
        return None

    # Only fetch intraday data for MORNING or combined/manual modes.
    intraday_data = {}
    if MODE in {"MORNING", "INTRADAY", "SWING"}:
        intraday_data = fetch_universe(fetch_intraday_candles)

    if MODE in {"MORNING", "INTRADAY"}:
        intraday_bull, intraday_bear = scan_intraday(intraday_data)
        option_bull, option_bear = [], []
    elif MODE == "BTST":
        intraday_bull, intraday_bear = [], []
        option_bull, option_bear = scan_stock_options(daily_data)
    else:
        intraday_bull, intraday_bear = scan_intraday(intraday_data)
        option_bull, option_bear = scan_stock_options(daily_data)

    summary = market_summary(daily_data)
    summary["ideas_count"] = (
        len(intraday_bull) + len(intraday_bear)
        + len(option_bull) + len(option_bear)
    )
    if option_bull:
        summary["best_rated"] = option_bull[0]["symbol"]

    try:
        global_cues = fetch_global_cues() or []
    except Exception as exc:
        logging.warning("Global cues unavailable: %s", exc)
        global_cues = []

    try:
        sectors = compute_sector_strength(daily_data) or []
    except Exception as exc:
        logging.warning("Sector strength unavailable: %s", exc)
        sectors = []

    payload = {
        "mode": MODE,
        "summary": summary,
        "global_cues": global_cues,
        "index_view": {},
        "index_trades": [],
        "sectors": sectors,
        "setups": {"bullish": option_bull, "bearish": option_bear},
        "equity_intraday": intraday_bull + intraday_bear,
        "intraday_bullish": intraday_bull,
        "intraday_bearish": intraday_bear,
        "options_bullish": option_bull,
        "options_bearish": option_bear,
        "capital": CAPITAL if CAPITAL_CONFIGURED else None,
        "capital_configured": CAPITAL_CONFIGURED,
        "risk_pct": RISK_PCT,
        "max_allocation_pct": MAX_ALLOCATION_PCT,
        "max_daily_loss_pct": MAX_DAILY_LOSS_PCT,
        "execution": "SIGNAL_ONLY",
        "automatic_orders": False,
        "intraday_status": (
            "UPSTOX V3 5-MINUTE DATA"
            if intraday_data else "NOT RUN / NO INTRADAY DATA"
        ),
        "option_strategy": "OPTION BUYING OR HEDGED DEBIT SPREAD",
    }

    try:
        send_alert(payload)
    except Exception as exc:
        logging.error("Report delivery failed: %s", exc)

    logging.info(
        "Completed MODE=%s | Intraday %s BUY/%s SELL | Options %s Bullish/%s Bearish",
        MODE, len(intraday_bull), len(intraday_bear),
        len(option_bull), len(option_bear),
    )
    return payload


if __name__ == "__main__":
    run_screener()
