import asyncio
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

try:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    _MCP_AVAILABLE = True
except Exception:
    ClientSession = None  # type: ignore[assignment]
    streamablehttp_client = None  # type: ignore[assignment]
    _MCP_AVAILABLE = False

from chain import analyze_chain
from config import (
    ALLOW_MIS_LEVERAGE,
    CAPITAL,
    CAPITAL_CONFIGURED,
    MAX_ALLOCATION_PCT,
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
from options_data import build_upstox_smartlists_and_movers, chain_upstox
from setups import btst_bearish_score, btst_rally_score, position_size
from tech import clean_ohlcv, compute_indicators
from tracker import (
    category_performance_summary,
    evaluate_signals_chronologically,
    log_signals,
    walk_forward_summary,
)
from universe import (
    BSE_STOCKS_UNIVERSE,
    LOT_SIZES,
    STOCKS_UNIVERSE,
    eq_keys,
    get_nse_fo_option_stocks,
    resolve_upstox_keys_from_master,
)

warnings.filterwarnings("ignore")
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
UPSTOX_ACCESS_TOKEN = (
    os.getenv("UPSTOX_ACCESS_TOKEN")
    or os.getenv("UPSTOX_TOKEN")
    or ""
).strip()
UPSTOX_BASE_V2 = "https://api.upstox.com/v2"
RUN_MODE = (os.getenv("MODE") or MODE or "BTST").strip().upper()

NSE_BHAVCOPY_MCP_URL = os.getenv(
    "NSE_BHAVCOPY_MCP_URL",
    "https://mcp.nseindia.in/bhavcopy/cm/mcp",
).strip()
NSE_CM_MARKET_MCP_URL = os.getenv(
    "NSE_CM_MARKET_MCP_URL",
    "https://mcp.nseindia.in/cmmkt/mcp",
).strip()
ENABLE_NSE_MCP = os.getenv("ENABLE_NSE_MCP", "false").strip().lower() in {"1", "true", "yes"}

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

_BSE_SET = set(BSE_STOCKS_UNIVERSE)
_WARNED_UPSTOX_KEYS: set[str] = set()
_WARNED_YF_COUNT = 0
_WARNED_MCP_COUNT = 0


def _passes_liquidity_and_movement_filter(close: float, atr: float, avg_turnover: float) -> bool:
    if avg_turnover < MIN_DAILY_TURNOVER_RS:
        return False
    if close <= 0 or atr <= 0:
        return False
    atr_pct = (atr / close) * 100.0
    return (atr >= 15.0) or (atr_pct >= 1.2)


def _has_option_chain_support(symbol: str, chain_bundle: dict | None = None) -> bool:
    sym_u = str(symbol or "").strip().upper()
    if sym_u in {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"}:
        return True
    if int(LOT_SIZES.get(sym_u, 0) or 0) > 1:
        return True
    csum = (chain_bundle or {}).get("summary") or {}
    if csum.get("chain_available"):
        return True
    cdf = (chain_bundle or {}).get("df")
    if cdf is not None and not getattr(cdf, "empty", True):
        return True
    return False


def _resolve_lot_size(symbol: str, opt_plan: dict | None = None) -> int:
    sym_u = str(symbol or "").strip().upper()
    plan_lot = (opt_plan or {}).get("lot_size")
    try:
        val = int(plan_lot)
        if val > 0:
            return val
    except (TypeError, ValueError):
        pass
    try:
        uni_lot = int(LOT_SIZES.get(sym_u, 1) or 1)
        if uni_lot > 0:
            return uni_lot
    except (TypeError, ValueError):
        pass
    return 1


def _yf_ticker(sym: str) -> str:
    if sym in YF_SYMBOL_MAP:
        return YF_SYMBOL_MAP[sym]
    if sym in _BSE_SET:
        return f"{sym}.BO"
    return f"{sym}.NS"


def _clean_yf_duplicates(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None or getattr(df, "empty", True):
        return None
    out = df.copy()
    if out.index.duplicated().any():
        out = out[~out.index.duplicated(keep="last")]
    if isinstance(out.columns, pd.MultiIndex):
        out = out.loc[:, ~out.columns.duplicated()]
        flat_cols = []
        for c in out.columns:
            parts = [str(x).strip() for x in c if x is not None and str(x).strip() != ""]
            flat_cols.append(parts[0] if parts else "")
        out.columns = flat_cols
    else:
        out.columns = [str(c).strip() for c in out.columns]
    out = out.loc[:, ~out.columns.duplicated()]
    return out


def _compute_session_vwap(df: pd.DataFrame, live_vwap: float | None = None, lookback_days: int = 1) -> float | None:
    if live_vwap is not None:
        try:
            lv = float(live_vwap)
            if lv > 0 and pd.notna(lv):
                return round(lv, 2)
        except (TypeError, ValueError):
            pass

    if df is None or getattr(df, "empty", True):
        return None

    sub = df.tail(max(int(lookback_days), 1))
    high = pd.to_numeric(sub["High"], errors="coerce")
    low = pd.to_numeric(sub["Low"], errors="coerce")
    close = pd.to_numeric(sub["Close"], errors="coerce")
    vol = pd.to_numeric(sub["Volume"], errors="coerce").fillna(0.0)

    typical = (high + low + close) / 3.0
    cum_vol = float(vol.sum()) if len(vol) else 0.0
    cum_pv = float((typical * vol).sum()) if len(typical) else 0.0
    if cum_vol > 0.0 and pd.notna(cum_pv):
        return round(cum_pv / cum_vol, 2)

    last_typ = float(typical.iloc[-1]) if len(typical) and pd.notna(typical.iloc[-1]) else None
    return round(last_typ, 2) if last_typ is not None else None


def _compute_avg_daily_range_rs(df: pd.DataFrame, lookback: int = 14) -> float:
    if df is None or getattr(df, "empty", True):
        return 0.0
    high = pd.to_numeric(df["High"], errors="coerce")
    low = pd.to_numeric(df["Low"], errors="coerce")
    rng = (high - low).dropna()
    if rng.empty:
        return 0.0
    val = float(rng.tail(max(int(lookback), 1)).mean())
    return round(val, 2) if pd.notna(val) else 0.0


def _compute_close_position_ratio(df: pd.DataFrame, close: float) -> float:
    if df is None or getattr(df, "empty", True):
        return 0.5
    last_row = df.iloc[-1]
    h = float(last_row.get("High") or close)
    l = float(last_row.get("Low") or close)
    if h > l:
        ratio = (close - l) / (h - l)
        return round(max(0.0, min(1.0, ratio)), 2)
    return 0.5


def _extract_sparkline_and_patterns(df: pd.DataFrame | None, close: float) -> dict[str, Any]:
    if df is None or getattr(df, "empty", True):
        return {"sparkline_10d": [], "setup_tags": []}

    closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
    highs = pd.to_numeric(df["High"], errors="coerce").dropna()
    lows = pd.to_numeric(df["Low"], errors="coerce").dropna()

    sparkline_10d = [round(float(x), 2) for x in closes.tail(10).tolist()]
    tags: list[str] = []

    if len(highs) >= 2 and len(lows) >= 2:
        cur_h, prev_h = float(highs.iloc[-1]), float(highs.iloc[-2])
        cur_l, prev_l = float(lows.iloc[-1]), float(lows.iloc[-2])

        if cur_h <= prev_h and cur_l >= prev_l:
            tags.append("INSIDE_BAR")
        if close > prev_h:
            tags.append("PDH_BREAK")
        elif close < prev_l:
            tags.append("PDL_BREAK")

    if len(highs) >= 21 and len(lows) >= 21:
        hi_20 = float(highs.iloc[-21:-1].max())
        lo_20 = float(lows.iloc[-21:-1].min())
        if close > hi_20:
            tags.append("20D_HIGH_BO")
        elif close < lo_20:
            tags.append("20D_LOW_BD")

    return {
        "sparkline_10d": sparkline_10d,
        "setup_tags": tags,
    }


def _compute_support_resistance_and_probability(
    df: pd.DataFrame | None,
    close: float,
    atr: float,
    ema20: float,
    ema50: float,
    rsi: float,
    vol_ratio: float,
    rs_score: float,
    sector_chg_pct: float,
    is_bull: bool,
    chain_summary: dict[str, Any],
) -> dict[str, Any]:
    recent_high = close + 1.5 * atr
    recent_low = max(close - 1.5 * atr, 0.05)

    if df is not None and not getattr(df, "empty", True):
        highs = pd.to_numeric(df["High"], errors="coerce").dropna().tail(10)
        lows = pd.to_numeric(df["Low"], errors="coerce").dropna().tail(10)
        if not highs.empty:
            hi_above = [float(x) for x in highs.tolist() if float(x) > close * 1.001]
            recent_high = min(hi_above) if hi_above else float(highs.max())
        if not lows.empty:
            lo_below = [float(x) for x in lows.tolist() if float(x) < close * 0.999]
            recent_low = max(lo_below) if lo_below else float(lows.min())

    oi_sup = chain_summary.get("immediate_support")
    oi_res = chain_summary.get("immediate_resistance")

    imm_support = round(max(recent_low, float(oi_sup)) if oi_sup else recent_low, 2)
    imm_resistance = round(min(recent_high, float(oi_res)) if oi_res else recent_high, 2)
    if imm_support >= close:
        imm_support = round(max(close - 1.0 * atr, 0.05), 2)
    if imm_resistance <= close:
        imm_resistance = round(close + 1.0 * atr, 2)

    if is_bull:
        pa_t1 = round(max(close + 0.8 * atr, imm_resistance if imm_resistance > close else close + 0.8 * atr), 2)
        pa_t2 = round(max(close + 1.5 * atr, pa_t1 + 0.7 * atr), 2)
        oi_target = round(float(chain_summary.get("oi_target_bull") or pa_t1), 2)
    else:
        pa_t1 = round(max(min(close - 0.8 * atr, imm_support if imm_support < close else close - 0.8 * atr), 0.05), 2)
        pa_t2 = round(max(min(close - 1.5 * atr, pa_t1 - 0.7 * atr), 0.05), 2)
        oi_target = round(float(chain_summary.get("oi_target_bear") or pa_t1), 2)

    prob = 50.0
    if is_bull:
        if close > ema20:
            prob += 7.0
        if ema20 > ema50:
            prob += 6.0
        if 52.0 <= rsi <= 75.0:
            prob += 5.0
        if vol_ratio >= 1.3:
            prob += 6.0
        if rs_score > 0:
            prob += min(rs_score * 1.2, 7.0)
        if sector_chg_pct > 0:
            prob += min(sector_chg_pct * 2.0, 5.0)
        if chain_summary.get("chain_available"):
            pcr = chain_summary.get("pcr")
            if pcr is not None and float(pcr) >= 1.0:
                prob += 5.0
    else:
        if close < ema20:
            prob += 7.0
        if ema20 < ema50:
            prob += 6.0
        if 25.0 <= rsi <= 48.0:
            prob += 5.0
        if vol_ratio >= 1.3:
            prob += 6.0
        if rs_score < 0:
            prob += min(abs(rs_score) * 1.2, 7.0)
        if sector_chg_pct < 0:
            prob += min(abs(sector_chg_pct) * 2.0, 5.0)
        if chain_summary.get("chain_available"):
            pcr = chain_summary.get("pcr")
            if pcr is not None and float(pcr) <= 1.0:
                prob += 5.0

    prob_clamped = round(max(35.0, min(92.0, prob)), 1)
    return {
        "immediate_support": imm_support,
        "immediate_resistance": imm_resistance,
        "pa_target_1": pa_t1,
        "pa_target_2": pa_t2,
        "oi_target": oi_target,
        "move_probability_pct": prob_clamped,
    }


def _parse_mcp_tool_result(result: Any) -> Any:
    if result is None:
        return None
    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured
    for block in (getattr(result, "content", None) or []):
        txt = getattr(block, "text", None)
        if txt:
            try:
                return json.loads(txt)
            except Exception:
                return txt
    return None


async def _call_nse_mcp_tool_async(url: str, tool_name: str, arguments: dict[str, Any]) -> Any:
    async with streamablehttp_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            res = await session.call_tool(tool_name, arguments=arguments)
            return _parse_mcp_tool_result(res)


def call_nse_mcp_tool(url: str, tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
    global _WARNED_MCP_COUNT
    if not ENABLE_NSE_MCP or not _MCP_AVAILABLE or not url:
        return None
    args = arguments or {}
    try:
        return asyncio.run(
            asyncio.wait_for(_call_nse_mcp_tool_async(url, tool_name, args), timeout=6.0)
        )
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                asyncio.wait_for(_call_nse_mcp_tool_async(url, tool_name, args), timeout=6.0)
            )
        except Exception as exc:
            if _WARNED_MCP_COUNT < 3:
                _WARNED_MCP_COUNT += 1
                logging.debug("NSE MCP call failed (%s): %s", tool_name, exc)
            return None
        finally:
            loop.close()
    except Exception as exc:
        if _WARNED_MCP_COUNT < 3:
            _WARNED_MCP_COUNT += 1
            logging.debug("NSE MCP call failed (%s): %s", tool_name, exc)
        return None


def _fetch_nse_mcp_live_snapshot() -> dict[str, Any]:
    if not ENABLE_NSE_MCP or not _MCP_AVAILABLE or not NSE_CM_MARKET_MCP_URL:
        return {}
    live_data = call_nse_mcp_tool(NSE_CM_MARKET_MCP_URL, "cm_get_live_market_data", {})
    if isinstance(live_data, dict):
        return live_data
    return {}


def _fetch_upstox_1y(instrument_key: str, symbol: str = "") -> pd.DataFrame | None:
    if not UPSTOX_ACCESS_TOKEN or not instrument_key:
        return None

    end_date = dt.datetime.now(IST).date()
    start_date = end_date - dt.timedelta(days=370)
    encoded = quote(instrument_key, safe="")
    url = f"{UPSTOX_BASE_V2}/historical-candle/{encoded}/day/{end_date.isoformat()}/{start_date.isoformat()}"
    headers = {"Authorization": f"Bearer {UPSTOX_ACCESS_TOKEN}", "Accept": "application/json"}
    try:
        resp = requests.get(url, headers=headers, timeout=6)
        if resp.status_code != 200:
            warn_key = f"{symbol}:{instrument_key}:{resp.status_code}"
            if warn_key not in _WARNED_UPSTOX_KEYS:
                _WARNED_UPSTOX_KEYS.add(warn_key)
                logging.warning(
                    "Upstox %s [%s] HTTP %s: %s",
                    symbol or "SYM",
                    instrument_key,
                    resp.status_code,
                    (resp.text or "")[:120],
                )
            return None
        candles = ((resp.json() or {}).get("data") or {}).get("candles") or []
        if not candles:
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
        if cleaned is not None and len(cleaned) >= 20:
            return cleaned
        return None
    except Exception as exc:
        logging.warning("Upstox %s [%s] exception: %s", symbol or "SYM", instrument_key, exc)
        return None


def _fetch_upstox_live_quotes(keys_by_sym: dict[str, str]) -> dict[str, dict[str, float]]:
    live_map: dict[str, dict[str, float]] = {}
    if not UPSTOX_ACCESS_TOKEN or not keys_by_sym:
        return live_map

    valid_pairs = [(sym, ikey) for sym, ikey in keys_by_sym.items() if ikey]
    if not valid_pairs:
        return live_map

    headers = {"Authorization": f"Bearer {UPSTOX_ACCESS_TOKEN}", "Accept": "application/json"}
    chunk_size = 50

    def _fetch_chunk(chunk: list[tuple[str, str]]) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        ikey_to_sym: dict[str, str] = {}
        norm_ikey_to_sym: dict[str, str] = {}
        for sym, ikey in chunk:
            ikey_to_sym[ikey] = sym
            norm_ikey_to_sym[ikey.replace(":", "|").upper()] = sym

        query_keys = quote(",".join(ikey for _, ikey in chunk), safe=",:|")
        url = f"{UPSTOX_BASE_V2}/market-quote/quotes?instrument_key={query_keys}"
        try:
            resp = requests.get(url, headers=headers, timeout=6)
            if resp.status_code != 200:
                logging.warning("Upstox live quote HTTP %s: %s", resp.status_code, (resp.text or "")[:140])
                return out
            data_obj = (resp.json() or {}).get("data") or {}
            if not isinstance(data_obj, dict):
                return out

            for resp_key, qobj in data_obj.items():
                if not isinstance(qobj, dict):
                    continue
                sym = (
                    ikey_to_sym.get(resp_key)
                    or norm_ikey_to_sym.get(str(resp_key).replace(":", "|").upper())
                    or str(qobj.get("symbol") or "").strip().upper()
                )
                if not sym or sym not in keys_by_sym:
                    continue

                def _num(val: Any) -> float | None:
                    if val is None or val == "":
                        return None
                    try:
                        fv = float(val)
                        return fv if pd.notna(fv) and fv > 0 else None
                    except (TypeError, ValueError):
                        return None

                ltp = _num(qobj.get("last_price"))
                ohlc = qobj.get("ohlc") if isinstance(qobj.get("ohlc"), dict) else {}
                o_px = _num(ohlc.get("open"))
                h_px = _num(ohlc.get("high"))
                l_px = _num(ohlc.get("low"))
                c_px = _num(ohlc.get("close"))
                prev_close = _num(qobj.get("prev_close") or qobj.get("previous_close") or c_px)

                eff_last = ltp if ltp is not None else c_px
                if eff_last is None:
                    continue

                chg_pct = None
                net_chg = qobj.get("net_change")
                if net_chg is not None and prev_close and prev_close > 0:
                    try:
                        chg_pct = (float(net_chg) / prev_close) * 100.0
                    except (TypeError, ValueError):
                        chg_pct = None
                if chg_pct is None and prev_close and prev_close > 0:
                    chg_pct = ((eff_last - prev_close) / prev_close) * 100.0

                vol = _num(qobj.get("volume")) or 0.0
                atp = _num(qobj.get("average_price") or qobj.get("atp") or qobj.get("vwap"))

                out[sym] = {
                    "ltp": round(eff_last, 2),
                    "open": round(o_px if o_px is not None else eff_last, 2),
                    "high": round(h_px if h_px is not None else eff_last, 2),
                    "low": round(l_px if l_px is not None else eff_last, 2),
                    "prev_close": round(prev_close, 2) if prev_close is not None else 0.0,
                    "change_pct": round(chg_pct, 2) if chg_pct is not None else 0.0,
                    "volume": vol,
                    "vwap": round(atp, 2) if atp is not None else 0.0,
                }
        except Exception as exc:
            logging.warning("Upstox live quote batch error: %s", exc)
        return out

    chunks = [valid_pairs[i : i + chunk_size] for i in range(0, len(valid_pairs), chunk_size)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for part in pool.map(_fetch_chunk, chunks):
            live_map.update(part)

    return live_map


def _apply_live_quotes_to_candles(
    candles: dict[str, pd.DataFrame],
    live_quotes: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    applied: dict[str, dict[str, float]] = {}
    if not live_quotes:
        return applied

    now_ist_date = dt.datetime.now(IST).date().isoformat()

    for sym, q in live_quotes.items():
        df = candles.get(sym)
        if df is None or df.empty:
            continue
        out = df.copy()
        ltp = float(q["ltp"])
        h_px = max(float(q["high"]), ltp)
        l_px = min(float(q["low"]), ltp) if float(q["low"]) > 0 else ltp
        o_px = float(q["open"]) if float(q["open"]) > 0 else ltp
        vol = float(q.get("volume") or 0.0)

        last_idx = out.index[-1]
        last_dt_str = str(last_idx)[:10]

        if last_dt_str == now_ist_date:
            out.loc[last_idx, "Open"] = o_px
            out.loc[last_idx, "High"] = max(float(out.loc[last_idx, "High"]), h_px)
            cur_low = float(out.loc[last_idx, "Low"])
            out.loc[last_idx, "Low"] = min(cur_low, l_px) if cur_low > 0 else l_px
            out.loc[last_idx, "Close"] = ltp
            if vol > 0:
                out.loc[last_idx, "Volume"] = max(float(out.loc[last_idx, "Volume"] or 0.0), vol)
        else:
            new_row = out.iloc[-1].copy()
            new_row["Open"] = o_px
            new_row["High"] = h_px
            new_row["Low"] = l_px
            new_row["Close"] = ltp
            if vol > 0:
                new_row["Volume"] = vol
            try:
                new_idx = pd.to_datetime(now_ist_date)
                out.loc[new_idx] = new_row
            except Exception:
                out.iloc[-1] = new_row

        cleaned = clean_ohlcv(out)
        if cleaned is not None and not cleaned.empty:
            candles[sym] = cleaned

        prev_c = float(q.get("prev_close") or 0.0)
        if prev_c <= 0 and len(out) >= 2:
            prev_c = float(out["Close"].iloc[-2])

        chg_pct = float(q.get("change_pct") or 0.0)
        if prev_c > 0:
            chg_pct = ((ltp - prev_c) / prev_c) * 100.0

        applied[sym] = {
            "close": round(ltp, 2),
            "open": round(o_px, 2),
            "high": round(h_px, 2),
            "low": round(l_px, 2),
            "prev_close": round(prev_c, 2),
            "volume": vol,
            "change_pct": round(chg_pct, 2),
            "vwap": float(q.get("vwap") or 0.0),
        }

    return applied


def _fetch_yf_1y(symbol: str) -> pd.DataFrame | None:
    global _WARNED_YF_COUNT
    primary_ticker = _yf_ticker(symbol)
    alt_ticker = (
        f"{symbol}.NS"
        if primary_ticker.endswith(".BO")
        else (f"{symbol}.BO" if primary_ticker.endswith(".NS") else primary_ticker)
    )
    candidates = [primary_ticker] if primary_ticker == alt_ticker else [primary_ticker, alt_ticker]

    for ticker in candidates:
        try:
            df = yf.download(
                ticker,
                period="1y",
                interval="1d",
                progress=False,
                threads=False,
                timeout=5,
                auto_adjust=False,
                group_by="column",
            )
            cleaned_raw = _clean_yf_duplicates(df)
            norm_dl = clean_ohlcv(cleaned_raw) if cleaned_raw is not None else None
            if norm_dl is not None and len(norm_dl) >= 20:
                return norm_dl
        except Exception as exc:
            if _WARNED_YF_COUNT < 6:
                _WARNED_YF_COUNT += 1
                logging.debug("YF %s err: %s", ticker, exc)
    return None


def load_candles_1y() -> tuple[dict[str, pd.DataFrame], str, dict[str, dict[str, float]]]:
    keys = dict(eq_keys())
    if UPSTOX_ACCESS_TOKEN and keys:
        master_keys = resolve_upstox_keys_from_master(list(keys.keys()))
        for sym, mkey in master_keys.items():
            if mkey:
                keys[sym] = mkey

    candles: dict[str, pd.DataFrame] = {}
    upstox_hits = 0
    failed_upstox_syms: list[str] = []

    def _worker(sym: str):
        df = _fetch_upstox_1y(keys.get(sym, ""), symbol=sym)
        if df is not None and not df.empty:
            return sym, df, "UPSTOX"
        return sym, None, "MISS"

    with ThreadPoolExecutor(max_workers=12) as pool:
        for sym, df, src in pool.map(_worker, list(keys.keys())):
            if df is not None and not df.empty:
                candles[sym] = df
                upstox_hits += 1
            else:
                failed_upstox_syms.append(sym)

    if failed_upstox_syms:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for sym, yf_df in pool.map(lambda s: (s, _fetch_yf_1y(s)), failed_upstox_syms):
                if yf_df is not None and not yf_df.empty:
                    candles[sym] = yf_df

    live_quotes = _fetch_upstox_live_quotes(keys)
    live_overrides = _apply_live_quotes_to_candles(candles, live_quotes)

    parts = []
    if live_overrides:
        parts.append(f"UPSTOX_LIVE({len(live_overrides)})")
    if upstox_hits > 0:
        parts.append("UPSTOX_V2")
    if upstox_hits < len(candles):
        parts.append("YFINANCE_1Y")
    source_label = " + ".join(parts) + " (NSE+BSE)"
    return candles, source_label, live_overrides


def _compute_intraday_gap_and_orb(
    sym: str,
    q: dict[str, Any],
    df: pd.DataFrame | None,
    nifty_gap_pct: float = 0.0,
    confirm_orb_15m: bool = False,
) -> dict[str, Any]:
    prev_close = float(q.get("prev_close") or 0.0)
    open_px = float(q.get("open") or 0.0)
    high_px = float(q.get("high") or 0.0)
    low_px = float(q.get("low") or 0.0)
    ltp = float(q.get("ltp") or open_px or 0.0)
    vol = float(q.get("volume") or 0.0)

    if prev_close <= 0 and df is not None and len(df) >= 2:
        prev_close = float(pd.to_numeric(df["Close"], errors="coerce").iloc[-2])

    prev_high = 0.0
    prev_low = 0.0
    avg_vol = 0.0
    if df is not None and not df.empty:
        hist_slice = df.iloc[:-1] if len(df) > 1 else df
        if not hist_slice.empty:
            prev_high = float(pd.to_numeric(hist_slice["High"], errors="coerce").iloc[-1])
            prev_low = float(pd.to_numeric(hist_slice["Low"], errors="coerce").iloc[-1])
            avg_vol = float(pd.to_numeric(hist_slice["Volume"], errors="coerce").tail(20).mean() or 0.0)

    gap_pct = round(((open_px - prev_close) / prev_close) * 100.0, 2) if (prev_close > 0 and open_px > 0) else 0.0
    open_eq_high = bool(open_px > 0 and high_px > 0 and abs(open_px - high_px) <= max(open_px * 0.0005, 0.05))
    open_eq_low = bool(open_px > 0 and low_px > 0 and abs(open_px - low_px) <= max(open_px * 0.0005, 0.05))
    broke_prev_high = bool(prev_high > 0 and max(high_px, ltp) > prev_high)
    broke_prev_low = bool(prev_low > 0 and min(low_px if low_px > 0 else ltp, ltp) < prev_low)

    expected_1m_vol = (avg_vol / 375.0) if avg_vol > 0 else 0.0
    first_min_vol_ratio = round(vol / expected_1m_vol, 2) if expected_1m_vol > 0 else 0.0

    side = None
    if (0.3 <= gap_pct <= 2.0) and open_eq_low and (nifty_gap_pct >= 0.0):
        side = "BULLISH"
    elif (-2.0 <= gap_pct <= -0.3) and open_eq_high and (nifty_gap_pct <= 0.0):
        side = "BEARISH"

    orb_confirmed = False
    if side == "BULLISH":
        orb_confirmed = bool(ltp > high_px or broke_prev_high) and (ltp >= open_px)
    elif side == "BEARISH":
        orb_confirmed = bool(ltp < low_px or broke_prev_low) and (ltp <= open_px)

    signal_state = "CONFIRMED" if (confirm_orb_15m and orb_confirmed) else "WATCHLIST"
    gap_score = round(
        (abs(gap_pct) * 20.0)
        + (20.0 if (broke_prev_high or broke_prev_low) else 0.0)
        + min(first_min_vol_ratio * 5.0, 25.0),
        2,
    )

    return {
        "symbol": sym,
        "gap_pct": gap_pct,
        "open_eq_high": open_eq_high,
        "open_eq_low": open_eq_low,
        "broke_prev_high": broke_prev_high,
        "broke_prev_low": broke_prev_low,
        "first_min_vol_ratio": first_min_vol_ratio,
        "nifty_gap_pct": round(nifty_gap_pct, 2),
        "gap_side": side,
        "orb_confirmed": orb_confirmed,
        "signal_state": signal_state,
        "gap_score": gap_score,
    }


def _select_option_leg_1pct_risk(
    symbol: str,
    side: str,
    spot: float,
    chain_df: pd.DataFrame | None,
    expiry: str | None,
    lot_size: int,
) -> dict[str, Any]:
    today_str = dt.datetime.now(IST).date().isoformat()
    if expiry and str(expiry).strip()[:10] == today_str:
        return {"available": False, "reason": "SKIP_EXPIRY_DAY", "lot_size": lot_size}
    if chain_df is None or getattr(chain_df, "empty", True) or spot <= 0 or lot_size <= 0:
        return {"available": False, "reason": "NO_CHAIN_OR_LOT", "lot_size": lot_size}

    max_risk_cap = float(CAPITAL) * float(RISK_PCT) if CAPITAL_CONFIGURED else 0.0
    max_alloc_cap = float(CAPITAL) * float(MAX_ALLOCATION_PCT) if CAPITAL_CONFIGURED else 0.0
    is_ce = str(side).upper() in {"CE", "BULLISH", "BUY CALL (CE)"}
    p = "ce" if is_ce else "pe"

    candidates = []
    for _, r in chain_df.iterrows():
        strike = float(r.get("strike") or 0.0)
        ltp = float(r.get(f"{p}_ltp") or 0.0)
        bid = float(r.get(f"{p}_bid") or 0.0)
        ask = float(r.get(f"{p}_ask") or 0.0)
        oi = int(r.get(f"{p}_oi") or 0)
        prev_oi = int(r.get(f"{p}_prev_oi") or 0)
        if strike <= 0 or ltp <= 0:
            continue
        if oi <= prev_oi:
            continue
        if bid > 0 and ask >= bid:
            spread_pct = ((ask - bid) / ask) * 100.0
            if spread_pct > 2.0:
                continue
        if is_ce and strike > spot:
            continue
        if (not is_ce) and strike < spot:
            continue
        candidates.append((abs(strike - spot), strike, ltp, oi - prev_oi))

    if not candidates:
        return {"available": False, "reason": "NO_ATM_ITM_OI_SPREAD_MATCH", "lot_size": lot_size}

    candidates.sort(key=lambda x: (x[0], -x[3]))
    _, pick_strike, pick_ltp, oi_inc = candidates[0]
    one_lot_cost = pick_ltp * lot_size
    if max_risk_cap > 0 and one_lot_cost > max_risk_cap:
        return {
            "available": False,
            "reason": "SKIP_LOT_TOO_LARGE",
            "strike": pick_strike,
            "ltp": pick_ltp,
            "lot_size": lot_size,
            "one_lot_risk": round(one_lot_cost, 2),
        }

    sizing = position_size(entry=pick_ltp, stop_loss=0.0, lot_size=lot_size)
    qty = int((sizing or {}).get("qty") or 0)
    if max_alloc_cap > 0 and (qty * pick_ltp) > max_alloc_cap:
        qty = int(max_alloc_cap // (pick_ltp * lot_size)) * lot_size
    if qty < lot_size:
        return {"available": False, "reason": "SKIP_LOT_TOO_LARGE", "lot_size": lot_size}

    return {
        "available": True,
        "contract_type": "ATM_OR_1ITM",
        "option_type": "CE" if is_ce else "PE",
        "strike": pick_strike,
        "ltp": round(pick_ltp, 2),
        "oi_increase": oi_inc,
        "qty": qty,
        "max_risk": round(min(qty * pick_ltp, max_risk_cap if max_risk_cap > 0 else qty * pick_ltp), 2),
        "expiry": expiry,
    }


def _safe_position_size(entry: float, stop_loss: float, lot_size: int = 1) -> dict:
    eff_lot = max(int(lot_size or 1), 1)
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
            kwargs["lot_size"] = eff_lot

        if kwargs and all(k in params for k in kwargs):
            res = position_size(**kwargs)
        else:
            res = position_size(entry, stop_loss)
        out = dict(res) if isinstance(res, dict) else {"qty": int(res or 0)}
    except Exception:
        res = position_size(entry, stop_loss)
        out = dict(res) if isinstance(res, dict) else {"qty": int(res or 0)}

    if int(out.get("qty") or 0) <= 0:
        out["qty"] = eff_lot
    return out


def _build_stock_setup(
    sym: str,
    ind: dict,
    df: pd.DataFrame,
    rs_info: dict,
    is_bull: bool,
    chain_bundle: dict | None,
    nifty_5d_pct: float = 0.0,
    sector_chg_pct: float = 0.0,
    gap_meta: dict[str, Any] | None = None,
) -> dict | None:
    close = float(ind["close"])
    atr_val = float(ind.get("atr") or 0.0)
    avg_turnover = float(ind.get("avg_turnover") or 0.0)
    if not _passes_liquidity_and_movement_filter(close, atr_val, avg_turnover):
        return None

    vwap = ind.get("vwap")
    ema20 = float(ind.get("ema20") or close)
    ema50 = float(ind.get("ema50") or close)
    rsi = float(ind.get("rsi") or 50.0)
    vol_ratio = float(ind.get("vol_ratio") or 1.0)
    chg_pct = float(ind.get("change_pct") or 0.0)
    avg_range_rs = float(ind.get("avg_range_rs") or 0.0)
    close_pos_ratio = float(ind.get("close_pos_ratio") or 0.5)
    has_oc = _has_option_chain_support(sym, chain_bundle)

    chain_summary = (chain_bundle or {}).get("summary") or {}
    chain_df = (chain_bundle or {}).get("df")
    expiry = (chain_bundle or {}).get("expiry")

    opt_plan = select_option_strategy(
        symbol=sym,
        side="CE" if is_bull else "PE",
        spot=close,
        chain_df=chain_df,
        expiry=expiry,
        prefer_spread=True,
    )
    if not isinstance(opt_plan, dict):
        opt_plan = {}
    lot_sz = _resolve_lot_size(sym, opt_plan)
    opt_plan["lot_size"] = lot_sz

    day_high = float(pd.to_numeric(df["High"], errors="coerce").iloc[-1]) if not df.empty else close
    day_low = float(pd.to_numeric(df["Low"], errors="coerce").iloc[-1]) if not df.empty else close
    sector = SECTOR_MAP.get(sym, "OTHER")
    rs_score = float((rs_info or {}).get("rs_score") or 0.0)
    pa_extra = _extract_sparkline_and_patterns(df, close)
    levels_and_prob = _compute_support_resistance_and_probability(
        df=df,
        close=close,
        atr=atr_val,
        ema20=ema20,
        ema50=ema50,
        rsi=rsi,
        vol_ratio=vol_ratio,
        rs_score=rs_score,
        sector_chg_pct=sector_chg_pct,
        is_bull=is_bull,
        chain_summary=chain_summary,
    )

    pa_t1 = levels_and_prob["pa_target_1"]
    pa_t2 = levels_and_prob["pa_target_2"]
    oi_t = levels_and_prob["oi_target"]
    prob_pct = levels_and_prob["move_probability_pct"]

    if RUN_MODE == "BTST":
        row_for_btst = {
            "close": close,
            "entry": close,
            "atr": atr_val,
            "day_high": day_high,
            "day_low": day_low,
            "close_pos_ratio": close_pos_ratio,
            "vol_ratio": vol_ratio,
            "ema20": ema20,
            "ema50": ema50,
            "ret_5d_pct": float((rs_info or {}).get("rs_5d") or chg_pct),
            "rsi": rsi,
        }
        btst_res = (
            btst_rally_score(
                row=row_for_btst,
                df=df,
                nifty_5d_pct=nifty_5d_pct,
                sector_chg_pct=sector_chg_pct,
            )
            if is_bull
            else btst_bearish_score(
                row=row_for_btst,
                df=df,
                nifty_5d_pct=nifty_5d_pct,
                sector_chg_pct=sector_chg_pct,
            )
        )
        if not btst_res:
            return None
        sl = float(btst_res["stop_loss"])
        adj_score = float(btst_res["btst_score"])
        label = "BTST Bull" if is_bull else "BTST Bear"
        why_trade = (
            f"{label} Score {adj_score:.0f}/100 (Prob {prob_pct:.0f}%): "
            + (", ".join(btst_res["btst_reasons"]) or "Momentum close")
        )
        extension_status = "BTST OVERNIGHT SETUP"
        entry_action = "BUY NEAR CLOSE / HOLD NEXT SESSION" if is_bull else "SELL NEAR CLOSE / HOLD NEXT SESSION"
    else:
        if is_bull:
            if chg_pct < -1.5 or close < ema20:
                return None
            sl = round(close - 1.5 * atr_val, 2)
        else:
            if chg_pct > 1.5 or close > ema20:
                return None
            sl = round(close + 1.5 * atr_val, 2)

        base_score = float(ind.get("bull_score" if is_bull else "bear_score") or 50.0)
        adj_score = round(max(0.0, min(100.0, 0.75 * base_score + 0.25 * (50.0 + rs_score * 1.5))), 2)
        gm = gap_meta or {}
        state_lbl = gm.get("signal_state") or "WATCHLIST"
        gap_lbl = f"Gap {gm.get('gap_pct', 0.0):+.2f}% [{state_lbl}]" if gm else f"Chg {chg_pct:+.2f}%"
        why_trade = (
            f"{'Bullish' if is_bull else 'Bearish'} {gap_lbl}, Prob {prob_pct:.0f}%, EMA20 {ema20:.2f}, "
            f"Sup {levels_and_prob['immediate_support']} / Res {levels_and_prob['immediate_resistance']}, RS {rs_score:+.2f}%."
        )
        extension_status = gm.get("signal_state") or "INTRADAY SETUP"
        entry_action = "ORB CONFIRMED ENTRY" if gm.get("orb_confirmed") else "WATCHLIST / CONFIRMATION"

    if (is_bull and sl >= close) or (not is_bull and sl <= close):
        return None

    sizing = _safe_position_size(entry=close, stop_loss=sl, lot_size=lot_sz)
    if int(sizing.get("qty") or 0) <= 0:
        return None

    return {
        "symbol": sym,
        "sector": sector,
        "has_option_chain": has_oc,
        "close_pos_ratio": close_pos_ratio,
        "side": "BULLISH" if is_bull else "BEARISH",
        "action": "BUY (LONG)" if is_bull else "SELL (SHORT)",
        "entry": round(close, 2),
        "close": round(close, 2),
        "spot": round(close, 2),
        "change_pct": round(chg_pct, 2),
        "atr": round(atr_val, 2),
        "atr_pct": round((atr_val / close) * 100.0, 2) if close > 0 else 0.0,
        "avg_range_rs": round(avg_range_rs, 2),
        "stop_loss": sl,
        "target_1": pa_t1,
        "target_2": pa_t2,
        "pa_target_1": pa_t1,
        "pa_target_2": pa_t2,
        "oi_target": oi_t,
        "immediate_support": levels_and_prob["immediate_support"],
        "immediate_resistance": levels_and_prob["immediate_resistance"],
        "move_probability_pct": prob_pct,
        "chain_summary": chain_summary,
        "rsi": round(rsi, 1),
        "vol_ratio": round(vol_ratio, 2),
        "vwap": round(float(vwap), 2) if (vwap is not None and float(vwap) > 0) else None,
        "ema20": round(ema20, 2),
        "ema50": round(ema50, 2),
        "rs_score": rs_score,
        "score": adj_score,
        "why_trade": why_trade,
        "extension_status": extension_status,
        "entry_action": entry_action,
        "best_entry_low": round(min(close, sl), 2),
        "best_entry_high": round(max(close, pa_t1), 2),
        "option_expiry": expiry,
        "option_plan": opt_plan,
        "gap_meta": gap_meta or {},
        "sparkline_10d": pa_extra["sparkline_10d"],
        "setup_tags": pa_extra["setup_tags"],
        **sizing,
    }


def _build_index_trades(
    candles: dict[str, pd.DataFrame],
    live_overrides: dict[str, dict[str, float]] | None = None,
) -> list[dict]:
    trades = []
    overrides = live_overrides or {}
    for idx_sym in ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]:
        df = candles.get(idx_sym)
        if df is None or df.empty:
            continue
        ind = compute_indicators(df)
        if not ind:
            continue

        ov = overrides.get(idx_sym) or {}
        spot = float(ov.get("close") or ind["close"])
        chg_pct = float(ov.get("change_pct") if "change_pct" in ov else (ind.get("change_pct") or 0.0))
        atr_val = float(ind.get("atr") or max(spot * 0.01, 1.0))
        ema20 = float(ind.get("ema20") or spot)
        ema50 = float(ind.get("ema50") or spot)
        rsi = float(ind.get("rsi") or 50.0)
        vol_ratio = float(ind.get("vol_ratio") or 1.0)
        session_vwap = _compute_session_vwap(df, live_vwap=ov.get("vwap"), lookback_days=1)
        is_bull = spot >= ema20 and chg_pct >= -0.35
        pa_extra = _extract_sparkline_and_patterns(df, spot)

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

        levels_and_prob = _compute_support_resistance_and_probability(
            df=df,
            close=spot,
            atr=atr_val,
            ema20=ema20,
            ema50=ema50,
            rsi=rsi,
            vol_ratio=vol_ratio,
            rs_score=0.0,
            sector_chg_pct=chg_pct,
            is_bull=is_bull,
            chain_summary=chain_summary,
        )

        plan = select_option_strategy(
            symbol=idx_sym,
            side="CE" if is_bull else "PE",
            spot=spot,
            chain_df=chain_df,
            expiry=expiry,
            prefer_spread=True,
        )
        if not isinstance(plan, dict):
            plan = {}

        lot_size = _resolve_lot_size(idx_sym, plan)
        plan["lot_size"] = lot_size
        leg_1pct = _select_option_leg_1pct_risk(
            symbol=idx_sym,
            side="CE" if is_bull else "PE",
            spot=spot,
            chain_df=chain_df,
            expiry=expiry,
            lot_size=lot_size,
        )

        sl = round(spot - 1.5 * atr_val, 2) if is_bull else round(spot + 1.5 * atr_val, 2)
        pa_t1 = levels_and_prob["pa_target_1"]
        pa_t2 = levels_and_prob["pa_target_2"]
        oi_t = levels_and_prob["oi_target"]
        prob_pct = levels_and_prob["move_probability_pct"]
        max_loss_lot = leg_1pct.get("max_risk") if leg_1pct.get("available") else plan.get("max_loss_lot")

        trades.append({
            "symbol": idx_sym,
            "sector": "INDEX",
            "side": "BULLISH" if is_bull else "BEARISH",
            "entry": round(spot, 2),
            "close": round(spot, 2),
            "spot": round(spot, 2),
            "change_pct": round(chg_pct, 2),
            "atr": round(atr_val, 2),
            "stop_loss": sl,
            "target_1": pa_t1,
            "target_2": pa_t2,
            "pa_target_1": pa_t1,
            "pa_target_2": pa_t2,
            "oi_target": oi_t,
            "immediate_support": levels_and_prob["immediate_support"],
            "immediate_resistance": levels_and_prob["immediate_resistance"],
            "move_probability_pct": prob_pct,
            "vwap": session_vwap,
            "qty": lot_size,
            "est_margin": max_loss_lot,
            "exposure": max_loss_lot,
            "max_risk": max_loss_lot,
            "sizing_status": leg_1pct.get("reason") if not leg_1pct.get("available") else "1% Risk Cap",
            "option_leg_1pct": leg_1pct,
            "chain_summary": chain_summary,
            "option_expiry": expiry,
            "option_plan": plan,
            "sparkline_10d": pa_extra["sparkline_10d"],
            "setup_tags": pa_extra["setup_tags"],
            "why_trade": (
                f"{'Bullish' if is_bull else 'Bearish'} index ({chg_pct:+.2f}%), Prob {prob_pct:.0f}%, "
                f"Sup {levels_and_prob['immediate_support']} / Res {levels_and_prob['immediate_resistance']}, EMA20 {ema20:.2f}."
            ),
        })
    return trades


def export_web_dashboard_data(payload: dict, base_dir: str = "docs/data") -> None:
    root = Path(base_dir)
    history_dir = root / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    now_ist = dt.datetime.now(IST)
    session_date = now_ist.date().isoformat()
    mode_tag = RUN_MODE

    enriched = dict(payload)
    enriched["mode"] = mode_tag
    enriched["generated_at_ist"] = now_ist.strftime("%Y-%m-%d %H:%M:%S IST")
    enriched["session_date"] = session_date

    (root / "latest.json").write_text(json.dumps(enriched, indent=2, default=str), encoding="utf-8")
    (history_dir / f"{session_date}.json").write_text(json.dumps(enriched, indent=2, default=str), encoding="utf-8")
    (history_dir / f"{session_date}_{mode_tag}.json").write_text(json.dumps(enriched, indent=2, default=str), encoding="utf-8")

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

    (root / "manifest.json").write_text(
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
    now_ist = dt.datetime.now(IST)
    confirm_orb_15m = (RUN_MODE == "MORNING") and ((now_ist.hour * 60 + now_ist.minute) >= (9 * 60 + 28))

    candles, data_source, live_overrides = load_candles_1y()
    logging.info("Loaded candles for %d symbols using %s (mode=%s)", len(candles), data_source, RUN_MODE)

    global_cues = fetch_global_cues()
    nse_live_snap = _fetch_nse_mcp_live_snapshot()
    sectors = compute_sector_strength(candles)
    rs_map = compute_relative_strength(candles)
    sector_map_pct = {str(s.get("name") or "").upper(): float(s.get("raw_avg") or 0.0) for s in (sectors or [])}

    nifty_ov = live_overrides.get("NIFTY") or {}
    nifty_gap_info = _compute_intraday_gap_and_orb(
        "NIFTY", nifty_ov, candles.get("NIFTY"), nifty_gap_pct=0.0, confirm_orb_15m=confirm_orb_15m
    )
    nifty_gap_pct = float(nifty_gap_info.get("gap_pct") or 0.0)

    nifty_df = candles.get("NIFTY")
    nifty_5d_pct = 0.0
    if nifty_df is not None and len(nifty_df) >= 6:
        nc = pd.to_numeric(nifty_df["Close"], errors="coerce").dropna()
        if len(nc) >= 6 and float(nc.iloc[-6]) > 0:
            nifty_5d_pct = ((float(nc.iloc[-1]) / float(nc.iloc[-6])) - 1.0) * 100.0

    indicators_by_sym: dict[str, dict] = {}
    gap_by_sym: dict[str, dict] = {}
    adv, dec, above20, above50, total_stocks = 0, 0, 0, 0, 0

    for sym in STOCKS_UNIVERSE:
        df = candles.get(sym)
        if df is None or df.empty:
            continue
        ind = compute_indicators(df)
        if not ind:
            continue

        ov = live_overrides.get(sym) or {}
        close = float(ov.get("close") or ind["close"])
        chg = float(ov.get("change_pct") if "change_pct" in ov else (ind.get("change_pct") or 0.0))
        ind["close"] = close
        ind["change_pct"] = chg
        ind["close_pos_ratio"] = _compute_close_position_ratio(df, close)
        ind["vwap"] = _compute_session_vwap(df, live_vwap=ov.get("vwap"), lookback_days=1)
        ind["avg_range_rs"] = _compute_avg_daily_range_rs(df, lookback=14)
        vol_20 = pd.to_numeric(df["Volume"], errors="coerce").tail(20).mean()
        close_20 = pd.to_numeric(df["Close"], errors="coerce").tail(20).mean()
        ind["avg_turnover"] = float((vol_20 or 0.0) * (close_20 or close))

        indicators_by_sym[sym] = ind
        if RUN_MODE == "MORNING":
            gap_by_sym[sym] = _compute_intraday_gap_and_orb(
                sym, ov, df, nifty_gap_pct=nifty_gap_pct, confirm_orb_15m=confirm_orb_15m
            )

        total_stocks += 1
        if chg > 0:
            adv += 1
        elif chg < 0:
            dec += 1
        if close >= float(ind.get("ema20") or close):
            above20 += 1
        if close >= float(ind.get("ema50") or close):
            above50 += 1

    fo_stocks_syms = get_nse_fo_option_stocks(candles=candles)
    if not fo_stocks_syms:
        fo_stocks_syms = [
            s for s in STOCKS_UNIVERSE
            if s in indicators_by_sym and s not in {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"}
        ]
    else:
        fo_stocks_syms = [s for s in fo_stocks_syms if s in indicators_by_sym]

    fo_stocks_syms.sort(
        key=lambda s: max(
            float(indicators_by_sym[s].get("bull_score") or 0.0),
            float(indicators_by_sym[s].get("bear_score") or 0.0),
        ),
        reverse=True,
    )

    bull_candidates, bear_candidates = [], []
    chain_cache: dict[str, dict] = {}
    chain_fetches = 0
    max_chain_fetches = max(len(fo_stocks_syms), 40)

    def _get_chain(sym: str, spot: float) -> dict:
        nonlocal chain_fetches
        if sym in chain_cache:
            return chain_cache[sym]
        if chain_fetches >= max_chain_fetches:
            return {"df": None, "expiry": None, "summary": {}}
        chain_fetches += 1
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

    for sym in fo_stocks_syms:
        ind = indicators_by_sym[sym]
        df = candles[sym]
        rs_info = rs_map.get(sym, {})
        sec_pct = sector_map_pct.get(SECTOR_MAP.get(sym, "OTHER").upper(), 0.0)
        gm = gap_by_sym.get(sym)
        cb = _get_chain(sym, float(ind["close"]))

        if RUN_MODE == "BTST":
            bull_setup = _build_stock_setup(
                sym, ind, df, rs_info, True, cb,
                nifty_5d_pct=nifty_5d_pct, sector_chg_pct=sec_pct, gap_meta=gm,
            )
            if bull_setup:
                bull_candidates.append(bull_setup)

            bear_setup = _build_stock_setup(
                sym, ind, df, rs_info, False, cb,
                nifty_5d_pct=nifty_5d_pct, sector_chg_pct=sec_pct, gap_meta=gm,
            )
            if bear_setup:
                bear_candidates.append(bear_setup)
        else:
            b_score = float(ind.get("bull_score") or 0.0)
            s_score = float(ind.get("bear_score") or 0.0)
            if b_score >= s_score and b_score >= 48.0:
                setup = _build_stock_setup(
                    sym, ind, df, rs_info, True, cb,
                    nifty_5d_pct=nifty_5d_pct, sector_chg_pct=sec_pct, gap_meta=gm,
                )
                if setup:
                    bull_candidates.append(setup)
            elif s_score > b_score and s_score >= 48.0:
                setup = _build_stock_setup(
                    sym, ind, df, rs_info, False, cb,
                    nifty_5d_pct=nifty_5d_pct, sector_chg_pct=sec_pct, gap_meta=gm,
                )
                if setup:
                    bear_candidates.append(setup)

    bull_candidates.sort(
        key=lambda x: (float(x.get("move_probability_pct") or 0.0), float(x.get("score") or 0.0)),
        reverse=True,
    )
    bear_candidates.sort(
        key=lambda x: (float(x.get("move_probability_pct") or 0.0), float(x.get("score") or 0.0)),
        reverse=True,
    )

    def _select_sector_balanced(pool: list[dict], limit: int, max_per_sector: int = 2) -> list[dict]:
        picked: list[dict] = []
        sector_counts: dict[str, int] = {}
        for item in pool:
            if len(picked) >= limit:
                break
            sec = str(item.get("sector") or "OTHER").strip().upper()
            if sector_counts.get(sec, 0) < max_per_sector:
                picked.append(item)
                sector_counts[sec] = sector_counts.get(sec, 0) + 1
        if len(picked) < limit:
            already = {id(x) for x in picked}
            for item in pool:
                if len(picked) >= limit:
                    break
                if id(item) not in already:
                    picked.append(item)
                    already.add(id(item))
        return picked

    # Strictly 10 Bullish + 10 Bearish Option Setups (20 total)
    options_bullish = _select_sector_balanced(bull_candidates, limit=10, max_per_sector=2)
    options_bearish = _select_sector_balanced(bear_candidates, limit=10, max_per_sector=2)

    used_syms = {str(x.get("symbol") or "").upper() for x in (options_bullish + options_bearish)}

    eq_bull_pool = [
        x for x in bull_candidates
        if str(x.get("symbol") or "").upper() not in used_syms
    ]
    eq_bear_pool = [
        x for x in bear_candidates
        if str(x.get("symbol") or "").upper() not in used_syms
    ]

    # Strictly 20 Equity Setups: 10 Bullish + 10 Bearish (sector-wise balanced)
    intraday_bullish = _select_sector_balanced(eq_bull_pool, limit=10, max_per_sector=2)
    intraday_bearish = _select_sector_balanced(eq_bear_pool, limit=10, max_per_sector=2)
    equity_intraday = intraday_bullish + intraday_bearish

    # Full rank-ordered and sector-grouped list for all analyzed stocks
    all_evaluated_stocks: list[dict[str, Any]] = []
    seen_eval_syms: set[str] = set()
    for item in (bull_candidates + bear_candidates):
        sym_key = str(item.get("symbol") or "").strip().upper()
        if sym_key and sym_key not in seen_eval_syms:
            seen_eval_syms.add(sym_key)
            all_evaluated_stocks.append(dict(item))

    all_evaluated_stocks.sort(
        key=lambda x: (
            float(x.get("move_probability_pct") or 0.0),
            float(x.get("score") or 0.0),
        ),
        reverse=True,
    )

    top_40_syms = {
        str(x.get("symbol") or "").strip().upper()
        for x in (options_bullish + options_bearish + equity_intraday)
    }

    ranked_stocks: list[dict[str, Any]] = []
    stocks_by_sector: dict[str, list[dict[str, Any]]] = {}

    for idx_pos, st_item in enumerate(all_evaluated_stocks, start=1):
        sym_u = str(st_item.get("symbol") or "").strip().upper()
        sec_u = str(st_item.get("sector") or "OTHER").strip().upper()
        entry_rec = {
            "rank": idx_pos,
            "is_top_40": sym_u in top_40_syms,
            "symbol": sym_u,
            "sector": sec_u,
            "side": st_item.get("side"),
            "score": st_item.get("score"),
            "move_probability_pct": st_item.get("move_probability_pct"),
            "entry": st_item.get("entry"),
            "stop_loss": st_item.get("stop_loss"),
            "pa_target_1": st_item.get("pa_target_1"),
            "pa_target_2": st_item.get("pa_target_2"),
            "oi_target": st_item.get("oi_target"),
            "immediate_support": st_item.get("immediate_support"),
            "immediate_resistance": st_item.get("immediate_resistance"),
            "change_pct": st_item.get("change_pct"),
        }
        ranked_stocks.append(entry_rec)
        stocks_by_sector.setdefault(sec_u, []).append(entry_rec)

    index_trades = _build_index_trades(candles, live_overrides=live_overrides)
    smartlists = build_upstox_smartlists_and_movers(candles, live_overrides=live_overrides)

    def _idx_fmt(sym: str):
        ov = live_overrides.get(sym)
        if ov and ov.get("close"):
            c_last = float(ov["close"])
            chg = float(ov.get("change_pct") or 0.0)
            return f"{c_last:,.2f}", f"{chg:+.2f}%"
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

    stock_setups_count = len(options_bullish) + len(options_bearish) + len(equity_intraday)
    total_ideas = len(index_trades) + stock_setups_count

    payload = {
        "title": TITLE,
        "mode": RUN_MODE,
        "summary": {
            "ideas_count": total_ideas,
            "stock_setups_count": stock_setups_count,
            "fo_stocks_analyzed": len(fo_stocks_syms),
            "total_ranked_stocks": len(ranked_stocks),
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
            "nse_mcp_live_active": bool(nse_live_snap),
        },
        "global_cues": global_cues or [],
        "sectors": sectors,
        "smartlists": smartlists,
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
        "ranked_stocks": ranked_stocks,
        "stocks_by_sector": stocks_by_sector,
    }

    eval_summary = evaluate_signals_chronologically(candles_by_symbol=candles)
    log_signals(payload)
    payload["walk_forward"] = walk_forward_summary()
    payload["performance"] = category_performance_summary()
    payload["tracker_health"] = eval_summary
    export_web_dashboard_data(payload)

    send_alert(payload)
    logging.info(
        "Dispatched %d total ideas (%d top setups + %d ranked stocks across %d F&O stocks) using %s",
        total_ideas,
        stock_setups_count,
        len(ranked_stocks),
        len(fo_stocks_syms),
        data_source,
    )


if __name__ == "__main__":
    run_screener()
