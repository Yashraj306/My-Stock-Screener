import datetime as dt
import inspect
import json
import logging
import os
import tempfile
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
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
    MAX_PORTFOLIO_RISK_PCT,
    MAX_SECTOR_POSITIONS,
    MIN_DAILY_TURNOVER_RS,
    MIS_LEVERAGE,
    MODE,
    RISK_PCT,
    TITLE,
)
from market import (
    SECTOR_MAP,
    compute_relative_strength,
    compute_sector_strength,
    fetch_global_cues,
)
from notify import send_alert
from option_strategy import select_option_strategy
from options_data import chain_upstox
from setups import position_size
from tech import clean_ohlcv, compute_indicators
from tracker import evaluate_signals_chronologically, log_signals, walk_forward_summary
from universe import LOT_SIZES, STOCKS_UNIVERSE, eq_keys

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
UPSTOX_ACCESS_TOKEN = (
    os.getenv("UPSTOX_ACCESS_TOKEN")
    or os.getenv("UPSTOX_TOKEN")
    or ""
).strip()
UPSTOX_BASE_V2 = "https://api.upstox.com/v2"

_YF_CACHE_DIR = os.path.join(tempfile.gettempdir(), "py-yfinance")
os.makedirs(_YF_CACHE_DIR, exist_ok=True)
try:
    yf.set_tz_cache_location(_YF_CACHE_DIR)
except Exception:
    pass

YF_SYMBOL_MAP = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "FINNIFTY": "^CNXFIN",
    "SENSEX": "^BSESN",
    "M&M": "M&M.NS",
}

_WARNED_UPSTOX_STATUS: set[int] = set()
_WARNED_YF_COUNT = 0


def _yf_ticker(sym: str) -> str:
    return YF_SYMBOL_MAP.get(sym, f"{sym}.NS")


