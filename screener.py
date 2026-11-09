import os
import sys
import time
import logging
import pandas as pd
import yfinance as yf

from config import MODE, TITLE, INDICES, SWING, CAPITAL
from universe import eq_keys
from tech import analyze
from setups import generate_setup, prerally, btst_gate, flags_for, levels, grade
from options_data import chain_upstox, chain_nse, nse_session
from chain import analyze_chain
from notify import send_alert

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def fetch_historical_data(symbol, period="6mo", interval="1d"):
    """Fetches daily candle data using Yahoo Finance."""
    ticker_sym = f"{symbol}.NS" if not symbol.endswith(".NS") and not symbol.startswith("^") else symbol
    try:
        df = yf.download(ticker_sym, period=period, interval=interval, progress=False)
        if df.empty or len(df) < 30:
            return None
        # Flatten MultiIndex columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.reset_index()
        # Rename standard columns if needed
        cols = {c: c.capitalize() for c in df.columns}
        df.rename(columns=cols, inplace=True)
        return df
    except Exception as e:
        logging.warning(f"Error fetching data for {symbol}: {e}")
        return None


def run_swing_screener():
    """Scans equity universe for Swing Trading signals."""
    logging.info(f"Starting {TITLE} in MODE={MODE}")
    symbols = list(eq_keys().keys())
    if not symbols:
        # Fallback default universe if eq_keys is empty
        symbols = [
            "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN",
            "BHARTIARTL", "KOTAKBANK", "LT", "ITC", "AXISBANK", "TATAMOTORS"
        ]

    swing_results = []
    btst_results = []
    option_insights = []

    # NSE session fallback if needed
    sess = nse_session()

    for idx, sym in enumerate(symbols, 1):
        logging.info(f"[{idx}/{len(symbols)}] Scanning {sym}...")
        df = fetch_historical_data(sym)
        if df is None:
            continue

        # 1. Professional Swing Setup Evaluation
        setup = generate_setup(df)
        if setup:
            setup["symbol"] = sym
            setup["flags"] = flags_for(df)
            setup["levels"] = levels(df)
            swing_results.append(setup)

        # 2. BTST Scan (if applicable)
        if MODE in ("BTST", "SWING") and btst_gate(df):
            tech_info = analyze(df)
            if tech_info:
                btst_results.append({
                    "symbol": sym,
                    "price": tech_info["price"],
                    "rsi": tech_info["rsi"],
                    "adx": tech_info["adx"]
                })

        # 3. Option Chain Scan (F&O stocks or Indices)
        if sym in INDICES or sym in symbols[:15]:  # scan high-liquidity names
            chain_df, expiry, source = None, None, None
            try:
                res = chain_upstox(sym)
                if res:
                    chain_df, expiry, source = res
                elif sess:
                    res_nse = chain_nse(sym, sess)
                    if res_nse:
                        chain_df, expiry, source = res_nse
            except Exception as e:
                logging.debug(f"Option fetch error {sym}: {e}")

            if chain_df is not None and not chain_df.empty:
                chain_info = analyze_chain(chain_df)
                if chain_info:
                    chain_info["symbol"] = sym
                    chain_info["expiry"] = expiry
                    chain_info["source"] = source
                    option_insights.append(chain_info)

        time.sleep(0.3)  # Rate-limit safety

    return {
        "swing": swing_results,
        "btst": btst_results,
        "options": option_insights
    }


def main():
    start_time = time.time()
    results = run_swing_screener()
    elapsed = round(time.time() - start_time, 2)
    logging.info(f"Scan finished in {elapsed}s. Found {len(results['swing'])} swing setups.")

    # Send alerts via Telegram and Email
    send_alert(results)


if __name__ == "__main__":
    main()
