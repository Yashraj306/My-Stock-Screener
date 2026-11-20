import datetime as dt
import logging
import math
import os
import warnings
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

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
    MAX_PORTFOLIO_RISK_PCT,
    MAX_SECTOR_POSITIONS,
    MIN_DAILY_TURNOVER_RS,
    MIS_LEVERAGE,
    MODE,
    RISK_PCT,
    SWING,
    TITLE,
)
from market import (
    SECTOR_MAP,
    compute_point_contributors,
    compute_relative_strength,
    compute_sector_strength,
    fetch_global_cues,
)
from notify import send_alert
from option_strategy import select_option_strategy
from options_data import chain_upstox
from report import render_html_report
from tech import compute_indicators
from tracker import evaluate_signals_chronologically, log_signals, walk_forward_summary
from universe import INDEX_MAP, LOT_SIZES, STOCKS_UNIVERSE, eq_keys

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
UPSTOX_ACCESS_TOKEN = (
    os.getenv("UPSTOX_ACCESS_TOKEN")
    or os.getenv("UPSTOX_TOKEN")
    or ""
).strip()

UPSTOX_BASE_V2 = "https://api.upstox.com/v2"

YF_SYMBOL_MAP = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "FINNIFTY": "^CNXFIN",
    "SENSEX": "^BSESN",
    "M&M": "M&M.NS",
}


def _yf_ticker(sym: str) -> str:
    return YF_SYMBOL_MAP.get(sym, f"{sym}.NS")


def _normalize_df(df: pd.DataFrame) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    work = df.copy()
    if isinstance(work.columns, pd.MultiIndex):
        work.columns = [c[0] for c in work.columns]
    work.columns = [str(c).strip().capitalize() for c in work.columns]

    if "Date" not in work.columns:
        if isinstance(work.index, pd.DatetimeIndex):
            work = work.reset_index().rename(columns={work.columns[0]: "Date"})
        elif "Datetime" in work.columns:
            work = work.rename(columns={"Datetime": "Date"})
        else:
            return None

    needed = ["Date", "Open", "High", "Low", "Close"]
    if not all(c in work.columns for c in needed):
        return None
    if "Volume" not in work.columns:
        work["Volume"] = 0.0

    work["Date"] = pd.to_datetime(work["Date"], errors="coerce")
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        work[col] = pd.to_numeric(work[col], errors="coerce")

    work = work.dropna(subset=["Date", "Open", "High", "Low", "Close"]).sort_values("Date").drop_duplicates("Date")
    work = work[work["Close"] > 0]
    if len(work) < 20:
        return None
    work["Date"] = work["Date"].dt.strftime("%Y-%m-%d")
    return work.reset_index(drop=True)


def _fetch_upstox_1y(instrument_key: str) -> pd.DataFrame | None:
    if not UPSTOX_ACCESS_TOKEN or not instrument_key:
        return None
    end_date = dt.datetime.now(IST).date()
    start_date = end_date - dt.timedelta(days=370)
    encoded = quote(instrument_key, safe="")
    url = f"{UPSTOX_BASE_V2}/historical-candle/{encoded}/day/{end_date.isoformat()}/{start_date.isoformat()}"
    headers = {"Authorization": f"Bearer {UPSTOX_ACCESS_TOKEN}", "Accept": "application/json"}
    try:
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code != 200:
            return None
        candles = ((resp.json() or {}).get("data") or {}).get("candles") or []
        if not candles:
            return None
        rows = []
        for c in candles:
            if len(c) >= 6:
                rows.append({
                    "Date": str(c[0])[:10],
                    "Open": c[1],
                    "High": c[2],
                    "Low": c[3],
                    "Close": c[4],
                    "Volume": c[5],
                })
        return _normalize_df(pd.DataFrame(rows))
    except Exception as exc:
        logging.debug("Upstox 1y fetch failed for %s: %s", instrument_key, exc)
        return None


def _fetch_yf_1y(symbol: str) -> pd.DataFrame | None:
    ticker = _yf_ticker(symbol)
    try:
        df = yf.download(ticker, period="1y", interval="1d", progress=False, timeout=12)
        norm = _normalize_df(df)
        if norm is not None:
            return norm
        t = yf.Ticker(ticker)
        hist = t.history(period="1y", interval="1d")
        return _normalize_df(hist)
    except Exception as exc:
        logging.debug("yfinance fetch failed for %s (%s): %s", symbol, ticker, exc)
        return None


