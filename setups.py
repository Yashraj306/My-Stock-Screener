
import pandas as pd
import numpy as np

from config import SWING, CAPITAL, RISK_PCT, MAX_ALLOCATION_PCT
from tech import analyze


def position_size(entry, stop, capital=CAPITAL):
    risk_amount = capital * RISK_PCT / 100
    risk_per_share = abs(entry - stop)

    if risk_per_share <= 0:
        return 0

    risk_qty = int(risk_amount / risk_per_share)

    allocation_amount = capital * MAX_ALLOCATION_PCT / 100
    allocation_qty = int(allocation_amount / entry)

    return max(0, min(risk_qty, allocation_qty))


def generate_setup(data, market_data=None, sector_data=None):

    t = analyze(data)

    if not t:
        return None

    price = t["price"]
    atr = t["atr"]
    rsi = t["rsi"]
    adx = t["adx"]
    volume = t["vr"]

    if price <= 0 or atr <= 0:
        return None

    if t["atr_pct"] > SWING["MAX_ATR_PERCENT"]:
        return None

    if adx < SWING["ADX_MIN"]:
        return None

    bullish_trend = (
        price > t["ema20"]
        and t["ema20"] > t["ema50"]
        and t["ema50"] > t["ema200"]
    )

    bearish_trend = (
        price < t["ema20"]
        and t["ema20"] < t["ema50"]
        and t["ema50"] < t["ema200"]
    )

    volume_ok = volume >= SWING["MIN_VOLUME_RATIO"]

    market_bullish = True
    market_bearish = True

    if market_data:
        market_bullish = (
            market_data.get("price", 0)
            > market_data.get("ema50", float("inf"))
        )

        market_bearish = (
            market_data.get("price", 0)
            < market_data.get("ema50", 0)
        )

    sector_bullish = True
    sector_bearish = True

    if sector_data:
        sector_return = sector_data.get("ret20", 0)

        sector_bullish = sector_return > 0
        sector_bearish = sector_return < 0

    direction = None
    reasons = []

    if (
        bullish_trend
        and market_bullish
        and sector_bullish
        and volume_ok
        and SWING["RSI_BULL_MIN"] <= rsi <= SWING["RSI_BULL_MAX"]
        and t["diff"] >= 3
    ):

        direction = "BUY"
        reasons.append("Bullish EMA alignment")
        reasons.append("Positive market trend")
        reasons.append("Positive sector strength")
        reasons.append("Volume confirmation")
        reasons.append("Bullish RSI and ADX")

    elif (
        bearish_trend
        and market_bearish
        and sector_bearish
        and volume_ok
        and SWING["RSI_BEAR_MIN"] <= rsi <= SWING["RSI_BEAR_MAX"]
        and t["diff"] <= -3
    ):

        direction = "SELL"
        reasons.append("Bearish EMA alignment")
        reasons.append("Negative market trend")
        reasons.append("Negative sector strength")
        reasons.append("Volume confirmation")
        reasons.append("Bearish RSI and ADX")

    if direction is None:
        return None

    stop_distance = atr * SWING["ATR_STOP_MULTIPLIER"]

    if direction == "BUY":
        stop = price - stop_distance

        target1 = price + (
            stop_distance * SWING["TARGET_1_R"]
        )

        target2 = price + (
            stop_distance * SWING["TARGET_2_R"]
        )

    else:
        stop = price + stop_distance

        target1 = price - (
            stop_distance * SWING["TARGET_1_R"]
        )

        target2 = price - (
            stop_distance * SWING["TARGET_2_R"]
        )

    qty = position_size(price, stop)

    if qty <= 0:
        return None

    risk = abs(price - stop)
    reward = abs(target2 - price)

    rr = reward / risk

    if rr < 2:
        return None

    return {
        "symbol": "",
        "direction": direction,
        "entry": round(price, 2),
        "stop_loss": round(stop, 2),
        "target1": round(target1, 2),
        "target2": round(target2, 2),
        "quantity": qty,
        "capital_required": round(price * qty, 2),
        "risk_amount": round(risk * qty, 2),
        "risk_reward": round(rr, 2),
        "rsi": round(rsi, 2),
        "adx": round(adx, 2),
        "volume_ratio": round(volume, 2),
        "reasons": reasons,
        "signal_date": t["asof"]
    }


def scan_swing(stock_data, market_data=None, sector_data=None):

    results = []

    for symbol, data in stock_data.items():

        try:
            sector = (
                sector_data.get(symbol)
                if sector_data else None
            )

            setup = generate_setup(
                data,
                market_data=market_data,
                sector_data=sector
            )

            if setup:
                setup["symbol"] = symbol
                results.append(setup)

        except Exception as e:
            print(f"Swing scan error {symbol}: {e}")

    return sorted(
        results,
        key=lambda x: (
            x["risk_reward"],
            x["adx"],
            x["volume_ratio"]
        ),
        reverse=True
    )
