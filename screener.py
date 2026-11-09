import os
import sys
import time
import logging
import warnings
import pandas as pd

warnings.filterwarnings("ignore")

from config import MODE, TITLE, CAPITAL, RISK_PCT, UPSTOX_TOKEN
from universe import INDEX_MAP, STOCKS_UNIVERSE
from tech import analyze
from setups import generate_setup, levels, flags_for, btst_gate, position_size
from options_data import fetch_upstox_candles, chain_upstox, chain_nse, nse_session
from chain import analyze_chain
from tracker import log_new_setups, update_open_trades
from notify import send_alert

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def classify_trade_suggestion(tech_data, setup, opt_info=None):
    price = tech_data.get("price", 0.0)
    rsi = tech_data.get("rsi", 50.0)
    adx = tech_data.get("adx", 0.0)
    vr = tech_data.get("vr", 1.0)
    ema20 = tech_data.get("ema20", price)
    ema50 = tech_data.get("ema50", price)
    atr = tech_data.get("atr", price * 0.015)

    pcr = opt_info.get("oi_pcr", 1.0) if opt_info else 1.0
    direction = "WATCH"
    reason = []

    # Bullish Trend Confirmation
    if price > ema20 and ema20 > ema50:
        if rsi >= 50 and adx >= 20 and pcr >= 0.85:
            direction = "STRONG BUY" if (vr >= 1.2 or pcr >= 1.1) else "BUY"
            reason.append("Trading above 20/50 EMA with strong momentum")
            if vr >= 1.2:
                reason.append(f"Volume surge ({vr}x)")
            if pcr >= 1.1:
                reason.append(f"Bullish Option PCR ({pcr})")

    # Bearish Breakdown Confirmation
    elif price < ema20 and ema20 < ema50:
        if rsi <= 45 and pcr <= 0.85:
            direction = "STRONG SELL" if pcr <= 0.65 else "SELL"
            reason.append("Trading below 20/50 EMA breakdown")
            if pcr <= 0.65:
                reason.append(f"Heavy Call writing / Low PCR ({pcr})")

    if setup:
        entry = setup["entry"]
        stop_loss = setup["stop_loss"]
        target_1 = setup["target_1"]
        target_2 = setup["target_2"]
        rr = setup["rr_ratio"]
        qty = setup["qty"]
    else:
        entry = round(price, 2)
        if "BUY" in direction:
            stop_loss = round(max(ema50, price - (1.5 * atr)), 2)
            risk = max(1.0, entry - stop_loss)
            target_1 = round(entry + (1.5 * risk), 2)
            target_2 = round(entry + (2.5 * risk), 2)
        else:
            stop_loss = round(min(ema50, price + (1.5 * atr)), 2)
            risk = max(1.0, stop_loss - entry)
            target_1 = round(entry - (1.5 * risk), 2)
            target_2 = round(entry - (2.5 * risk), 2)
        rr = round(abs(target_1 - entry) / risk, 2)
        qty = position_size(entry, stop_loss)

    return {
        "direction": direction,
        "entry": entry,
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "rr_ratio": rr,
        "qty": qty,
        "reasons": ", ".join(reason) if reason else "Consolidating near key moving averages"
    }


def run_screener():
    start = time.time()
    logging.info(f"Triggering Upstox Direct Market Feed ({TITLE} - {MODE})")

    if not UPSTOX_TOKEN:
        logging.error("UPSTOX_TOKEN is not configured! Please provide token in Secrets.")

    try:
        update_open_trades()
    except Exception as e:
        logging.warning(f"Tracker update skipped: {e}")

    sess = nse_session()

    index_analysis = []
    trade_suggestions = []
    full_stock_analysis = []

    # 1. Direct Upstox Feed for Indices
    for idx_name in ["NIFTY", "BANKNIFTY", "SENSEX"]:
        df = fetch_upstox_candles(idx_name)
        if df is None:
            continue

        t = analyze(df)
        pivots = levels(df)

        chain_df, expiry, source = None, None, None
        try:
            res = chain_upstox(idx_name)
            if res:
                chain_df, expiry, source = res
            elif sess and idx_name != "SENSEX":
                res_nse = chain_nse(idx_name, sess)
                if res_nse:
                    chain_df, expiry, source = res_nse
        except Exception:
            pass

        opt_info = analyze_chain(chain_df) if (chain_df is not None and not chain_df.empty) else None

        index_analysis.append({
            "symbol": idx_name,
            "spot": t.get("price") if t else round(df["Close"].iloc[-1], 2),
            "rsi": round(t.get("rsi", 50), 1) if t else None,
            "adx": round(t.get("adx", 0), 1) if t else None,
            "pivots": pivots,
            "options": opt_info
        })

    # 2. Direct Upstox Feed for Constituent F&O Stocks
    for sym in STOCKS_UNIVERSE:
        df = fetch_upstox_candles(sym)
        if df is None:
            continue

        t = analyze(df)
        if not t:
            continue

        setup = generate_setup(df)
        pivots = levels(df)
        flags = flags_for(df)

        # Fetch option chain for triggers & high volatility counters
        opt_info = None
        if setup or abs(t.get("rsi", 50) - 50) > 8 or t.get("vr", 1.0) > 1.2:
            chain_df, expiry, _ = None, None, None
            try:
                res = chain_upstox(sym)
                if res:
                    chain_df, expiry, _ = res
                elif sess:
                    res_nse = chain_nse(sym, sess)
                    if res_nse:
                        chain_df, expiry, _ = res_nse
            except Exception:
                pass
            if chain_df is not None and not chain_df.empty:
                opt_info = analyze_chain(chain_df)

        suggestion = classify_trade_suggestion(t, setup, opt_info)

        stock_record = {
            "symbol": sym,
            "price": t["price"],
            "rsi": round(t["rsi"], 1),
            "adx": round(t["adx"], 1),
            "volume_ratio": round(t.get("vr", 1.0), 2),
            "ema20": round(t.get("ema20", 0), 2),
            "ema50": round(t.get("ema50", 0), 2),
            "pivots": pivots,
            "flags": flags,
            "options": opt_info,
            "trade": suggestion
        }
        full_stock_analysis.append(stock_record)

        if suggestion["direction"] in ["STRONG BUY", "BUY", "STRONG SELL", "SELL"]:
            trade_suggestions.append(stock_record)

    # 3. Log into tracker
    if trade_suggestions:
        active_setups = []
        for s in trade_suggestions:
            t_plan = s["trade"]
            active_setups.append({
                "symbol": s["symbol"],
                "signal": "BUY" if "BUY" in t_plan["direction"] else "SELL",
                "entry": t_plan["entry"],
                "stop_loss": t_plan["stop_loss"],
                "target_1": t_plan["target_1"],
                "target_2": t_plan["target_2"],
                "qty": t_plan["qty"]
            })
        log_new_setups(active_setups)

    payload = {
        "indices": index_analysis,
        "trades": trade_suggestions,
        "total_scanned": len(full_stock_analysis),
        "stocks": full_stock_analysis
    }

    elapsed = round(time.time() - start, 2)
    logging.info(f"Upstox direct scan complete in {elapsed}s. Signals generated: {len(trade_suggestions)}")

    send_alert(payload)
    return payload


if __name__ == "__main__":
    run_screener()