def load_candles_1y() -> tuple[dict[str, pd.DataFrame], str]:
    keys = eq_keys()
    candles: dict[str, pd.DataFrame] = {}
    upstox_hits = 0

    def _worker(sym: str):
        df = _fetch_upstox_1y(keys.get(sym, ""))
        if df is not None and not df.empty:
            return sym, df, "UPSTOX"
        yf_df = _fetch_yf_1y(sym)
        return sym, yf_df, "YF"

    with ThreadPoolExecutor(max_workers=6) as pool:
        for sym, df, src in pool.map(_worker, list(keys.keys())):
            if df is not None and not df.empty:
                candles[sym] = df
                if src == "UPSTOX":
                    upstox_hits += 1

    if upstox_hits > 0 and upstox_hits >= max(1, len(candles) // 2):
        source_label = "UPSTOX_V2_HIST_1Y"
    elif upstox_hits > 0:
        source_label = "UPSTOX_V2 + YFINANCE_1Y"
    else:
        source_label = "YFINANCE_1Y"
    return candles, source_label


def _compute_oi_target(chain_summary: dict | None, entry: float, is_bull: bool) -> float | None:
    if not chain_summary or entry <= 0:
        return None
    candidates = []
    for key in ("resistance_strike", "support_strike", "max_pain"):
        val = chain_summary.get(key)
        try:
            fval = float(val)
            if fval > 0:
                candidates.append(fval)
        except (TypeError, ValueError):
            continue
    if is_bull:
        ahead = [x for x in candidates if x > entry]
        return round(min(ahead), 2) if ahead else None
    below = [x for x in candidates if x < entry]
    return round(max(below), 2) if below else None


def _compute_equity_sizing(entry: float, stop_loss: float) -> dict:
    if not CAPITAL_CONFIGURED or CAPITAL <= 0:
        return {
            "qty": 0,
            "exposure": 0.0,
            "est_margin": 0.0,
            "max_risk": 0.0,
            "sizing_status": "CAPITAL NOT CONFIGURED",
        }
    risk_per_share = abs(entry - stop_loss)
    if entry <= 0 or risk_per_share <= 0:
        return {
            "qty": 0,
            "exposure": 0.0,
            "est_margin": 0.0,
            "max_risk": 0.0,
            "sizing_status": "INVALID SL / ENTRY",
        }

    lev_mult = MIS_LEVERAGE if (ALLOW_MIS_LEVERAGE and MIS_LEVERAGE > 1.0) else 1.0
    max_risk_budget = CAPITAL * RISK_PCT
    max_notional_by_alloc = (CAPITAL * MAX_ALLOCATION_PCT) * lev_mult

    qty_by_risk = math.floor(max_risk_budget / risk_per_share)
    qty_by_alloc = math.floor(max_notional_by_alloc / entry)
    qty = max(min(qty_by_risk, qty_by_alloc), 0)

    if qty <= 0:
        return {
            "qty": 0,
            "exposure": 0.0,
            "est_margin": 0.0,
            "max_risk": 0.0,
            "sizing_status": "SIZE TOO SMALL FOR RISK CAP",
        }

    exposure = round(qty * entry, 2)
    est_margin = round(exposure / lev_mult, 2)
    max_risk = round(qty * risk_per_share, 2)
    lev_label = f"MIS {lev_mult:g}x" if lev_mult > 1.0 else "1x Cash"
    return {
        "qty": qty,
        "exposure": exposure,
        "est_margin": est_margin,
        "max_risk": max_risk,
        "sizing_status": f"{lev_label} Assumed",
    }


def _apply_portfolio_risk_caps(items: list[dict], max_items: int) -> list[dict]:
    selected: list[dict] = []
    sector_counts: dict[str, int] = {}
    total_risk = 0.0
    portfolio_risk_cap = CAPITAL * MAX_PORTFOLIO_RISK_PCT if CAPITAL_CONFIGURED else float("inf")

    for item in items:
        if len(selected) >= max_items:
            break
        sec = str(item.get("sector") or "OTHER").upper()
        if sector_counts.get(sec, 0) >= MAX_SECTOR_POSITIONS:
            continue
        trade_risk = float(item.get("max_risk") or 0.0)
        if CAPITAL_CONFIGURED and trade_risk > 0 and (total_risk + trade_risk) > portfolio_risk_cap:
            continue
        selected.append(item)
        sector_counts[sec] = sector_counts.get(sec, 0) + 1
        total_risk += trade_risk
    return selected
def _build_stock_setup(sym: str, ind: dict, rs_info: dict, is_bull: bool, chain_bundle: dict | None, strict_filters: bool = True) -> dict | None:
    close = float(ind["close"])
    atr = float(ind.get("atr") or max(close * 0.015, 1.0))
    vwap = float(ind.get("vwap") or close)
    ema20 = float(ind.get("ema20") or close)
    ema50 = float(ind.get("ema50") or close)
    rsi = float(ind.get("rsi") or 50.0)
    vol_ratio = float(ind.get("vol_ratio") or 1.0)
    chg_pct = float(ind.get("change_pct") or 0.0)
    avg_turnover = float(ind.get("avg_turnover") or 0.0)

    min_turn = MIN_DAILY_TURNOVER_RS if strict_filters else 0.0
    min_vol = 0.80 if strict_filters else 0.30

    if avg_turnover < min_turn:
        return None
    if vol_ratio < min_vol:
        return None

    if strict_filters:
        if is_bull:
            if chg_pct < -0.50 or close < ema20 or close < vwap:
                return None
        else:
            if chg_pct > 0.50 or close > ema20 or close > vwap:
                return None

    if is_bull:
        sl = round(max(close - 1.5 * atr, min(ema20, vwap) * 0.992), 2)
        pa_t1 = round(close + 1.8 * atr, 2)
        pa_t2 = round(close + 3.0 * atr, 2)
    else:
        sl = round(min(close + 1.5 * atr, max(ema20, vwap) * 1.008), 2)
        pa_t1 = round(max(close - 1.8 * atr, 0.05), 2)
        pa_t2 = round(max(close - 3.0 * atr, 0.05), 2)

    if (is_bull and sl >= close) or (not is_bull and sl <= close):
        return None

    chain_summary = (chain_bundle or {}).get("summary") or {}
    chain_df = (chain_bundle or {}).get("df")
    expiry = (chain_bundle or {}).get("expiry")
    oi_target = _compute_oi_target(chain_summary, close, is_bull=is_bull)

    sizing = _compute_equity_sizing(close, sl)
    opt_plan = select_option_strategy(
        symbol=sym,
        side="CE" if is_bull else "PE",
        spot=close,
        chain_df=chain_df,
        expiry=expiry,
        prefer_spread=True,
    )

    rs_score = float((rs_info or {}).get("rs_score") or 0.0)
    base_score = float(ind.get("bull_score" if is_bull else "bear_score") or 50.0)
    adj_score = round(max(0.0, min(100.0, 0.8 * base_score + 0.2 * (50.0 + rs_score * 2.0))), 2)

    return {
        "symbol": sym,
        "sector": SECTOR_MAP.get(sym, "OTHER"),
        "side": "BULLISH" if is_bull else "BEARISH",
        "action": "BUY (LONG)" if is_bull else "SELL (SHORT)",
        "entry": round(close, 2),
        "close": round(close, 2),
        "spot": round(close, 2),
        "change_pct": round(chg_pct, 2),
        "stop_loss": sl,
        "target_1": pa_t1,
        "target_2": pa_t2,
        "pa_target_1": pa_t1,
        "pa_target_2": pa_t2,
        "oi_target": oi_target,
        "rsi": round(rsi, 1),
        "vol_ratio": round(vol_ratio, 2),
        "vwap": round(vwap, 2),
        "ema20": round(ema20, 2),
        "ema50": round(ema50, 2),
        "rs_score": rs_score,
        "score": adj_score,
        "history_label": ind.get("history_label", "1y"),
        "chain_summary": chain_summary,
        "option_plan": opt_plan,
        **sizing,
    }


def _build_index_trades(candles: dict[str, pd.DataFrame], vix_val: float | None) -> list[dict]:
    trades = []
    for idx_sym in ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]:
        df = candles.get(idx_sym)
        if df is None or df.empty:
            continue
        ind = compute_indicators(df)
        if not ind:
            continue
        spot = float(ind["close"])
        atr = float(ind.get("atr") or max(spot * 0.01, 1.0))
        ema20 = float(ind.get("ema20") or spot)
        chg_pct = float(ind.get("change_pct") or 0.0)

        is_bull = spot >= ema20 and chg_pct >= -0.35
        chain_df, expiry = None, None
        chain_summary = {}
        try:
            raw_chain = chain_upstox(idx_sym, spot)
            if isinstance(raw_chain, tuple):
                chain_df, expiry = raw_chain[0], raw_chain[1] if len(raw_chain) > 1 else None
            elif isinstance(raw_chain, dict):
                chain_df = raw_chain.get("df")
                expiry = raw_chain.get("expiry")
            if chain_df is not None and not getattr(chain_df, "empty", True):
                chain_summary = analyze_chain(chain_df, spot) or {}
        except Exception as exc:
            logging.debug("Index option chain fetch failed for %s: %s", idx_sym, exc)

        plan = select_option_strategy(
            symbol=idx_sym,
            side="CE" if is_bull else "PE",
            spot=spot,
            chain_df=chain_df,
            expiry=expiry,
            prefer_spread=True,
        )

        sl = round(spot - 1.5 * atr, 2) if is_bull else round(spot + 1.5 * atr, 2)
        pa_t1 = round(spot + 1.5 * atr, 2) if is_bull else round(max(spot - 1.5 * atr, 0.0), 2)
        pa_t2 = round(spot + 2.5 * atr, 2) if is_bull else round(max(spot - 2.5 * atr, 0.0), 2)
        oi_target = _compute_oi_target(chain_summary, spot, is_bull=is_bull)

        lot_size = int(plan.get("lot_size") or LOT_SIZES.get(idx_sym, 0))
        max_loss_lot = plan.get("max_loss_lot")
        sizing_status = "CAPITAL NOT CONFIGURED" if not CAPITAL_CONFIGURED else "1 Lot Risk Cap"

        trades.append({
            "symbol": idx_sym,
            "sector": "INDEX",
            "side": "BULLISH" if is_bull else "BEARISH",
            "entry": round(spot, 2),
            "close": round(spot, 2),
            "spot": round(spot, 2),
            "change_pct": round(chg_pct, 2),
            "stop_loss": sl,
            "target_1": pa_t1,
            "target_2": pa_t2,
            "pa_target_1": pa_t1,
            "pa_target_2": pa_t2,
            "oi_target": oi_target,
            "qty": lot_size,
            "est_margin": max_loss_lot,
            "exposure": max_loss_lot,
            "max_risk": max_loss_lot,
            "sizing_status": sizing_status,
            "chain_summary": chain_summary,
            "option_plan": plan,
        })
    return trades


