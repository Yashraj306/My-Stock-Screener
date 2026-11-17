
import logging
import math

import pandas as pd


LOG = logging.getLogger(__name__)


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _valid_option(row, prefix):
    """Validate a real option quote and its liquidity."""

    ltp = _num(row.get(f"{prefix}_ltp"))
    bid = _num(row.get(f"{prefix}_bid"))
    ask = _num(row.get(f"{prefix}_ask"))
    oi = _num(row.get(f"{prefix}_oi"))
    volume = _num(row.get(f"{prefix}_vol"))
    iv = _num(row.get(f"{prefix}_iv"))
    delta = _num(row.get(f"{prefix}_delta"))
    theta = _num(row.get(f"{prefix}_theta"))
    instrument = row.get(f"{prefix}_instrument_key")

    if not instrument:
        return None

    if not all(x is not None and x > 0 for x in [ltp, bid, ask]):
        return None

    if ask < bid or ltp <= 0:
        return None

    mid = (bid + ask) / 2

    if mid <= 0:
        return None

    spread_pct = (ask - bid) / mid * 100

    if spread_pct > 5:
        return None

    if oi is None or oi <= 0:
        return None

    if volume is None or volume <= 0:
        return None

    if iv is None or iv <= 0:
        return None

    if delta is None or theta is None:
        return None

    return {
        "ltp": ltp,
        "bid": bid,
        "ask": ask,
        "oi": oi,
        "volume": volume,
        "iv": iv,
        "delta": delta,
        "theta": theta,
        "spread_pct": spread_pct,
        "instrument_key": instrument
    }


def _select_leg(df, prefix, spot, direction):
    """Select a liquid option using actual market data."""

    candidates = []

    for _, row in df.iterrows():
        strike = _num(row.get("strike"))

        if strike is None or strike <= 0:
            continue

        quote = _valid_option(row, prefix)

        if quote is None:
            continue

        delta = abs(quote["delta"])

        if direction == "CALL":
            if not 0.35 <= delta <= 0.75:
                continue
        else:
            if not 0.35 <= delta <= 0.75:
                continue

        distance = abs(strike - spot) / spot * 100

        if distance > 8:
            continue

        candidates.append({
            "strike": strike,
            "quote": quote,
            "distance": distance
        })

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (
            abs(abs(x["quote"]["delta"]) - 0.55),
            x["quote"]["spread_pct"],
            x["distance"]
        )
    )

    return candidates[0]


def _select_hedge(df, prefix, spot, direction, long_strike):
    """Select a farther OTM short option for a debit spread."""

    candidates = []

    for _, row in df.iterrows():
        strike = _num(row.get("strike"))

        if strike is None or strike <= 0:
            continue

        if direction == "CALL" and strike <= long_strike:
            continue

        if direction == "PUT" and strike >= long_strike:
            continue

        quote = _valid_option(row, prefix)

        if quote is None:
            continue

        delta = abs(quote["delta"])

        if not 0.10 <= delta <= 0.40:
            continue

        candidates.append({
            "strike": strike,
            "quote": quote,
            "distance": abs(strike - long_strike)
        })

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (
            abs(abs(x["quote"]["delta"]) - 0.25),
            x["quote"]["spread_pct"],
            x["distance"]
        )
    )

    return candidates[0]


def _make_buying_plan(leg, option_type, expiry):
    quote = leg["quote"]

    return {
        "strategy": "OPTION_BUYING",
        "option_type": option_type,
        "expiry": expiry,
        "strike": leg["strike"],
        "instrument_key": quote["instrument_key"],
        "entry_premium": quote["ask"],
        "bid": quote["bid"],
        "ask": quote["ask"],
        "iv": quote["iv"],
        "delta": quote["delta"],
        "theta": quote["theta"],
        "oi": quote["oi"],
        "volume": quote["volume"],
        "maximum_loss_per_unit": quote["ask"],
        "quantity": None,
        "capital_configured": False,
        "execution": "SIGNAL_ONLY"
    }


def _make_spread_plan(long_leg, short_leg, option_type, expiry):
    long_quote = long_leg["quote"]
    short_quote = short_leg["quote"]

    # Conservative debit uses buy ask and sell bid.
    debit = long_quote["ask"] - short_quote["bid"]

    width = abs(
        long_leg["strike"] - short_leg["strike"]
    )

    if debit <= 0 or debit >= width:
        return None

    maximum_profit = width - debit

    return {
        "strategy": (
            "BULL_CALL_SPREAD"
            if option_type == "CE"
            else "BEAR_PUT_SPREAD"
        ),
        "option_type": option_type,
        "expiry": expiry,
        "buy_leg": {
            "strike": long_leg["strike"],
            "instrument_key": long_quote["instrument_key"],
            "entry_premium": long_quote["ask"]
        },
        "sell_leg": {
            "strike": short_leg["strike"],
            "instrument_key": short_quote["instrument_key"],
            "entry_premium": short_quote["bid"]
        },
        "net_debit_per_unit": round(debit, 2),
        "spread_width": round(width, 2),
        "maximum_loss_per_unit": round(debit, 2),
        "maximum_profit_per_unit": round(maximum_profit, 2),
        "breakeven": round(
            long_leg["strike"] + debit
            if option_type == "CE"
            else long_leg["strike"] - debit,
            2
        ),
        "quantity": None,
        "capital_configured": False,
        "execution": "SIGNAL_ONLY"
    }


def select_option_strategy(
    chain_result,
    signal,
    lot_size=None
):
    """
    Select option buying or debit spread.

    chain_result format:
        (dataframe, expiry, spot)

    signal:
        BUY / BULLISH / SELL / BEARISH

    No order placement and no quantity calculation.
    """

    if not chain_result or len(chain_result) != 3:
        return None

    df, expiry, spot = chain_result

    if not isinstance(df, pd.DataFrame) or df.empty:
        return None

    spot = _num(spot)

    if spot is None or spot <= 0 or not expiry:
        return None

    signal = str(signal).upper().strip()

    if signal in {"BUY", "BULLISH"}:
        direction = "CALL"
        prefix = "ce"
        option_type = "CE"

    elif signal in {"SELL", "BEARISH"}:
        direction = "PUT"
        prefix = "pe"
        option_type = "PE"

    else:
        return None

    long_leg = _select_leg(
        df, prefix, spot, direction
    )

    if long_leg is None:
        return None

    iv = long_leg["quote"]["iv"]
    theta = abs(long_leg["quote"]["theta"])

    # High IV or heavy theta favors a defined-risk spread.
    prefer_spread = iv >= 35 or theta >= 1.5

    short_leg = _select_hedge(
        df,
        prefix,
        spot,
        direction,
        long_leg["strike"]
    )

    if prefer_spread and short_leg is not None:
        spread = _make_spread_plan(
            long_leg,
            short_leg,
            option_type,
            expiry
        )

        if spread is not None:
            spread["selection_reason"] = (
                "Higher IV or theta; defined-risk debit spread selected"
            )
            return spread

    # Buying is allowed only when the option quote is liquid.
    if (
        long_leg["quote"]["spread_pct"] <= 2.5
        and iv < 45
    ):
        plan = _make_buying_plan(
            long_leg,
            option_type,
            expiry
        )

        plan["selection_reason"] = (
            "Liquid option with acceptable IV and bid-ask spread"
        )

        return plan

    # If no safe buying or spread structure exists, reject.
    return None
