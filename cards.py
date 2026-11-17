"""
Card formatting helpers for My Stock Screener.

Creates readable, plain-text stock and stock-option cards from signal
dictionaries. This module has no broker integration and never places orders.

Public functions:
- format_equity_card(signal)
- format_option_card(signal)
- format_signal_card(signal, card_type="equity")
- build_signal_cards(signals, card_type="equity")
- render_cards_text(signals, card_type="equity")

Missing values are shown as "Unavailable"; no market data is invented.
"""

from html import escape
from typing import Any


def _value(data: dict, *keys):
    for key in keys:
        value = data.get(key)
        if value is not None and value != "":
            return value
    return None


def _number(value):
    try:
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return number
    except (TypeError, ValueError):
        return None


def _fmt_price(value, currency="₹"):
    number = _number(value)
    if number is None:
        return "Unavailable"
    return f"{currency}{number:,.2f}"


def _fmt_metric(value, suffix=""):
    number = _number(value)
    if number is None:
        return "Unavailable"
    return f"{number:.2f}{suffix}"


def _direction(signal: dict) -> str:
    raw = str(
        _value(signal, "action", "signal", "side", "direction") or ""
    ).upper()

    if "BUY" in raw or "BULL" in raw or "LONG" in raw or "CALL" in raw:
        return "BULLISH"
    if "SELL" in raw or "BEAR" in raw or "SHORT" in raw or "PUT" in raw:
        return "BEARISH"
    return "UNCLASSIFIED"


def _base_fields(signal: dict) -> list:
    symbol = str(_value(signal, "symbol", "tradingsymbol") or "UNKNOWN").upper()
    direction = _direction(signal)
    mode = str(_value(signal, "setup_kind", "mode") or "SIGNAL").upper()

    return [
        f"{symbol} | {direction}",
        f"Setup: {mode}",
        f"Entry: {_fmt_price(_value(signal, 'entry', 'entry_price'))}",
        f"Stop Loss: {_fmt_price(_value(signal, 'stop_loss', 'sl'))}",
        f"Target 1: {_fmt_price(_value(signal, 'target_1', 'target1'))}",
        f"Target 2: {_fmt_price(_value(signal, 'target_2', 'target2'))}",
    ]


def format_equity_card(signal: dict) -> str:
    """Return a plain-text card for an intraday equity signal."""
    if not isinstance(signal, dict):
        raise TypeError("signal must be a dictionary")

    lines = ["=" * 44]
    lines.extend(_base_fields(signal))
    lines.extend([
        f"Sector: {_value(signal, 'sector') or 'Unavailable'}",
        f"RSI: {_fmt_metric(_value(signal, 'rsi'))}",
        f"ADX: {_fmt_metric(_value(signal, 'adx'))}",
        f"Volume Ratio: {_fmt_metric(_value(signal, 'volume_ratio', 'vol_ratio'), 'x')}",
        f"VWAP: {_fmt_price(_value(signal, 'vwap'))}",
        f"Leverage: {_value(signal, 'leverage') or 'Not assumed'}",
        "Quantity: Not calculated",
        "Execution: SIGNAL_ONLY",
        "Automatic orders: DISABLED",
        "=" * 44,
    ])
    return "\n".join(lines)


def format_option_card(signal: dict) -> str:
    """Return a plain-text card for a BTST stock-option signal."""
    if not isinstance(signal, dict):
        raise TypeError("signal must be a dictionary")

    option_plan = signal.get("option_plan")
    if not isinstance(option_plan, dict):
        option_plan = {}

    chain = signal.get("opt_chain")
    if not isinstance(chain, dict):
        chain = {}

    lines = ["=" * 44]
    lines.extend(_base_fields(signal))
    lines.extend([
        f"Option Strategy: {_value(signal, 'strategy') or option_plan.get('strategy') or chain.get('strategy') or 'Unavailable'}",
        f"Option Type: {_value(signal, 'option_type') or option_plan.get('option_type') or 'Unavailable'}",
        f"Strike: {_value(signal, 'strike') or option_plan.get('strike') or 'Unavailable'}",
        f"Expiry: {_value(signal, 'expiry') or option_plan.get('expiry') or chain.get('expiry') or 'Unavailable'}",
        f"Premium / Net Debit: {_fmt_price(_value(signal, 'premium', 'entry_premium', 'net_debit_per_unit') or option_plan.get('entry_premium') or option_plan.get('net_debit_per_unit'))}",
        f"Maximum Loss per Unit: {_fmt_price(_value(signal, 'maximum_loss_per_unit') or option_plan.get('maximum_loss_per_unit') or chain.get('maximum_loss_per_unit'))}",
        f"Maximum Profit per Unit: {_fmt_price(_value(signal, 'maximum_profit_per_unit') or option_plan.get('maximum_profit_per_unit'))}",
        f"IV: {_fmt_metric(_value(signal, 'iv') or option_plan.get('iv'))}",
        f"Delta: {_fmt_metric(_value(signal, 'delta') or option_plan.get('delta'))}",
        f"Theta: {_fmt_metric(_value(signal, 'theta') or option_plan.get('theta'))}",
        "Quantity: Not calculated",
        "Execution: SIGNAL_ONLY",
        "Automatic orders: DISABLED",
        "=" * 44,
    ])
    return "\n".join(lines)


def format_signal_card(signal: dict, card_type="equity") -> str:
    """Format one equity or option card."""
    kind = str(card_type or "equity").strip().lower()
    if kind in {"option", "options", "btst"}:
        return format_option_card(signal)
    return format_equity_card(signal)


def build_signal_cards(signals, card_type="equity") -> list:
    """Format a list of signal dictionaries, skipping invalid entries."""
    if not isinstance(signals, (list, tuple)):
        return []

    cards = []
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        try:
            cards.append(format_signal_card(signal, card_type=card_type))
        except (TypeError, ValueError, KeyError):
            continue
    return cards


def render_cards_text(signals, card_type="equity") -> str:
    """Join formatted cards into a single plain-text report section."""
    cards = build_signal_cards(signals, card_type=card_type)
    return "\n\n".join(cards) if cards else "No valid signals available."


def format_equity_card_html(signal: dict) -> str:
    """Return a minimal escaped HTML card for email/report integration."""
    text_content = format_equity_card(signal)
    safe_text = escape(text_content)
    return (
        '<div style="border:1px solid #d7dee8;border-radius:10px;'
        'padding:14px;margin:10px 0;font-family:Arial,sans-serif;'
        'white-space:pre-line;">'
        f"{safe_text}"
        "</div>"
    )


def format_option_card_html(signal: dict) -> str:
    """Return a minimal escaped HTML card for email/report integration."""
    text_content = format_option_card(signal)
    safe_text = escape(text_content)
    return (
        '<div style="border:1px solid #d7dee8;border-radius:10px;'
        'padding:14px;margin:10px 0;font-family:Arial,sans-serif;'
        'white-space:pre-line;">'
        f"{safe_text}"
        "</div>"
    )