def _clean_yf_duplicates(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [str(c[0]) for c in out.columns]
    out = out.loc[:, ~out.columns.duplicated()]
    if out.index.duplicated().any():
        out = out[~out.index.duplicated(keep="last")]
    return out


def _compute_cumulative_vwap(df: pd.DataFrame) -> float | None:
    high = pd.to_numeric(df["High"], errors="coerce")
    low = pd.to_numeric(df["Low"], errors="coerce")
    close = pd.to_numeric(df["Close"], errors="coerce")
    vol = pd.to_numeric(df["Volume"], errors="coerce").fillna(0.0)

    typical = (high + low + close) / 3.0
    cum_vol = vol.cumsum()
    cum_pv = (typical * vol).cumsum()

    last_cum_vol = float(cum_vol.iloc[-1]) if len(cum_vol) else 0.0
    if last_cum_vol > 0.0 and pd.notna(cum_pv.iloc[-1]):
        return round(float(cum_pv.iloc[-1]) / last_cum_vol, 2)
    return None


def _fetch_upstox_1y(instrument_key: str) -> pd.DataFrame | None:
    if not UPSTOX_ACCESS_TOKEN:
        logging.warning("Upstox token is empty for %s", instrument_key)
        return None
    if not instrument_key:
        return None

    end_date = dt.datetime.now(IST).date()
    start_date = end_date - dt.timedelta(days=370)
    encoded = quote(instrument_key, safe="")
    url = f"{UPSTOX_BASE_V2}/historical-candle/{encoded}/day/{end_date.isoformat()}/{start_date.isoformat()}"
    headers = {"Authorization": f"Bearer {UPSTOX_ACCESS_TOKEN}", "Accept": "application/json"}
    try:
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code != 200:
            if resp.status_code not in _WARNED_UPSTOX_STATUS:
                _WARNED_UPSTOX_STATUS.add(resp.status_code)
                logging.warning(
                    "Upstox %s HTTP %s: %s",
                    instrument_key,
                    resp.status_code,
                    (resp.text or "")[:120],
                )
            return None
        candles = ((resp.json() or {}).get("data") or {}).get("candles") or []
        if not candles:
            logging.warning("Upstox %s returned empty candles", instrument_key)
            return None
        rows = [
            {
                "Date": str(c[0])[:10],
                "Open": c[1],
                "High": c[2],
                "Low": c[3],
                "Close": c[4],
                "Volume": c[5],
            }
            for c in candles
            if len(c) >= 6
        ]
        cleaned = clean_ohlcv(pd.DataFrame(rows))
        if cleaned is None:
            logging.warning("Upstox %s failed OHLCV normalization", instrument_key)
            return None
        if len(cleaned) < 20:
            return None
        return cleaned
    except Exception as exc:
        logging.warning("Upstox %s exception: %s", instrument_key, exc)
        return None


def _fetch_yf_1y(symbol: str) -> pd.DataFrame | None:
    global _WARNED_YF_COUNT
    ticker = _yf_ticker(symbol)
    try:
        df = yf.download(
            ticker,
            period="1y",
            interval="1d",
            progress=False,
            threads=False,
            timeout=12,
            auto_adjust=False,
        )
        cleaned_raw = _clean_yf_duplicates(df)
        norm = clean_ohlcv(cleaned_raw) if cleaned_raw is not None else None
        if norm is not None and len(norm) >= 20:
            return norm

        hist = yf.Ticker(ticker).history(period="1y", interval="1d", auto_adjust=False)
        cleaned_hist = _clean_yf_duplicates(hist)
        norm2 = clean_ohlcv(cleaned_hist) if cleaned_hist is not None else None
        if norm2 is not None and len(norm2) >= 20:
            return norm2

        if _WARNED_YF_COUNT < 5:
            _WARNED_YF_COUNT += 1
            logging.warning("YF %s returned empty/insufficient data", ticker)
        return None
    except Exception as exc:
        if _WARNED_YF_COUNT < 5:
            _WARNED_YF_COUNT += 1
            logging.warning("YF %s err: %s", ticker, exc)
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


def _pick_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    lower_map = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        key = cand.strip().lower()
        if key in lower_map:
            return lower_map[key]
    for c in df.columns:
        cl = str(c).strip().lower()
        for cand in candidates:
            if cand.strip().lower() in cl:
                return c
    return None


def _build_session_guardrails(now_ist: dt.datetime) -> dict:
    hm = now_ist.hour * 60 + now_ist.minute
    in_calibration = (9 * 60 + 15) <= hm <= (9 * 60 + 25)
    after_cutoff = hm >= (14 * 60 + 30)
    in_optimal_window = (10 * 60) <= hm <= (11 * 60)

    if in_calibration:
        window_status = "CALIBRATION (9:15–9:25 AM)"
        window_note = "9:15–9:25 AM calibration phase: observe opening stabilization before placing orders."
    elif after_cutoff:
        window_status = "POST-2:30 PM CUTOFF"
        window_note = "After 2:30 PM cutoff: avoid fresh intraday option buying due to late-session theta decay."
    elif in_optimal_window:
        window_status = "OPTIMAL WINDOW (10:00–11:00 AM)"
        window_note = "10:00–11:00 AM window: Greeks and liquidity typically stabilize after opening range."
    else:
        window_status = "STANDARD SESSION"
        window_note = "Check WTT/WTB shift %, EOS/EOR levels, and implied volatility before entry."

    return {
        "window_status": window_status,
        "window_note": window_note,
        "in_calibration": in_calibration,
        "after_cutoff": after_cutoff,
        "in_optimal_window": in_optimal_window,
        "rules": [
            "9:15–9:25 AM: Initial calibration window; verify strike & OI stabilization.",
            "2:30 PM onwards: Cutoff rule for fresh intraday option buying & late-session risk.",
            "WTT / WTB: Secondary strike ratio approaching 100% signals structural shift; reversal if ratio decays.",
            "Dynamic SL: Anchor stop-loss to option structure/invalidations rather than fixed arbitrary points.",
        ],
    }


def _build_option_chain_analytics(chain_df: Any, spot: float, lot_size: int = 1) -> dict:
    if chain_df is None or getattr(chain_df, "empty", True):
        return {
            "rows": [],
            "strong_support": None,
            "weak_support": None,
            "strong_resistance": None,
            "weak_resistance": None,
            "support_shift": "STRONG",
            "resistance_shift": "STRONG",
            "support_shift_pct": 0.0,
            "resistance_shift_pct": 0.0,
            "sentiment_state": "NEUTRAL / NO CHAIN",
            "eos": None,
            "eor": None,
            "eos_minus_1": None,
            "eos_plus_1": None,
            "eor_minus_1": None,
            "eor_plus_1": None,
        }

    df = chain_df.copy()
    strike_col = _pick_col(df, ["strike", "strike_price", "strikePrice"])
    ce_oi_col = _pick_col(df, ["ce_oi", "call_oi", "ceOi", "oi_ce"])
    pe_oi_col = _pick_col(df, ["pe_oi", "put_oi", "peOi", "oi_pe"])
    ce_chg_col = _pick_col(df, ["ce_chg_oi", "ce_oi_chg", "call_oi_change", "ceChgOi", "chg_oi_ce"])
    pe_chg_col = _pick_col(df, ["pe_chg_oi", "pe_oi_chg", "put_oi_change", "peChgOi", "chg_oi_pe"])
    ce_ltp_col = _pick_col(df, ["ce_ltp", "call_ltp", "ceLtp", "ltp_ce"])
    pe_ltp_col = _pick_col(df, ["pe_ltp", "put_ltp", "peLtp", "ltp_pe"])
    ce_vol_col = _pick_col(df, ["ce_vol", "ce_volume", "call_volume", "ceVol"])
    pe_vol_col = _pick_col(df, ["pe_vol", "pe_volume", "put_volume", "peVol"])

    if not strike_col:
        return {
            "rows": [],
            "strong_support": None,
            "weak_support": None,
            "strong_resistance": None,
            "weak_resistance": None,
            "support_shift": "STRONG",
            "resistance_shift": "STRONG",
            "support_shift_pct": 0.0,
            "resistance_shift_pct": 0.0,
            "sentiment_state": "NEUTRAL / NO CHAIN",
            "eos": None,
            "eor": None,
            "eos_minus_1": None,
            "eos_plus_1": None,
            "eor_minus_1": None,
            "eor_plus_1": None,
        }

    eff_lot = max(int(lot_size or 1), 1)
    rows: list[dict] = []

    for _, r in df.iterrows():
        try:
            strike = float(r[strike_col])
        except (TypeError, ValueError):
            continue
        if strike <= 0:
            continue

        def _f(col):
            if not col:
                return 0.0
            try:
                v = float(r[col])
                return 0.0 if pd.isna(v) else v
            except (TypeError, ValueError):
                return 0.0

        ce_oi = _f(ce_oi_col)
        pe_oi = _f(pe_oi_col)
        ce_chg = _f(ce_chg_col)
        pe_chg = _f(pe_chg_col)
        ce_ltp = _f(ce_ltp_col)
        pe_ltp = _f(pe_ltp_col)
        ce_vol = _f(ce_vol_col)
        pe_vol = _f(pe_vol_col)

        prev_ce_oi = max(ce_oi - ce_chg, 1.0)
        prev_pe_oi = max(pe_oi - pe_chg, 1.0)
        ce_chg_pct = round((ce_chg / prev_ce_oi) * 100.0, 1) if ce_oi > 0 else 0.0
        pe_chg_pct = round((pe_chg / prev_pe_oi) * 100.0, 1) if pe_oi > 0 else 0.0

        ce_contracts = int(round(ce_chg / eff_lot)) if eff_lot > 1 else int(round(ce_chg))
        pe_contracts = int(round(pe_chg / eff_lot)) if eff_lot > 1 else int(round(pe_chg))

        rows.append({
            "strike": round(strike, 2),
            "is_atm": False,
            "ce_oi": int(round(ce_oi)),
            "pe_oi": int(round(pe_oi)),
            "ce_chg_oi": int(round(ce_chg)),
            "pe_chg_oi": int(round(pe_chg)),
            "ce_chg_pct": ce_chg_pct,
            "pe_chg_pct": pe_chg_pct,
            "ce_contracts": ce_contracts,
            "pe_contracts": pe_contracts,
            "ce_ltp": round(ce_ltp, 2),
            "pe_ltp": round(pe_ltp, 2),
            "ce_vol": int(round(ce_vol)),
            "pe_vol": int(round(pe_vol)),
            "pe_strength_pct": 0.0,
            "ce_strength_pct": 0.0,
        })

    rows.sort(key=lambda x: x["strike"])
    if not rows:
        return {
            "rows": [],
            "strong_support": None,
            "weak_support": None,
            "strong_resistance": None,
            "weak_resistance": None,
            "support_shift": "STRONG",
            "resistance_shift": "STRONG",
            "support_shift_pct": 0.0,
            "resistance_shift_pct": 0.0,
            "sentiment_state": "NEUTRAL",
            "eos": None,
            "eor": None,
            "eos_minus_1": None,
            "eos_plus_1": None,
            "eor_minus_1": None,
            "eor_plus_1": None,
        }

    closest_idx = min(range(len(rows)), key=lambda i: abs(rows[i]["strike"] - spot))
    rows[closest_idx]["is_atm"] = True

    strike_step = 50.0
    if len(rows) >= 2:
        diffs = [round(rows[i + 1]["strike"] - rows[i]["strike"], 2) for i in range(len(rows) - 1) if rows[i + 1]["strike"] > rows[i]["strike"]]
        if diffs:
            strike_step = float(pd.Series(diffs).median())

    max_pe_row = max(rows, key=lambda x: x["pe_oi"])
    max_ce_row = max(rows, key=lambda x: x["ce_oi"])
    strong_sup = max_pe_row["strike"] if max_pe_row["pe_oi"] > 0 else None
    strong_res = max_ce_row["strike"] if max_ce_row["ce_oi"] > 0 else None

    base_pe = max(float(max_pe_row["pe_oi"]), 1.0)
    base_ce = max(float(max_ce_row["ce_oi"]), 1.0)

    for r in rows:
        r["pe_strength_pct"] = round((r["pe_oi"] / base_pe) * 100.0, 1) if max_pe_row["pe_oi"] > 0 else 0.0
        r["ce_strength_pct"] = round((r["ce_oi"] / base_ce) * 100.0, 1) if max_ce_row["ce_oi"] > 0 else 0.0

    other_pe = [r for r in rows if r["strike"] != strong_sup and r["pe_oi"] > 0]
    weak_sup_row = max(other_pe, key=lambda x: x["pe_oi"]) if other_pe else None
    weak_sup = weak_sup_row["strike"] if weak_sup_row else None
    sup_shift_pct = weak_sup_row["pe_strength_pct"] if weak_sup_row else 0.0

    support_shift = "STRONG"
    if weak_sup is not None and strong_sup is not None and sup_shift_pct >= 65.0:
        if weak_sup > strong_sup:
            support_shift = "WTT"
        elif weak_sup < strong_sup:
            support_shift = "WTB"

    other_ce = [r for r in rows if r["strike"] != strong_res and r["ce_oi"] > 0]
    weak_res_row = max(other_ce, key=lambda x: x["ce_oi"]) if other_ce else None
    weak_res = weak_res_row["strike"] if weak_res_row else None
    res_shift_pct = weak_res_row["ce_strength_pct"] if weak_res_row else 0.0

    resistance_shift = "STRONG"
    if weak_res is not None and strong_res is not None and res_shift_pct >= 65.0:
        if weak_res > strong_res:
            resistance_shift = "WTT"
        elif weak_res < strong_res:
            resistance_shift = "WTB"

    sup_ltp = float(max_pe_row["pe_ltp"] or 0.0)
    res_ltp = float(max_ce_row["ce_ltp"] or 0.0)
    eos = round(strong_sup - sup_ltp, 2) if strong_sup is not None else None
    eor = round(strong_res + res_ltp, 2) if strong_res is not None else None

    eos_minus_1 = round(eos - strike_step, 2) if eos is not None else None
    eos_plus_1 = round(eos + strike_step, 2) if eos is not None else None
    eor_minus_1 = round(eor - strike_step, 2) if eor is not None else None
    eor_plus_1 = round(eor + strike_step, 2) if eor is not None else None

    for r in rows:
        st = r["strike"]
        role = "NEUTRAL"
        impact = "Balanced strike flow; monitor OI buildup around spot."

        if strong_sup is not None and st == strong_sup:
            role = "STRONG_SUPPORT"
            impact = f"Strong Support ({r['pe_chg_pct']:+.1f}% OI chg, {r['pe_contracts']:+,} cntr): primary floor; EOS ~ {eos}."
        elif strong_res is not None and st == strong_res:
            role = "STRONG_RESISTANCE"
            impact = f"Strong Resistance ({r['ce_chg_pct']:+.1f}% OI chg, {r['ce_contracts']:+,} cntr): primary ceiling; EOR ~ {eor}."
        elif weak_sup is not None and st == weak_sup and r["pe_strength_pct"] >= 65.0:
            role = "WEAK_SUPPORT"
            dir_lbl = "WTT (upward shift)" if st > (strong_sup or st) else "WTB (downward shift)"
            impact = f"Weak Support / {dir_lbl} at {r['pe_strength_pct']:.1f}% of peak ({r['pe_chg_oi']:+,} OI): watch 100% shift or unwind."
        elif weak_res is not None and st == weak_res and r["ce_strength_pct"] >= 65.0:
            role = "WEAK_RESISTANCE"
            dir_lbl = "WTT (upward shift)" if st > (strong_res or st) else "WTB (downward shift)"
            impact = f"Weak Resistance / {dir_lbl} at {r['ce_strength_pct']:.1f}% of peak ({r['ce_chg_oi']:+,} OI): watch breakout or shift."
        elif r["pe_chg_oi"] < 0 and abs(r["pe_chg_pct"]) >= 15.0:
            impact = f"Put unwinding ({r['pe_chg_pct']:+.1f}%): support weakening near {st}."
        elif r["ce_chg_oi"] < 0 and abs(r["ce_chg_pct"]) >= 15.0:
            impact = f"Call unwinding ({r['ce_chg_pct']:+.1f}%): resistance easing near {st}."

        r["zone_role"] = role
        r["impact_note"] = impact

    if support_shift == "WTT" and resistance_shift in {"WTT", "STRONG"}:
        sentiment_state = "BULLISH / WTT PRESSURE"
    elif support_shift == "WTB" or resistance_shift == "WTB":
        sentiment_state = "BEARISH / WTB PRESSURE"
    elif support_shift != "STRONG" and resistance_shift != "STRONG" and support_shift != resistance_shift:
        sentiment_state = "STATE OF CONFUSION (SOC)"
    else:
        sentiment_state = "RANGE-BOUND (EOS–EOR)"

    return {
        "rows": rows,
        "strong_support": strong_sup,
        "weak_support": weak_sup,
        "strong_resistance": strong_res,
        "weak_resistance": weak_res,
        "support_shift": support_shift,
        "resistance_shift": resistance_shift,
        "support_shift_pct": sup_shift_pct,
        "resistance_shift_pct": res_shift_pct,
        "sentiment_state": sentiment_state,
        "strike_step": strike_step,
        "eos": eos,
        "eor": eor,
        "eos_minus_1": eos_minus_1,
        "eos_plus_1": eos_plus_1,
        "eor_minus_1": eor_minus_1,
        "eor_plus_1": eor_plus_1,
    }
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


def _select_capped_items(items: list[dict], max_items: int, enforce_risk_cap: bool = True) -> list[dict]:
    selected: list[dict] = []
    sector_counts: dict[str, int] = {}
    total_risk = 0.0
    portfolio_risk_cap = CAPITAL * MAX_PORTFOLIO_RISK_PCT if (CAPITAL_CONFIGURED and enforce_risk_cap) else float("inf")

    for item in items:
        if len(selected) >= max_items:
            break
        sec = str(item.get("sector") or "OTHER").upper()
        if sector_counts.get(sec, 0) >= MAX_SECTOR_POSITIONS:
            continue
        trade_risk = float(item.get("max_risk") or 0.0)
        if enforce_risk_cap and CAPITAL_CONFIGURED and trade_risk > 0 and (total_risk + trade_risk) > portfolio_risk_cap:
            continue
        selected.append(item)
        sector_counts[sec] = sector_counts.get(sec, 0) + 1
        total_risk += trade_risk
    return selected


def _safe_position_size(entry: float, stop_loss: float, lot_size: int = 1) -> dict:
    try:
        sig = inspect.signature(position_size)
        params = sig.parameters
        kwargs = {}
        if "entry" in params:
            kwargs["entry"] = entry
        if "stop_loss" in params:
            kwargs["stop_loss"] = stop_loss
        elif "sl" in params:
            kwargs["sl"] = stop_loss
        if "lot_size" in params:
            kwargs["lot_size"] = lot_size

        if kwargs and all(k in params for k in kwargs):
            res = position_size(**kwargs)
        else:
            res = position_size(entry, stop_loss)
        return dict(res) if isinstance(res, dict) else {"qty": int(res or 0)}
    except Exception:
        res = position_size(entry, stop_loss)
        return dict(res) if isinstance(res, dict) else {"qty": int(res or 0)}


def _build_entry_and_rationale(
    sector: str,
    is_bull: bool,
    close: float,
    atr_val: float,
    vwap: float | None,
    ema20: float,
    rsi: float,
    vol_ratio: float,
    chg_pct: float,
    rs_score: float,
    chain_summary: dict | None,
    chain_analytics: dict | None = None,
) -> dict:
    atr_safe = max(float(atr_val or 0.0), 0.05)
    anchor = float(vwap) if (vwap is not None and float(vwap) > 0) else ema20
    dist_from_anchor_atr = abs(close - anchor) / atr_safe

    if is_bull:
        overextended = (rsi >= 75.0) or (chg_pct >= 3.5) or (close > anchor and dist_from_anchor_atr >= 2.0)
        best_low = round(max(min(anchor, close) - 0.25 * atr_safe, close - 1.0 * atr_safe), 2)
        best_high = round(max(anchor, close - 0.35 * atr_safe), 2)
        if best_low > best_high:
            best_low, best_high = best_high, best_low
    else:
        overextended = (rsi <= 25.0) or (chg_pct <= -3.5) or (close < anchor and dist_from_anchor_atr >= 2.0)
        best_low = round(min(anchor, close + 0.35 * atr_safe), 2)
        best_high = round(min(max(anchor, close) + 0.25 * atr_safe, close + 1.0 * atr_safe), 2)
        if best_low > best_high:
            best_low, best_high = best_high, best_low

    if overextended:
        extension_status = "EXTENDED / NEAR TOP (Pullback Risk)"
        entry_action = "WAIT FOR RETRACEMENT"
    elif dist_from_anchor_atr > 1.1:
        extension_status = "MODERATELY STRETCHED"
        entry_action = "PREFER PULLBACK TO BEST ZONE"
    else:
        extension_status = "HEALTHY / IN-ZONE"
        entry_action = "IMMEDIATE OR BEST ZONE"

    oi_txt = ""
    if chain_summary and chain_summary.get("oi_build_up"):
        oi_txt = f" OI: {chain_summary.get('oi_build_up')} (PCR {chain_summary.get('pcr')})."

    ca = chain_analytics or {}
    lvl_txt = ""
    if ca.get("eos") is not None or ca.get("eor") is not None:
        lvl_txt = f" EOS {ca.get('eos')} / EOR {ca.get('eor')} [{ca.get('sentiment_state')}]."

    vwap_txt = f"VWAP {vwap:.2f}" if (vwap is not None and vwap > 0) else "VWAP N/A"
    dir_txt = "Bullish alignment" if is_bull else "Bearish alignment"
    why_trade = (
        f"{dir_txt} ({chg_pct:+.2f}%), {'above' if close >= ema20 else 'below'} EMA20 ({ema20:.2f}), "
        f"{vwap_txt}, RSI {rsi:.1f}, Vol {vol_ratio:.2f}x, "
        f"RS vs Nifty {rs_score:+.2f}% in {sector}.{oi_txt}{lvl_txt}"
    )

    return {
        "why_trade": why_trade,
        "extension_status": extension_status,
        "entry_action": entry_action,
        "best_entry_low": best_low,
        "best_entry_high": best_high,
    }


def _build_stock_setup(sym: str, ind: dict, rs_info: dict, is_bull: bool, chain_bundle: dict | None) -> dict | None:
    close = float(ind["close"])
    atr_val = float(ind.get("atr") or max(close * 0.015, 1.0))
    vwap = ind.get("vwap")
    ema20 = float(ind.get("ema20") or close)
    ema50 = float(ind.get("ema50") or close)
    rsi = float(ind.get("rsi") or 50.0)
    vol_ratio = float(ind.get("vol_ratio") or 0.0)
    chg_pct = float(ind.get("change_pct") or 0.0)
    avg_turnover = float(ind.get("avg_turnover") or 0.0)

    if avg_turnover < MIN_DAILY_TURNOVER_RS:
        return None
    if vol_ratio < 0.70:
        return None

    if is_bull:
        if chg_pct < -0.75 or close < ema20:
            return None
        sl = round(close - 1.5 * atr_val, 2)
        pa_t1 = round(close + 1.8 * atr_val, 2)
        pa_t2 = round(close + 3.0 * atr_val, 2)
    else:
        if chg_pct > 0.75 or close > ema20:
            return None
        sl = round(close + 1.5 * atr_val, 2)
        pa_t1 = round(max(close - 1.8 * atr_val, 0.05), 2)
        pa_t2 = round(max(close - 3.0 * atr_val, 0.05), 2)

    if (is_bull and sl >= close) or (not is_bull and sl <= close):
        return None

    chain_summary = (chain_bundle or {}).get("summary") or {}
    chain_df = (chain_bundle or {}).get("df")
    expiry = (chain_bundle or {}).get("expiry")
    oi_target = _compute_oi_target(chain_summary, close, is_bull=is_bull)

    sizing = _safe_position_size(entry=close, stop_loss=sl, lot_size=1)
    if sizing.get("qty", 0) <= 0:
        return None

    opt_plan = select_option_strategy(
        symbol=sym,
        side="CE" if is_bull else "PE",
        spot=close,
        chain_df=chain_df,
        expiry=expiry,
        prefer_spread=True,
    )
    lot_sz = int(opt_plan.get("lot_size") or LOT_SIZES.get(sym, 1) or 1)
    chain_analytics = _build_option_chain_analytics(chain_df, spot=close, lot_size=lot_sz)

    rs_score = float((rs_info or {}).get("rs_score") or 0.0)
    sector = SECTOR_MAP.get(sym, "OTHER")
    rationale = _build_entry_and_rationale(
        sector=sector,
        is_bull=is_bull,
        close=close,
        atr_val=atr_val,
        vwap=vwap,
        ema20=ema20,
        rsi=rsi,
        vol_ratio=vol_ratio,
        chg_pct=chg_pct,
        rs_score=rs_score,
        chain_summary=chain_summary,
        chain_analytics=chain_analytics,
    )

    base_score = float(ind.get("bull_score" if is_bull else "bear_score") or 50.0)
    adj_score = round(max(0.0, min(100.0, 0.8 * base_score + 0.2 * (50.0 + rs_score * 2.0))), 2)

    return {
        "symbol": sym,
        "sector": sector,
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
        "vwap": round(float(vwap), 2) if vwap is not None else None,
        "ema20": round(ema20, 2),
        "ema50": round(ema50, 2),
        "rs_score": rs_score,
        "score": adj_score,
        "history_label": ind.get("history_label", "1y"),
        "chain_summary": chain_summary,
        "chain_analytics": chain_analytics,
        "option_chain": chain_analytics.get("rows", []),
        "option_expiry": expiry,
        "option_plan": opt_plan,
        **rationale,
        **sizing,
    }


def _build_index_trades(candles: dict[str, pd.DataFrame]) -> list[dict]:
    trades = []
    for idx_sym in ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]:
        df = candles.get(idx_sym)
        if df is None or df.empty:
            continue
        ind = compute_indicators(df)
        if not ind:
            continue
        spot = float(ind["close"])
        atr_val = float(ind.get("atr") or max(spot * 0.01, 1.0))
        ema20 = float(ind.get("ema20") or spot)
        chg_pct = float(ind.get("change_pct") or 0.0)
        cum_vwap = _compute_cumulative_vwap(df)

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

        sl = round(spot - 1.5 * atr_val, 2) if is_bull else round(spot + 1.5 * atr_val, 2)
        pa_t1 = round(spot + 1.5 * atr_val, 2) if is_bull else round(max(spot - 1.5 * atr_val, 0.0), 2)
        pa_t2 = round(spot + 2.5 * atr_val, 2) if is_bull else round(max(spot - 2.5 * atr_val, 0.0), 2)
        oi_target = _compute_oi_target(chain_summary, spot, is_bull=is_bull)

        lot_size = int(plan.get("lot_size") or LOT_SIZES.get(idx_sym, 1) or 1)
        chain_analytics = _build_option_chain_analytics(chain_df, spot=spot, lot_size=lot_size)
        max_loss_lot = plan.get("max_loss_lot")
        sizing_status = "CAPITAL NOT CONFIGURED" if not CAPITAL_CONFIGURED else "1 Lot Risk Cap"

        rationale = _build_entry_and_rationale(
            sector="INDEX",
            is_bull=is_bull,
            close=spot,
            atr_val=atr_val,
            vwap=cum_vwap,
            ema20=ema20,
            rsi=float(ind.get("rsi") or 50.0),
            vol_ratio=float(ind.get("vol_ratio") or 0.0),
            chg_pct=chg_pct,
            rs_score=0.0,
            chain_summary=chain_summary,
            chain_analytics=chain_analytics,
        )

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
            "chain_analytics": chain_analytics,
            "option_chain": chain_analytics.get("rows", []),
            "option_expiry": expiry,
            "option_plan": plan,
            **rationale,
        })
    return trades


