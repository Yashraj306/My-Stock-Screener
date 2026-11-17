"""
Signal tracker for My Stock Screener.

This module records screener signals and evaluates their later price status.
It does not connect to a broker and never places, modifies, or exits orders.

Storage:
- JSON file, default: data/signal_tracker.json
- Override with TRACKER_FILE environment variable

Main public functions:
- load_tracker()
- save_tracker(records)
- record_signals(signals, mode=None)
- update_signal_prices(latest_prices)
- get_tracker_summary()

`latest_prices` must be a mapping such as {"RELIANCE": 1234.5}.
No prices are fetched or fabricated by this module.
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

TRACKER_FILE = Path(
    os.getenv("TRACKER_FILE", "data/signal_tracker.json")
)

VALID_STATUSES = {
    "OPEN",
    "TARGET_1_HIT",
    "TARGET_2_HIT",
    "STOP_LOSS_HIT",
    "EXPIRED",
    "DATA_UNAVAILABLE",
}


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _number(value: Any):
    try:
        result = float(value)
        if result != result or result in (float("inf"), float("-inf")):
            return None
        return result
    except (TypeError, ValueError):
        return None


def _read_json(path: Path):
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, list):
            logger.warning("Tracker file is not a JSON list; starting empty.")
            return []
        return [row for row in data if isinstance(row, dict)]
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Could not read tracker file %s: %s", path, exc)
        return []


def load_tracker(path=None):
    """Load tracker records. Returns an empty list if no file exists."""
    target = Path(path) if path else TRACKER_FILE
    return _read_json(target)


def save_tracker(records, path=None):
    """
    Atomically save tracker records as JSON.

    Returns the saved file path. Raises OSError if persistence fails.
    """
    target = Path(path) if path else TRACKER_FILE
    target.parent.mkdir(parents=True, exist_ok=True)

    safe_records = records if isinstance(records, list) else []
    fd, temp_name = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=str(target.parent),
        text=True,
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                safe_records,
                handle,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            handle.write("\n")
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise

    return str(target)


def _signal_direction(signal):
    action = str(
        signal.get("action")
        or signal.get("signal")
        or signal.get("side")
        or ""
    ).upper()

    if "BUY" in action or "BULL" in action or "LONG" in action:
        return "BULLISH"
    if "SELL" in action or "BEAR" in action or "SHORT" in action:
        return "BEARISH"
    return "UNKNOWN"


def _make_record(signal, mode=None):
    symbol = str(signal.get("symbol", "")).strip().upper()
    entry = _number(signal.get("entry"))
    stop = _number(signal.get("stop_loss"))
    target_1 = _number(signal.get("target_1"))
    target_2 = _number(signal.get("target_2"))

    if not symbol or entry is None or entry <= 0:
        return None

    direction = _signal_direction(signal)
    if direction == "UNKNOWN":
        return None

    if direction == "BULLISH":
        if stop is None or target_1 is None or stop >= entry or target_1 <= entry:
            return None
        if target_2 is not None and target_2 <= target_1:
            target_2 = None
    else:
        if stop is None or target_1 is None or stop <= entry or target_1 >= entry:
            return None
        if target_2 is not None and target_2 >= target_1:
            target_2 = None

    timestamp = _now_utc()
    signal_id = f"{symbol}-{timestamp}-{direction}"

    return {
        "signal_id": signal_id,
        "symbol": symbol,
        "mode": str(mode or signal.get("mode") or "UNKNOWN").upper(),
        "direction": direction,
        "entry": entry,
        "stop_loss": stop,
        "target_1": target_1,
        "target_2": target_2,
        "status": "OPEN",
        "created_at": timestamp,
        "last_updated_at": timestamp,
        "last_price": None,
        "closed_at": None,
        "execution": "SIGNAL_ONLY",
    }


def record_signals(signals, mode=None, path=None):
    """
    Add valid signals to the JSON tracker.

    `signals` may be a list of signal dictionaries. Duplicate signals with
    the same symbol, mode, direction and entry are not added while OPEN.
    Returns the newly added records.
    """
    records = load_tracker(path)
    new_records = []

    for signal in signals or []:
        if not isinstance(signal, dict):
            continue

        record = _make_record(signal, mode=mode)
        if record is None:
            continue

        duplicate = any(
            row.get("status") == "OPEN"
            and row.get("symbol") == record["symbol"]
            and row.get("mode") == record["mode"]
            and row.get("direction") == record["direction"]
            and _number(row.get("entry")) == record["entry"]
            for row in records
        )
        if duplicate:
            continue

        records.append(record)
        new_records.append(record)

    if new_records:
        save_tracker(records, path)

    return new_records


def _evaluate_status(record, price):
    direction = record.get("direction")
    stop = _number(record.get("stop_loss"))
    target_1 = _number(record.get("target_1"))
    target_2 = _number(record.get("target_2"))

    if None in (stop, target_1):
        return "DATA_UNAVAILABLE"

    # If a single candle crosses both stop and target, this tracker cannot
    # infer which happened first. It requires a single latest traded price.
    if direction == "BULLISH":
        if price <= stop:
            return "STOP_LOSS_HIT"
        if target_2 is not None and price >= target_2:
            return "TARGET_2_HIT"
        if price >= target_1:
            return "TARGET_1_HIT"
    elif direction == "BEARISH":
        if price >= stop:
            return "STOP_LOSS_HIT"
        if target_2 is not None and price <= target_2:
            return "TARGET_2_HIT"
        if price <= target_1:
            return "TARGET_1_HIT"

    return "OPEN"


def update_signal_prices(latest_prices, path=None):
    """
    Update OPEN records using caller-supplied latest prices.

    latest_prices maps symbol to a numeric latest price. Missing symbols are
    left unchanged; no price lookup or synthetic value is used.
    Returns the number of records whose status or price changed.
    """
    if not isinstance(latest_prices, dict):
        raise TypeError("latest_prices must be a dictionary of symbol: price")

    records = load_tracker(path)
    changed = 0
    timestamp = _now_utc()

    for record in records:
        if record.get("status") != "OPEN":
            continue

        symbol = str(record.get("symbol", "")).upper()
        price = _number(latest_prices.get(symbol))
        if price is None or price <= 0:
            continue

        old_price = record.get("last_price")
        new_status = _evaluate_status(record, price)

        record["last_price"] = price
        record["last_updated_at"] = timestamp

        if new_status != record.get("status"):
            record["status"] = new_status
            if new_status != "OPEN":
                record["closed_at"] = timestamp

        if old_price != price or new_status != "OPEN":
            changed += 1

    if changed:
        save_tracker(records, path)

    return changed


def get_tracker_summary(path=None):
    """Return counts by status and total record count."""
    records = load_tracker(path)
    counts = {status: 0 for status in VALID_STATUSES}

    for record in records:
        status = record.get("status", "DATA_UNAVAILABLE")
        if status not in counts:
            status = "DATA_UNAVAILABLE"
        counts[status] += 1

    return {
        "total": len(records),
        "open": counts["OPEN"],
        "target_1_hit": counts["TARGET_1_HIT"],
        "target_2_hit": counts["TARGET_2_HIT"],
        "stop_loss_hit": counts["STOP_LOSS_HIT"],
        "expired": counts["EXPIRED"],
        "data_unavailable": counts["DATA_UNAVAILABLE"],
    }


def mark_expired(before_iso, path=None):
    """
    Mark OPEN records created before an ISO-8601 timestamp as EXPIRED.
    The caller must supply the expiry cutoff explicitly.
    """
    cutoff = str(before_iso)
    records = load_tracker(path)
    changed = 0
    timestamp = _now_utc()

    for record in records:
        if record.get("status") == "OPEN" and str(record.get("created_at", "")) < cutoff:
            record["status"] = "EXPIRED"
            record["last_updated_at"] = timestamp
            record["closed_at"] = timestamp
            changed += 1

    if changed:
        save_tracker(records, path)

    return changed
