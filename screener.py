import datetime as dt
import logging
import math
import os
import warnings
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from tracker import evaluate_signals_chronologically, log_signals, walk_forward_summary

import pandas as pd
import requests
import yfinance as yf

from chain import analyze_chain
from config import (
    ALLOW_MIS_LEVERAGE,
    CAPITAL,
    CAPITAL_CONFIGURED,
    MAX_ALLOCATION_PCT,
    MAX_OPEN_POSITIONS,
    MIS_LEVERAGE,
    MODE,
    RISK_PCT,
    SWING,
    TITLE,
)
from market import (
    SECTOR_MAP,
    compute_point_contributors,
    compute_sector_strength,
    fetch_global_cues,
)
from notify import send_alert
from option_strategy import select_option_strategy
from options_data import chain_upstox
from tech import analyze, clean_ohlcv, pct
from universe import STOCKS_UNIVERSE, eq_keys

warnings.filterwarnings("ignore")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
UPSTOX_TOKEN = os.getenv("UPSTOX_TOKEN", "").strip()
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _fetch_upstox_historical_candles() -> tuple[dict[str, pd.DataFrame], bool]:
    if not UPSTOX_TOKEN:
        return {}, False

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {UPSTOX_TOKEN}",
    }
    key_map = eq_keys()
    to_date = dt.datetime.now(IST).date().strftime("%Y-%m-%d")
    from_date = (dt.datetime.now(IST).date() - dt.timedelta(days=375)).strftime("%Y-%m-%d")
    candles_by_sym: dict[str, pd.DataFrame] = {}

    def _fetch_one(item: tuple[str, str]) -> tuple[str, pd.DataFrame | None]:
        sym, inst_key = item
        encoded = quote(inst_key, safe="")
        url = f"https://api.upstox.com/v2/historical-candle/{encoded}/day/{to_date}/{from_date}"
        try:
            resp = requests.get(url, headers=headers, timeout=8)
            if resp.status_code != 200:
                return sym, None
            raw_candles = resp.json().get("data", {}).get("candles", [])
            if not raw_candles:
                return sym, None
            df = pd.DataFrame(
                raw_candles,
                columns=["Date", "Open", "High", "Low", "Close", "Volume", "OI"],
            )
            cleaned = clean_ohlcv(df)
            if cleaned.empty or len(cleaned) < 20:
                return sym, None
            return sym, cleaned
        except Exception as exc:
            logging.debug("Upstox historical fetch failed for %s: %s", sym, exc)
            return sym, None

    with ThreadPoolExecutor(max_workers=10) as pool:
        for sym, df in pool.map(_fetch_one, key_map.items()):
            if df is not None:
                candles_by_sym[sym] = df

    if len(candles_by_sym) < 10:
        return {}, False
    return candles_by_sym, True


def _fetch_yfinance_candles() -> tuple[dict[str, pd.DataFrame], str]:
    tickers = {
        "NIFTY": "^NSEI",
        "BANKNIFTY": "^NSEBANK",
        "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
        "SENSEX": "^BSESN",
    }
    for sym in STOCKS_UNIVERSE:
        tickers[sym] = f"{sym}.NS"

    try:
        raw = yf.download(
            " ".join(tickers.values()),
            period="1y",
            interval="1d",
            group_by="ticker",
            threads=True,
            progress=False,
            ignore_tz=True,
            timeout=25,
        )
    except Exception as exc:
        logging.error("yfinance 1y download error: %s", exc)
        return {}, "UNAVAILABLE"

    if raw is None or raw.empty:
        return {}, "UNAVAILABLE"

    data: dict[str, pd.DataFrame] = {}
    is_multi = isinstance(raw.columns, pd.MultiIndex)

    for name, yf_sym in tickers.items():
        try:
            slice_df = raw[yf_sym] if (is_multi and yf_sym in raw.columns.get_level_values(0)) else raw
            reset_df = slice_df.dropna(how="all").reset_index()
            cleaned = clean_ohlcv(reset_df)
            if not cleaned.empty and len(cleaned) >= 20:
                data[name] = cleaned
        except Exception as exc:
            logging.debug("Failed parsing candles for %s: %s", name, exc)
            continue

    source_label = "Yahoo Finance (1y)" if data else "UNAVAILABLE"
    return data, source_label


