import os
import math
import time
import requests
import datetime as dt
import logging
import warnings
import pandas as pd
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor

warnings.filterwarnings("ignore")

from config import MODE, TITLE, CAPITAL, RISK_PCT
from universe import INDEX_MAP, STOCKS_UNIVERSE, ISIN_MAP, LOT_SIZES, eq_keys
from tech import analyze
from options_data import chain_upstox
from chain import analyze_chain
from market import fetch_global_cues, compute_sector_strength, compute_point_contributors, SECTOR_MAP
from notify import send_alert

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
UPSTOX_TOKEN = os.getenv("UPSTOX_TOKEN", "").strip()


def bsm_greeks(spot, strike, dte_days, iv_pct, side="CE", r=0.07):
    T = max(dte_days / 365.0, 1 / 365.0)
    sigma = max(iv_pct / 100.0, 0.12)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    norm_cdf = lambda x: 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
    if side == "CE":
        price = spot * norm_cdf(d1) - strike * math.exp(-r * T) * norm_cdf(d2)
        delta = norm_cdf(d1)
    else:
        price = strike * math.exp(-r * T) * norm_cdf(-d2) - spot * norm_cdf(-d1)
        delta = norm_cdf(d1) - 1.0

    return {"ltp": max(round(price, 1), 1.0), "delta": round(abs(delta), 2), "iv": iv_pct}


def fetch_upstox_live_candles():
    if not UPSTOX_TOKEN:
        return {}, False

    headers = {"Accept": "application/json", "Authorization": f"Bearer {UPSTOX_TOKEN}"}
    key_map = eq_keys()
    to_date = dt.date.today().strftime("%Y-%m-%d")
    from_date = (dt.date.today() - dt.timedelta(days=100)).strftime("%Y-%m-%d")
    data = {}

    def fetch_one(item):
        name, inst_key = item
        url = f"https://api.upstox.com/v2/historical-candle/{ requests.utils.quote(inst_key, safe='') }/day/{to_date}/{from_date}"
        try:
            r = requests.get(url, headers=headers, timeout=6)
            if r.status_code == 200:
                candles = r.json().get("data", {}).get("candles", [])
                if candles and len(candles) >= 10:
                    df = pd.DataFrame(candles, columns=["Date", "Open", "High", "Low", "Close", "Volume", "OI"])
                    return name, df.iloc[::-1].reset_index(drop=True)
        except Exception:
            pass
        return name, None

    with ThreadPoolExecutor(max_workers=10) as ex:
        for name, df in ex.map(fetch_one, key_map.items()):
            if df is not None: data[name] = df

    if len(data) < 15: return {}, False
    return data, True


def batch_fetch_candles():
    upstox_data, ok = fetch_upstox_live_candles()
    if ok and len(upstox_data) >= 20:
        return upstox_data, "Upstox Live API v2"

    tickers = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "FINNIFTY": "NIFTY_FIN_SERVICE.NS", "SENSEX": "^BSESN"}
    for s in STOCKS_UNIVERSE: tickers[s] = f"{s}.NS"

    try:
        raw = yf.download(" ".join(tickers.values()), period="3mo", interval="1d", group_by="ticker", threads=True, progress=False, timeout=20)
    except Exception:
        return {}, "Fallback"

    data = {}
    is_multi = isinstance(raw.columns, pd.MultiIndex)
    for name, sym in tickers.items():
        try:
            df = raw[sym].dropna(how="all").copy() if is_multi else raw.copy()
            if not df.empty and len(df) >= 10:
                data[name] = df.reset_index().rename(columns=lambda c: str(c).capitalize())
        except Exception:
            continue
    return data, "Yahoo Finance Feed"


def build_hedged_option_plan(sym, chain_df, spot, atr_val, side="CE", days_to_exp=12):
    step = 100 if (sym in ["BANKNIFTY", "SENSEX"] or spot > 3000) else 50
    lot_size = int(LOT_SIZES.get(sym, 250))
    est_iv = 22.0

    atm_strike = round(spot / step) * step
    otm_hedge_strike = (atm_strike + 2 * step) if side == "CE" else (atm_strike - 2 * step)

    g_buy = bsm_greeks(spot, atm_strike, days_to_exp, est_iv, side)
    g_sell = bsm_greeks(spot, otm_hedge_strike, days_to_exp, est_iv * 0.95, side)
    buy_ltp, sell_ltp = g_buy["ltp"], min(g_sell["ltp"], round(g_buy["ltp"] * 0.45, 1))

    net_debit = round(max(buy_ltp - sell_ltp, 0.5), 1)
    spread_width = abs(otm_hedge_strike - atm_strike)
    max_profit_pts = max(round(spread_width - net_debit, 1), 1.0)

    fmt_s = lambda x: int(x) if float(x).is_integer() else x
    return {
        "strike_name": f"{fmt_s(atm_strike)} {side}",
        "ltp": buy_ltp, "delta": g_buy["delta"], "iv": est_iv,
        "sl": round(buy_ltp * 0.6, 1), "t1": round(buy_ltp * 1.4, 1)
    }, {
        "strategy_name": "Bull Call Spread" if side == "CE" else "Bear Put Spread",
        "lot_size": lot_size, "buy_leg": f"{fmt_s(atm_strike)} {side}", "buy_prem": buy_ltp,
        "sell_leg": f"{fmt_s(otm_hedge_strike)} {side}", "sell_prem": sell_ltp,
        "net_debit": net_debit, "breakeven": atm_strike + net_debit if side == "CE" else atm_strike - net_debit,
        "max_loss": int(round(net_debit * lot_size)), "max_profit": int(round(max_profit_pts * lot_size))
    }


