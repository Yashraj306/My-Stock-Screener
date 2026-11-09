import os
import json
import logging
from datetime import datetime
import pandas as pd
import yfinance as yf

TRACKER_FILE = "trades_tracker.json"
logging.basicConfig(level=logging.INFO)


def load_tracker():
    """Loads existing open and closed trades from JSON storage."""
    if not os.path.exists(TRACKER_FILE):
        return {"open_trades": [], "closed_trades": []}
    try:
        with open(TRACKER_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Error loading tracker file: {e}")
        return {"open_trades": [], "closed_trades": []}


def save_tracker(data):
    """Persists trades data back to JSON storage."""
    try:
        with open(TRACKER_FILE, "w") as f:
            json.dump(data, f, indent=2)
        logging.info("Trades tracker updated successfully.")
    except Exception as e:
        logging.error(f"Failed to save tracker: {e}")


def log_new_setups(setups):
    """Logs qualified swing trade signals into the open trades book."""
    tracker = load_tracker()
    open_symbols = {t["symbol"] for t in tracker["open_trades"]}
    today_str = datetime.now().strftime("%Y-%m-%d")

    added_count = 0
    for s in setups:
        sym = s["symbol"]
        # Avoid duplicate tracking of the same symbol if already active
        if sym in open_symbols:
            continue

        trade_entry = {
            "id": f"{sym}_{today_str}",
            "symbol": sym,
            "entry_date": today_str,
            "signal": s["signal"],
            "entry_price": s["entry"],
            "stop_loss": s["stop_loss"],
            "target_1": s["target_1"],
            "target_2": s["target_2"],
            "quantity": s["qty"],
            "status": "OPEN"
        }
        tracker["open_trades"].append(trade_entry)
        added_count += 1

    if added_count > 0:
        save_tracker(tracker)
        logging.info(f"Logged {added_count} new trades to tracker.")


def update_open_trades():
    """
    Fetches the latest LTP for open trades, checks if Stop Loss
    or Targets are hit, and moves closed trades to the archive.
    """
    tracker = load_tracker()
    open_trades = tracker.get("open_trades", [])
    if not open_trades:
        logging.info("No active open trades to track.")
        return

    remaining_open = []
    closed_now = []
    today_str = datetime.now().strftime("%Y-%m-%d")

    for trade in open_trades:
        sym = trade["symbol"]
        ticker_sym = f"{sym}.NS" if not sym.endswith(".NS") else sym

        try:
            ticker = yf.Ticker(ticker_sym)
            hist = ticker.history(period="1d")
            if hist.empty:
                remaining_open.append(trade)
                continue

            ltp = round(float(hist["Close"].iloc[-1]), 2)
            high = round(float(hist["High"].iloc[-1]), 2)
            low = round(float(hist["Low"].iloc[-1]), 2)
        except Exception as e:
            logging.warning(f"Could not fetch latest price for {sym}: {e}")
            remaining_open.append(trade)
            continue

        signal = trade["signal"]
        entry = trade["entry_price"]
        sl = trade["stop_loss"]
        t1 = trade["target_1"]

        is_closed = False
        exit_price = ltp
        exit_reason = ""

        if signal == "BUY":
            if low <= sl:
                is_closed = True
                exit_price = sl
                exit_reason = "STOP_LOSS_HIT"
            elif high >= t1:
                is_closed = True
                exit_price = t1
                exit_reason = "TARGET_HIT"
        elif signal == "SELL":
            if high >= sl:
                is_closed = True
                exit_price = sl
                exit_reason = "STOP_LOSS_HIT"
            elif low <= t1:
                is_closed = True
                exit_price = t1
                exit_reason = "TARGET_HIT"

        if is_closed:
            pnl_per_share = (exit_price - entry) if signal == "BUY" else (entry - exit_price)
            total_pnl = round(pnl_per_share * trade["quantity"], 2)
            trade.update({
                "exit_date": today_str,
                "exit_price": exit_price,
                "status": exit_reason,
                "realized_pnl": total_pnl
            })
            closed_now.append(trade)
            logging.info(f"Trade closed: {sym} | Reason: {exit_reason} | PnL: ₹{total_pnl}")
        else:
            remaining_open.append(trade)

    tracker["open_trades"] = remaining_open
    tracker["closed_trades"].extend(closed_now)
    save_tracker(tracker)