def batch_fetch_candles() -> tuple[dict[str, pd.DataFrame], str]:
    upstox_data, ok = _fetch_upstox_historical_candles()
    if ok:
        return upstox_data, "UPSTOX_V2_HIST_1Y"
    return _fetch_yfinance_candles()


def _bounded_scores(metrics: dict, high_val: float, low_val: float, mode: str) -> tuple[float, float]:
    price = float(metrics["price"])
    ema20 = float(metrics["ema20"])
    ema50 = float(metrics["ema50"])
    ema200 = float(metrics["ema200"])
    rsi_val = float(metrics["rsi"])
    vr = max(float(metrics.get("vr") or 0.0), 0.0)
    vwap = float(metrics.get("vwap") or price)

    bar_range = max(high_val - low_val, 1e-9)
    pos_in_range = min(max((price - low_val) / bar_range, 0.0), 1.0)

    bull_trend = 0.0
    bear_trend = 0.0
    if price > ema20:
        bull_trend += 12.0
    else:
        bear_trend += 12.0
    if ema20 > ema50:
        bull_trend += 13.0
    elif ema20 < ema50:
        bear_trend += 13.0
    if price > ema200:
        bull_trend += 10.0
    elif price < ema200:
        bear_trend += 10.0

    bull_rsi = max(0.0, min(25.0, ((rsi_val - 40.0) / 35.0) * 25.0))
    bear_rsi = max(0.0, min(25.0, ((60.0 - rsi_val) / 35.0) * 25.0))
    vol_score = min(20.0, (min(vr, 2.0) / 2.0) * 20.0)

    vwap_bull = 10.0 if price >= vwap else 0.0
    vwap_bear = 10.0 if price <= vwap else 0.0
    if mode == SWING:
        range_bull = 10.0 if price >= ema50 else pos_in_range * 10.0
        range_bear = 10.0 if price <= ema50 else (1.0 - pos_in_range) * 10.0
    else:
        range_bull = pos_in_range * 10.0
        range_bear = (1.0 - pos_in_range) * 10.0

    bull_total = round(min(max(bull_trend + bull_rsi + vol_score + vwap_bull + range_bull, 0.0), 100.0), 2)
    bear_total = round(min(max(bear_trend + bear_rsi + vol_score + vwap_bear + range_bear, 0.0), 100.0), 2)
    return bull_total, bear_total


def _compute_equity_sizing(entry: float, stop_loss: float) -> dict:
    risk_per_share = abs(float(entry) - float(stop_loss))
    effective_leverage = MIS_LEVERAGE if ALLOW_MIS_LEVERAGE else 1.0

    if not CAPITAL_CONFIGURED or CAPITAL <= 0.0 or entry <= 0.0 or risk_per_share <= 0.0:
        return {
            "qty": 0,
            "notional_exposure": 0.0,
            "margin_required": 0.0,
            "max_risk_rs": 0.0,
            "leverage_used": effective_leverage,
            "sizing_status": "NOT_CALCULATED",
        }

    max_risk_budget = CAPITAL * RISK_PCT
    max_notional_cap = CAPITAL * MAX_ALLOCATION_PCT * effective_leverage

    qty_by_risk = math.floor(max_risk_budget / risk_per_share)
    qty_by_notional = math.floor(max_notional_cap / entry)
    qty = max(0, min(qty_by_risk, qty_by_notional))

    notional_exposure = round(qty * entry, 2)
    margin_required = round(notional_exposure / effective_leverage, 2)
    actual_max_risk = round(qty * risk_per_share, 2)

    return {
        "qty": qty,
        "notional_exposure": notional_exposure,
        "margin_required": margin_required,
        "max_risk_rs": actual_max_risk,
        "leverage_used": effective_leverage,
        "sizing_status": "CALCULATED" if qty > 0 else "ZERO_QTY_UNDER_RISK_CAP",
    }