def run_screener():
    logging.info(f"Running screener in MODE: {MODE}")
    candles, data_source = batch_fetch_candles()

    nifty_df = candles.get("NIFTY")
    nifty_spot = round(nifty_df["Close"].iloc[-1]) if nifty_df is not None else 25000
    nifty_prev = nifty_df["Close"].iloc[-2] if nifty_df is not None else nifty_spot
    nifty_chg = f"{(nifty_spot / nifty_prev - 1) * 100:+.2f}%"
    nifty_rsi = round(analyze(nifty_df).get("rsi", 50))

    bnf_df = candles.get("BANKNIFTY")
    bnf_spot = round(bnf_df["Close"].iloc[-1]) if bnf_df is not None else 59000
    bnf_prev = bnf_df["Close"].iloc[-2] if bnf_df is not None else bnf_spot
    bnf_chg = f"{(bnf_spot / bnf_prev - 1) * 100:+.2f}%"

    fin_df = candles.get("FINNIFTY")
    fin_spot = round(fin_df["Close"].iloc[-1]) if fin_df is not None else 27300
    fin_prev = fin_df["Close"].iloc[-2] if fin_df is not None else fin_spot
    fin_chg = f"{(fin_spot / fin_prev - 1) * 100:+.2f}%"

    ups, downs, above_20, above_50, scanned = 0, 0, 0, 0, 0
    scored_stocks = []

    for sym in STOCKS_UNIVERSE:
        df = candles.get(sym)
        if df is not None and len(df) >= 15:
            scanned += 1
            c, p = df["Close"].iloc[-1], df["Close"].iloc[-2]
            if c > p: ups += 1
            else: downs += 1
            t = analyze(df)
            if t:
                if c > t["ema20"]: above_20 += 1
                if c > t["ema50"]: above_50 += 1
                scored_stocks.append({
                    "symbol": sym, "price": c, "chg": ((c/p)-1)*100,
                    "rsi": t["rsi"], "vr": t["vr"], "atr": t["atr"],
                    "vwap": t["vwap"], "sector": SECTOR_MAP.get(sym, "OTHER"),
                    "score": t["rsi"] + (t["vr"] * 10)
                })

    sorted_bulls = sorted(scored_stocks, key=lambda x: x["score"], reverse=True)[:5]
    sorted_bears = sorted(scored_stocks, key=lambda x: x["score"])[:5]

    def make_setup(s, is_bull):
        p, atr = s["price"], s["atr"]
        sl = round(p - 1.5 * atr, 2) if is_bull else round(p + 1.5 * atr, 2)
        risk = max(abs(p - sl), p * 0.005)
        side = "CE" if is_bull else "PE"
        strike, hedge = build_hedged_option_plan(s["symbol"], None, p, atr, side)
        return {
            "symbol": s["symbol"], "side": f"BUY {side}", "setup_kind": "Spread Setup",
            "grade": "A", "sector": s["sector"], "close": p, "change_pct": s["chg"],
            "rsi": s["rsi"], "vol_ratio": s["vr"], "entry": p, "stop_loss": sl,
            "target_1": round(p + 1.5 * risk if is_bull else p - 1.5 * risk, 2),
            "target_2": round(p + 2.5 * risk if is_bull else p - 2.5 * risk, 2),
            "strike": strike, "hedge_plan": hedge, "reasons": [f"Momentum score strong | RSI {s['rsi']:.0f}"]
        }

    final_bulls = [make_setup(s, True) for s in sorted_bulls]
    final_bears = [make_setup(s, False) for s in sorted_bears]

    index_trades = []
    for idx_name in ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]:
        df = candles.get(idx_name)
        if df is not None:
            t = analyze(df)
            p = t["price"]
            strike, hedge = build_hedged_option_plan(idx_name, None, p, t["atr"], "CE")
            index_trades.append({
                "symbol": idx_name, "side": "BUY CE", "setup_kind": "Index Spread",
                "grade": "A", "sector": "INDEX", "close": p, "change_pct": 0.5,
                "strike": strike, "hedge_plan": hedge, "reasons": ["Index momentum alignment"]
            })

    equity_intraday = []
    for s in sorted_bulls[:3]:
        p = s["price"]
        equity_intraday.append({
            "symbol": s["symbol"], "sector": s["sector"], "action": "BUY (LONG)",
            "entry": p, "chg": s["chg"], "stop_loss": round(p - s["atr"], 2),
            "target_1": round(p + 1.5 * s["atr"], 2), "target_2": round(p + 2.5 * s["atr"], 2),
            "qty": int(100000 // p), "exposure": int(100000), "rsi": s["rsi"], "volume_ratio": s["vr"], "vwap": s["vwap"]
        })

    payload = {
        "mode": MODE,
        "summary": {
            "data_source": data_source, "nifty_spot": f"{nifty_spot:,}", "nifty_chg": nifty_chg,
            "bnf_spot": f"{bnf_spot:,}", "bnf_chg": bnf_chg, "fin_spot": f"{fin_spot:,}", "fin_chg": fin_chg,
            "breadth_ratio": f"{ups} Adv / {downs} Dec", "above_ema20": round((above_20 / max(1, scanned)) * 100),
            "breadth_tone": "BULLISH" if ups > downs else "BEARISH", "ideas_count": len(index_trades) + len(final_bulls) + len(final_bears) + len(equity_intraday)
        },
        "global_cues": fetch_global_cues(),
        "sectors": compute_sector_strength(candles),
        "index_trades": index_trades,
        "setups": {"bullish": final_bulls, "bearish": final_bears},
        "equity_intraday": equity_intraday
    }

    send_alert(payload)
    logging.info("Screener executed successfully and email sent!")
    return payload


if __name__ == "__main__":
    run_screener()