def run_screener():
    candles, data_source = load_candles_1y()
    logging.info("Loaded candles for %d symbols using %s", len(candles), data_source)
    global_cues = fetch_global_cues()
    vix_val = None
    for c in global_cues:
        if "VIX" in str(c.get("name", "")).upper() and c.get("raw_val") is not None:
            vix_val = float(c["raw_val"])
            break

    sectors = compute_sector_strength(candles)
    rs_map = compute_relative_strength(candles)

    indicators_by_sym: dict[str, dict] = {}
    adv, dec, above20, above50, total_stocks = 0, 0, 0, 0, 0

    for sym in STOCKS_UNIVERSE:
        df = candles.get(sym)
        if df is None or df.empty:
            continue
        ind = compute_indicators(df)
        if not ind:
            continue
        close = float(ind["close"])
        high = float(df["High"].iloc[-1])
        low = float(df["Low"].iloc[-1])
        vol = float(df["Volume"].iloc[-1])
        typical = (high + low + close) / 3.0
        vol_20 = pd.to_numeric(df["Volume"], errors="coerce").tail(20).mean()
        typ_20 = ((pd.to_numeric(df["High"], errors="coerce") + pd.to_numeric(df["Low"], errors="coerce") + pd.to_numeric(df["Close"], errors="coerce")) / 3.0).tail(20).mean()
        ind["vwap"] = round(typical, 2)
        ind["avg_turnover"] = float((vol_20 or vol) * (typ_20 or close))
        indicators_by_sym[sym] = ind

        total_stocks += 1
        chg = float(ind.get("change_pct") or 0.0)
        if chg > 0:
            adv += 1
        elif chg < 0:
            dec += 1
        if close >= float(ind.get("ema20") or close):
            above20 += 1
        if close >= float(ind.get("ema50") or close):
            above50 += 1

    bull_raw, bear_raw = [], []
    chain_cache: dict[str, dict] = {}

    def _get_chain(sym: str, spot: float) -> dict:
        if sym in chain_cache:
            return chain_cache[sym]
        bundle = {"df": None, "expiry": None, "summary": {}}
        try:
            raw = chain_upstox(sym, spot)
            if isinstance(raw, tuple):
                cdf, exp = raw[0], raw[1] if len(raw) > 1 else None
            elif isinstance(raw, dict):
                cdf, exp = raw.get("df"), raw.get("expiry")
            else:
                cdf, exp = None, None
            csum = analyze_chain(cdf, spot) if cdf is not None and not getattr(cdf, "empty", True) else {}
            bundle = {"df": cdf, "expiry": exp, "summary": csum or {}}
        except Exception as exc:
            logging.debug("Stock chain error for %s: %s", sym, exc)
        chain_cache[sym] = bundle
        return bundle

    for sym, ind in indicators_by_sym.items():
        b_score = float(ind.get("bull_score") or 0.0)
        s_score = float(ind.get("bear_score") or 0.0)
        rs_info = rs_map.get(sym, {})
        if b_score >= s_score and b_score >= 55.0:
            cb = _get_chain(sym, float(ind["close"]))
            setup = _build_stock_setup(sym, ind, rs_info, True, cb, strict_filters=True)
            if setup:
                bull_raw.append(setup)
        elif s_score > b_score and s_score >= 55.0:
            cb = _get_chain(sym, float(ind["close"]))
            setup = _build_stock_setup(sym, ind, rs_info, False, cb, strict_filters=True)
            if setup:
                bear_raw.append(setup)

    if not bull_raw and not bear_raw and indicators_by_sym:
        for sym, ind in indicators_by_sym.items():
            b_score = float(ind.get("bull_score") or 0.0)
            s_score = float(ind.get("bear_score") or 0.0)
            rs_info = rs_map.get(sym, {})
            is_bull = b_score >= s_score
            cb = _get_chain(sym, float(ind["close"]))
            setup = _build_stock_setup(sym, ind, rs_info, is_bull, cb, strict_filters=False)
            if setup:
                (bull_raw if is_bull else bear_raw).append(setup)

    bull_raw.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    bear_raw.sort(key=lambda x: x.get("score", 0.0), reverse=True)

    options_bullish = _apply_portfolio_risk_caps(bull_raw, max_items=5)
    options_bearish = _apply_portfolio_risk_caps(bear_raw, max_items=5)

    intraday_pool = sorted(
        bull_raw[:4] + bear_raw[:4],
        key=lambda x: x.get("score", 0.0),
        reverse=True,
    )
    equity_intraday = _apply_portfolio_risk_caps(intraday_pool, max_items=MAX_OPEN_POSITIONS)
    index_trades = _build_index_trades(candles, vix_val=vix_val)

    def _idx_fmt(sym: str):
        df = candles.get(sym)
        if df is None or len(df) < 2:
            return "—", "—"
        c_last = float(df["Close"].iloc[-1])
        c_prev = float(df["Close"].iloc[-2])
        chg = ((c_last / c_prev) - 1.0) * 100.0 if c_prev else 0.0
        return f"{c_last:,.2f}", f"{chg:+.2f}%"

    n_spot, n_chg = _idx_fmt("NIFTY")
    b_spot, b_chg = _idx_fmt("BANKNIFTY")
    f_spot, f_chg = _idx_fmt("FINNIFTY")

    breadth_tone = "BULLISH" if adv > dec else ("BEARISH" if dec > adv else "MIXED")
    above20_pct = round((above20 / total_stocks) * 100.0) if total_stocks else 0
    above50_pct = round((above50 / total_stocks) * 100.0) if total_stocks else 0

    total_ideas = len(index_trades) + len(options_bullish) + len(options_bearish) + len(equity_intraday)

    payload = {
        "title": TITLE,
        "summary": {
            "ideas_count": total_ideas,
            "breadth_tone": breadth_tone,
            "data_source": data_source,
            "nifty_spot": n_spot,
            "nifty_chg": n_chg,
            "bnf_spot": b_spot,
            "bnf_chg": b_chg,
            "fin_spot": f_spot,
            "fin_chg": f_chg,
            "breadth_ratio": f"{adv} Adv/{dec} Dec",
            "above_ema20": above20_pct,
            "above_ema50": above50_pct,
            "capital": CAPITAL,
            "capital_configured": CAPITAL_CONFIGURED,
            "risk_pct": RISK_PCT,
            "max_allocation_pct": MAX_ALLOCATION_PCT,
            "leverage_enabled": ALLOW_MIS_LEVERAGE,
            "leverage_multiplier": MIS_LEVERAGE,
        },
        "global_cues": global_cues,
        "sectors": sectors,
        "index_trades": index_trades,
        "setups": {
            "bullish": options_bullish,
            "bearish": options_bearish,
        },
        "options_bullish": options_bullish,
        "options_bearish": options_bearish,
        "equity_intraday": equity_intraday,
        "intraday_bullish": [x for x in equity_intraday if x.get("action") == "BUY (LONG)"],
        "intraday_bearish": [x for x in equity_intraday if x.get("action") == "SELL (SHORT)"],
    }

    evaluate_signals_chronologically(candles_by_symbol=candles)
    log_signals(payload)
    payload["walk_forward"] = walk_forward_summary()

    send_alert(payload)
    logging.info("Dispatched %d setups using %s", total_ideas, data_source)


if __name__ == "__main__":
    run_screener()