def _index_tag_for_sector(sec: str) -> str:
    if sec == "BANK":
        return "NSE/BSE • Bank/Fin"
    if sec == "FINANCE":
        return "NSE/BSE • FinNifty"
    return "NSE/BSE • LargeCap"
def run_screener() -> dict:
    run_ts = dt.datetime.now(IST).isoformat()
    logging.info("Running screener in MODE=%s at %s", MODE, run_ts)

    candles, data_source = batch_fetch_candles()
    sector_list = compute_sector_strength(candles)
    global_cues = fetch_global_cues()

    nifty_df = candles.get("NIFTY")
    nifty_m = analyze(nifty_df) if nifty_df is not None else {}
    nifty_spot = float(nifty_m.get("price") or 0.0)
    nifty_prev = float(nifty_m.get("prev_close") or nifty_spot)
    nifty_chg_num = pct(nifty_spot, nifty_prev)
    nifty_chg_str = f"{nifty_chg_num:+.2f}%"
    nifty_rsi = round(float(nifty_m.get("rsi") or 50.0))

    bnf_df = candles.get("BANKNIFTY")
    bnf_m = analyze(bnf_df) if bnf_df is not None else {}
    bnf_spot = float(bnf_m.get("price") or 0.0)
    bnf_prev = float(bnf_m.get("prev_close") or bnf_spot)
    bnf_chg_num = pct(bnf_spot, bnf_prev)
    bnf_chg_str = f"{bnf_chg_num:+.2f}%"

    fin_df = candles.get("FINNIFTY")
    fin_m = analyze(fin_df) if fin_df is not None else {}
    fin_spot = float(fin_m.get("price") or 0.0)
    fin_prev = float(fin_m.get("prev_close") or fin_spot)
    fin_chg_num = pct(fin_spot, fin_prev)
    fin_chg_str = f"{fin_chg_num:+.2f}%"

    contributors = compute_point_contributors(candles, nifty_spot, bnf_spot)

    ups = 0
    downs = 0
    above_20 = 0
    above_50 = 0
    scanned = 0
    scored_stocks: list[dict] = []

    for sym in STOCKS_UNIVERSE:
        df = candles.get(sym)
        if df is None or len(df) < 20:
            continue
        m = analyze(df)
        if not m:
            continue

        scanned += 1
        price = float(m["price"])
        prev_close = float(m["prev_close"])
        chg_pct = round(pct(price, prev_close), 2)

        if chg_pct > 0:
            ups += 1
        elif chg_pct < 0:
            downs += 1

        if price > float(m["ema20"]):
            above_20 += 1
        if price > float(m["ema50"]):
            above_50 += 1

        high_val = float(df["High"].iloc[-1])
        low_val = float(df["Low"].iloc[-1])
        bull_score, bear_score = _bounded_scores(m, high_val, low_val, MODE)

        scored_stocks.append({
            "symbol": sym,
            "price": price,
            "prev_close": prev_close,
            "change_pct": chg_pct,
            "rsi": round(float(m["rsi"]), 1),
            "adx": round(float(m["adx"]), 1),
            "vr": round(float(m["vr"]), 2),
            "atr": float(m["atr"]),
            "ema20": float(m["ema20"]),
            "ema50": float(m["ema50"]),
            "ema200": float(m["ema200"]),
            "has_ema200_history": bool(m.get("has_ema200_history")),
            "vwap": round(float(m["vwap"]), 2),
            "sector": SECTOR_MAP.get(sym, "OTHER"),
            "index_tag": _index_tag_for_sector(SECTOR_MAP.get(sym, "OTHER")),
            "bull_score": bull_score,
            "bear_score": bear_score,
            "bars": int(m["bars"]),
        })

    bull_candidates = [
        s for s in scored_stocks
        if s["price"] >= s["ema20"] and s["bull_score"] >= s["bear_score"]
    ]
    bear_candidates = [
        s for s in scored_stocks
        if s["price"] <= s["ema20"] and s["bear_score"] > s["bull_score"]
    ]

    top_bulls = sorted(bull_candidates, key=lambda x: x["bull_score"], reverse=True)[:5]
    top_bears = sorted(bear_candidates, key=lambda x: x["bear_score"], reverse=True)[:5]

    chains_cache: dict[str, tuple[pd.DataFrame | None, str | None]] = {}

    def _get_chain(symbol: str) -> tuple[pd.DataFrame | None, str | None]:
        if symbol not in chains_cache:
            res = chain_upstox(symbol)
            if res and isinstance(res, tuple) and len(res) >= 2:
                chains_cache[symbol] = (res[0], res[1])
            else:
                chains_cache[symbol] = (None, None)
        return chains_cache[symbol]

    index_trades: list[dict] = []
    for idx_name in ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]:
        df = candles.get(idx_name)
        if df is None:
            continue
        m = analyze(df)
        if not m:
            continue

        price = float(m["price"])
        prev_c = float(m["prev_close"])
        chg_pct = round(pct(price, prev_c), 2)
        atr_val = float(m["atr"])
        ema20 = float(m["ema20"])

        is_bull = (price >= ema20) and (chg_pct >= 0.0 or price >= float(m["vwap"]))
        side_code = "CE" if is_bull else "PE"
        side_label = "BUY CALL (CE)" if is_bull else "BUY PUT (PE)"

        sl = round(price - 1.2 * atr_val, 2) if is_bull else round(price + 1.2 * atr_val, 2)
        risk_pts = max(abs(price - sl), 1e-4)
        t1 = round(price + 1.5 * risk_pts, 2) if is_bull else round(price - 1.5 * risk_pts, 2)
        t2 = round(price + 2.5 * risk_pts, 2) if is_bull else round(price - 2.5 * risk_pts, 2)

        chain_df, expiry = _get_chain(idx_name)
        chain_summary = analyze_chain(chain_df, spot_override=price, prev_spot=prev_c)
        opt_plan = select_option_strategy(
            symbol=idx_name,
            side=side_code,
            spot=price,
            chain_df=chain_df,
            expiry=expiry,
            prefer_spread=True,
        )

        index_trades.append({
            "symbol": idx_name,
            "side": side_label,
            "setup_kind": f"{MODE} Index {'Bullish' if is_bull else 'Bearish'}",
            "sector": "INDEX",
            "close": round(price, 2),
            "change_pct": chg_pct,
            "rsi": round(float(m["rsi"]), 1),
            "vol_ratio": round(float(m["vr"]), 2),
            "entry": round(price, 2),
            "stop_loss": sl,
            "target_1": t1,
            "target_2": t2,
            "score": round(float(m["rsi"]), 1),
            "opt_chain": chain_summary,
            "option_plan": opt_plan,
            "hedge_plan": opt_plan if opt_plan.get("available") else None,
            "reasons": [
                f"Price {'above' if is_bull else 'below'} EMA20 ({round(ema20, 2)}) with {chg_pct:+.2f}% move",
                chain_summary["oi_quadrant"] if chain_summary else "Live option chain not available",
            ],
            "timestamp": run_ts,
        })

    def _build_option_card(stock: dict, is_bull: bool) -> dict:
        sym = stock["symbol"]
        price = stock["price"]
        atr_val = stock["atr"]
        side_code = "CE" if is_bull else "PE"
        side_label = "BUY CALL (CE)" if is_bull else "BUY PUT (PE)"

        sl = round(price - 1.5 * atr_val, 2) if is_bull else round(price + 1.5 * atr_val, 2)
        risk_pts = max(abs(price - sl), 1e-4)
        t1 = round(price + 1.5 * risk_pts, 2) if is_bull else round(price - 1.5 * risk_pts, 2)
        t2 = round(price + 2.5 * risk_pts, 2) if is_bull else round(price - 2.5 * risk_pts, 2)

        chain_df, expiry = _get_chain(sym)
        chain_summary = analyze_chain(chain_df, spot_override=price, prev_spot=stock["prev_close"])
        opt_plan = select_option_strategy(
            symbol=sym,
            side=side_code,
            spot=price,
            chain_df=chain_df,
            expiry=expiry,
            prefer_spread=True,
        )

        score_val = stock["bull_score"] if is_bull else stock["bear_score"]
        reasons = [
            f"Score {score_val}/100 | RSI {stock['rsi']} | Vol {stock['vr']}x | VWAP ₹{stock['vwap']}",
            f"EMA200 history: {'1y/200+ bars' if stock['has_ema200_history'] else 'Short history (<200 bars)'}",
        ]
        if chain_summary:
            reasons.append(f"OI Structure: {chain_summary['oi_quadrant']} (PCR {chain_summary['oi_pcr']})")

        return {
            "symbol": sym,
            "side": side_label,
            "setup_kind": f"{MODE} {'Bullish' if is_bull else 'Bearish'} Option",
            "sector": stock["sector"],
            "close": round(price, 2),
            "change_pct": stock["change_pct"],
            "rsi": stock["rsi"],
            "vol_ratio": stock["vr"],
            "vwap": stock["vwap"],
            "entry": round(price, 2),
            "stop_loss": sl,
            "target_1": t1,
            "target_2": t2,
            "score": score_val,
            "opt_chain": chain_summary,
            "option_plan": opt_plan,
            "hedge_plan": opt_plan if opt_plan.get("available") else None,
            "reasons": reasons,
            "timestamp": run_ts,
        }

    options_bullish = [_build_option_card(s, True) for s in top_bulls]
    options_bearish = [_build_option_card(s, False) for s in top_bears]

    per_side_cap = max(1, MAX_OPEN_POSITIONS // 2) if MAX_OPEN_POSITIONS >= 2 else 1
    equity_intraday: list[dict] = []

    for s in top_bulls[:per_side_cap]:
        entry = s["price"]
        sl = round(entry - 0.75 * s["atr"], 2)
        risk_pts = max(entry - sl, entry * 0.005)
        t1 = round(entry + 1.5 * risk_pts, 2)
        t2 = round(entry + 2.5 * risk_pts, 2)
        sizing = _compute_equity_sizing(entry, sl)
        equity_intraday.append({
            "symbol": s["symbol"],
            "sector": s["sector"],
            "index_tag": s["index_tag"],
            "action": "BUY (LONG)",
            "entry": round(entry, 2),
            "chg": s["change_pct"],
            "stop_loss": sl,
            "target_1": t1,
            "target_2": t2,
            "rsi": s["rsi"],
            "volume_ratio": s["vr"],
            "vwap": s["vwap"],
            "score": s["bull_score"],
            "timestamp": run_ts,
            **sizing,
        })

    for s in top_bears[:per_side_cap]:
        entry = s["price"]
        sl = round(entry + 0.75 * s["atr"], 2)
        risk_pts = max(sl - entry, entry * 0.005)
        t1 = round(entry - 1.5 * risk_pts, 2)
        t2 = round(entry - 2.5 * risk_pts, 2)
        sizing = _compute_equity_sizing(entry, sl)
        equity_intraday.append({
            "symbol": s["symbol"],
            "sector": s["sector"],
            "index_tag": s["index_tag"],
            "action": "SELL (SHORT)",
            "entry": round(entry, 2),
            "chg": s["change_pct"],
            "stop_loss": sl,
            "target_1": t1,
            "target_2": t2,
            "rsi": s["rsi"],
            "volume_ratio": s["vr"],
            "vwap": s["vwap"],
            "score": s["bear_score"],
            "timestamp": run_ts,
            **sizing,
        })

    breadth_tone = "BULLISH" if ups > downs * 1.15 else ("BEARISH" if downs > ups * 1.15 else "MIXED")
    total_ideas = len(index_trades) + len(options_bullish) + len(options_bearish) + len(equity_intraday)

    all_ranked = sorted(
        options_bullish + options_bearish,
        key=lambda x: x.get("score", 0.0),
        reverse=True,
    )
    best_rated = (
        f"{all_ranked[0]['symbol']} ({all_ranked[0]['side']}) [Score {all_ranked[0]['score']}]"
        if all_ranked
        else "None"
    )

    payload = {
        "mode": MODE,
        "title": TITLE,
        "timestamp": run_ts,
        "summary": {
            "data_source": data_source,
            "nifty_spot": f"{nifty_spot:,.2f}" if nifty_spot > 0 else "—",
            "nifty_chg": nifty_chg_str,
            "nifty_rsi": nifty_rsi,
            "bnf_spot": f"{bnf_spot:,.2f}" if bnf_spot > 0 else "—",
            "bnf_chg": bnf_chg_str,
            "fin_spot": f"{fin_spot:,.2f}" if fin_spot > 0 else "—",
            "fin_chg": fin_chg_str,
            "breadth_ratio": f"{ups} Adv / {downs} Dec",
            "above_ema20": round((above_20 / max(1, scanned)) * 100),
            "above_ema50": round((above_50 / max(1, scanned)) * 100),
            "breadth_tone": breadth_tone,
            "ideas_count": total_ideas,
            "best_rated": best_rated,
            "capital": CAPITAL,
            "capital_configured": CAPITAL_CONFIGURED,
            "risk_pct": RISK_PCT,
            "max_allocation_pct": MAX_ALLOCATION_PCT,
            "leverage_enabled": ALLOW_MIS_LEVERAGE,
            "leverage_multiplier": MIS_LEVERAGE if ALLOW_MIS_LEVERAGE else 1.0,
        },
        "global_cues": global_cues,
        "index_view": {
            "NIFTY": {
                "spot": f"{nifty_spot:,.2f}" if nifty_spot > 0 else "—",
                "chg": nifty_chg_str,
                "rsi": nifty_rsi,
                "lifting": contributors["NIFTY"]["lifting"],
                "dragging": contributors["NIFTY"]["dragging"],
            },
            "BANKNIFTY": {
                "spot": f"{bnf_spot:,.2f}" if bnf_spot > 0 else "—",
                "chg": bnf_chg_str,
                "rsi": round(float(bnf_m.get("rsi") or 50.0)),
                "lifting": contributors["BANKNIFTY"]["lifting"],
                "dragging": contributors["BANKNIFTY"]["dragging"],
            },
        },
        "sectors": sector_list,
        "index_trades": index_trades,
        "setups": {
            "bullish": options_bullish,
            "bearish": options_bearish,
        },
        "options_bullish": options_bullish,
        "options_bearish": options_bearish,
        "equity_intraday": equity_intraday,
        "intraday_bullish": [x for x in equity_intraday if x["action"] == "BUY (LONG)"],
        "intraday_bearish": [x for x in equity_intraday if x["action"] == "SELL (SHORT)"],
    }

    evaluate_signals_chronologically(candles_by_symbol=candles)
    log_signals(payload)
    payload["walk_forward"] = walk_forward_summary()

    send_alert(payload)
    logging.info("Dispatched %d setups using %s", total_ideas, data_source)
    return payload


if __name__ == "__main__":
    run_screener()