def export_web_dashboard_data(payload: dict, base_dir: str = "docs/data") -> None:
    root = Path(base_dir)
    history_dir = root / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    now_ist = dt.datetime.now(IST)
    session_date = now_ist.date().isoformat()
    mode_tag = str(payload.get("mode") or MODE or "RUN").upper()

    enriched = dict(payload)
    enriched["generated_at_ist"] = now_ist.strftime("%Y-%m-%d %H:%M:%S IST")
    enriched["session_date"] = session_date
    enriched["session_guardrails"] = _build_session_guardrails(now_ist)

    (root / "latest.json").write_text(
        json.dumps(enriched, indent=2, default=str),
        encoding="utf-8",
    )

    (history_dir / f"{session_date}.json").write_text(
        json.dumps(enriched, indent=2, default=str),
        encoding="utf-8",
    )
    (history_dir / f"{session_date}_{mode_tag}.json").write_text(
        json.dumps(enriched, indent=2, default=str),
        encoding="utf-8",
    )

    manifest_path = root / "manifest.json"
    entries = []
    for f in sorted(history_dir.glob("*.json"), reverse=True):
        stem = f.stem
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
            summ = raw.get("summary") or {}
            entries.append({
                "id": stem,
                "file": f"data/history/{f.name}",
                "date": raw.get("session_date") or stem[:10],
                "mode": raw.get("mode") or "RUN",
                "generated_at_ist": raw.get("generated_at_ist") or "",
                "ideas_count": summ.get("ideas_count", 0),
                "breadth_tone": summ.get("breadth_tone", "MIXED"),
                "nifty_spot": summ.get("nifty_spot", "—"),
                "nifty_chg": summ.get("nifty_chg", "—"),
            })
        except Exception:
            continue

    manifest_path.write_text(
        json.dumps(
            {
                "updated_at_ist": now_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
                "latest_file": "data/latest.json",
                "snapshots": entries[:90],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def run_screener():
    candles, data_source = load_candles_1y()
    logging.info("Loaded candles for %d symbols using %s", len(candles), data_source)
    global_cues = fetch_global_cues()

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
        cum_vwap = _compute_cumulative_vwap(df)
        vol_20 = pd.to_numeric(df["Volume"], errors="coerce").tail(20).mean()
        close_20 = pd.to_numeric(df["Close"], errors="coerce").tail(20).mean()

        ind["vwap"] = cum_vwap
        ind["avg_turnover"] = float((vol_20 or 0.0) * (close_20 or close))
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

    bull_candidates, bear_candidates = [], []
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
        if b_score >= s_score and b_score >= 52.0:
            cb = _get_chain(sym, float(ind["close"]))
            setup = _build_stock_setup(sym, ind, rs_info, True, cb)
            if setup:
                bull_candidates.append(setup)
        elif s_score > b_score and s_score >= 52.0:
            cb = _get_chain(sym, float(ind["close"]))
            setup = _build_stock_setup(sym, ind, rs_info, False, cb)
            if setup:
                bear_candidates.append(setup)

    bull_candidates.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    bear_candidates.sort(key=lambda x: x.get("score", 0.0), reverse=True)

    options_bullish = _select_capped_items(bull_candidates, max_items=5, enforce_risk_cap=False)
    options_bearish = _select_capped_items(bear_candidates, max_items=5, enforce_risk_cap=False)

    intraday_bullish = _select_capped_items(bull_candidates, max_items=5, enforce_risk_cap=True)
    intraday_bearish = _select_capped_items(bear_candidates, max_items=5, enforce_risk_cap=True)
    equity_intraday = intraday_bullish + intraday_bearish

    index_trades = _build_index_trades(candles)

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

    stock_setups_count = len(options_bullish) + len(options_bearish) + len(intraday_bullish) + len(intraday_bearish)
    total_ideas = len(index_trades) + stock_setups_count

    payload = {
        "title": TITLE,
        "mode": MODE,
        "summary": {
            "ideas_count": total_ideas,
            "stock_setups_count": stock_setups_count,
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
        "global_cues": global_cues or [],
        "sectors": sectors,
        "index_trades": index_trades,
        "setups": {
            "bullish": options_bullish,
            "bearish": options_bearish,
        },
         "options_bullish": options_bullish,
        "options_bearish": options_bearish,
        "equity_intraday": equity_intraday,
        "intraday_bullish": intraday_bullish,
        "intraday_bearish": intraday_bearish,
    }

    evaluate_signals_chronologically(candles_by_symbol=candles)
    log_signals(payload)
    payload["walk_forward"] = walk_forward_summary()
    export_web_dashboard_data(payload)

    send_alert(payload)
    logging.info(
        "Dispatched %d total ideas (%d stock setups: %dB/%dS opt + %dB/%dS eq) using %s",
        total_ideas,
        stock_setups_count,
        len(options_bullish),
        len(options_bearish),
        len(intraday_bullish),
        len(intraday_bearish),
        data_source,
    )


if __name__ == "__main__":
    run_screener()