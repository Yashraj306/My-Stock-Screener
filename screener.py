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
from universe import BSE_STOCKS_UNIVERSE, LOT_SIZES, STOCKS_UNIVERSE, eq_keys

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
UPSTOX_ACCESS_TOKEN = (
    os.getenv("UPSTOX_ACCESS_TOKEN")
    or os.getenv("UPSTOX_TOKEN")
    or ""
).strip()
UPSTOX_BASE_V2 = "https://api.upstox.com/v2"

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
    """How close the current/last price is to the day's high (0.0 = low, 1.0 = high)."""
    if df is None or getattr(df, "empty", True):
        return 0.5
    last_row = df.iloc[-1]
    h = float(last_row.get("High") or close)
    l = float(last_row.get("Low") or close)
    if h > l:
        ratio = (close - l) / (h - l)
        return round(max(0.0, min(1.0, ratio)), 2)
    return 0.5


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


def _extract_rows_from_mcp_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("data", "records", "candles", "history", "rows", "items", "result"):
            val = payload.get(key)
            if isinstance(val, list):
                return [r for r in val if isinstance(r, dict)]
    return []


def _fetch_nse_mcp_bhavcopy_1y(symbol: str) -> pd.DataFrame | None:
    if not ENABLE_NSE_MCP or not _MCP_AVAILABLE or symbol in _BSE_SET:
        return None
    if symbol in {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"}:
        return None

    end_dt = dt.datetime.now(IST).date()
    start_dt = end_dt - dt.timedelta(days=370)
    cur_end = end_dt
    all_rows: list[dict[str, Any]] = []

    for _ in range(3):
        chunk_start = max(start_dt, cur_end - dt.timedelta(days=120))
        data = call_nse_mcp_tool(
            NSE_BHAVCOPY_MCP_URL,
            "get_stock_history",
            {
                "symbol": symbol,
                "startDate": chunk_start.isoformat(),
                "endDate": cur_end.isoformat(),
            },
        )
        rows = _extract_rows_from_mcp_payload(data)
        if not rows:
            break
        all_rows.extend(rows)
        cur_end = chunk_start - dt.timedelta(days=1)
        if cur_end <= start_dt:
            break

    if not all_rows:
        return None

    norm_rows = []
    for r in all_rows:
        dt_val = r.get("Date") or r.get("date") or r.get("CH_TIMESTAMP") or r.get("timestamp")
        o_val = r.get("Open") if r.get("Open") is not None else (r.get("open") if r.get("open") is not None else r.get("CH_OPENING_PRICE"))
        h_val = r.get("High") if r.get("High") is not None else (r.get("high") if r.get("high") is not None else r.get("CH_TRADE_HIGH_PRICE"))
        l_val = r.get("Low") if r.get("Low") is not None else (r.get("low") if r.get("low") is not None else r.get("CH_TRADE_LOW_PRICE"))
        c_val = r.get("Close") if r.get("Close") is not None else (r.get("close") if r.get("close") is not None else r.get("CH_CLOSING_PRICE"))
        v_val = r.get("Volume") if r.get("Volume") is not None else (r.get("volume") if r.get("volume") is not None else r.get("CH_TOT_TRADED_QTY"))
        if dt_val and c_val is not None:
            norm_rows.append({
                "Date": str(dt_val)[:10],
                "Open": o_val if o_val is not None else c_val,
                "High": h_val if h_val is not None else c_val,
                "Low": l_val if l_val is not None else c_val,
                "Close": c_val,
                "Volume": v_val if v_val is not None else 0,
            })

    cleaned = clean_ohlcv(pd.DataFrame(norm_rows))
    if cleaned is not None and len(cleaned) >= 20:
        return cleaned
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
            "change_pct": round(chg_pct, 2),
            "vwap": float(q.get("vwap") or 0.0),
        }

    return applied


def _fetch_yf_1y(symbol: str) -> pd.DataFrame | None:
    global _WARNED_YF_COUNT
    primary_ticker = _yf_ticker(symbol)
    try:
        df = yf.download(
            primary_ticker,
            period="1y",
            interval="1d",
            progress=False,
            threads=False,
            timeout=6,
            auto_adjust=False,
        )
        cleaned_raw = _clean_yf_duplicates(df)
        norm_dl = clean_ohlcv(cleaned_raw) if cleaned_raw is not None else None
        if norm_dl is not None and len(norm_dl) >= 20:
            return norm_dl
    except Exception as exc:
        if _WARNED_YF_COUNT < 6:
            _WARNED_YF_COUNT += 1
            logging.warning("YF %s err: %s", primary_ticker, exc)
    return None


def load_candles_1y() -> tuple[dict[str, pd.DataFrame], str, dict[str, dict[str, float]]]:
    keys = eq_keys()
    candles: dict[str, pd.DataFrame] = {}
    upstox_hits = 0
    mcp_hits = 0

    def _worker(sym: str):
        df = _fetch_upstox_1y(keys.get(sym, ""), symbol=sym)
        if df is not None and not df.empty:
            return sym, df, "UPSTOX"
        yf_df = _fetch_yf_1y(sym)
        if yf_df is not None and not yf_df.empty:
            return sym, yf_df, "YF"
        if ENABLE_NSE_MCP:
            mcp_df = _fetch_nse_mcp_bhavcopy_1y(sym)
            if mcp_df is not None and not mcp_df.empty:
                return sym, mcp_df, "NSE_MCP"
        return sym, None, "NONE"

    with ThreadPoolExecutor(max_workers=12) as pool:
        for sym, df, src in pool.map(_worker, list(keys.keys())):
            if df is not None and not df.empty:
                candles[sym] = df
                if src == "UPSTOX":
                    upstox_hits += 1
                elif src == "NSE_MCP":
                    mcp_hits += 1

    live_quotes = _fetch_upstox_live_quotes(keys)
    live_overrides = _apply_live_quotes_to_candles(candles, live_quotes)

    parts = []
    if live_overrides:
        parts.append(f"UPSTOX_LIVE({len(live_overrides)})")
    if upstox_hits > 0:
        parts.append("UPSTOX_V2")
    if mcp_hits > 0:
        parts.append(f"NSE_MCP({mcp_hits})")
    if not parts or (upstox_hits + mcp_hits) < len(candles):
        parts.append("YFINANCE_1Y")
    source_label = " + ".join(parts) + " (NSE+BSE)"
    return candles, source_label, live_overrides


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
    empty_res = {
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
    if chain_df is None or getattr(chain_df, "empty", True):
        return empty_res

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
        return empty_res

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
        return empty_res

    closest_idx = min(range(len(rows)), key=lambda i: abs(rows[i]["strike"] - spot))
    rows[closest_idx]["is_atm"] = True

    strike_step = 50.0
    if len(rows) >= 2:
        diffs = [
            round(rows[i + 1]["strike"] - rows[i]["strike"], 2)
            for i in range(len(rows) - 1)
            if rows[i + 1]["strike"] > rows[i]["strike"]
        ]
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

    start_i = max(0, closest_idx - 15)
    end_i = min(len(rows), closest_idx + 16)
    trimmed_rows = rows[start_i:end_i]

    return {
        "rows": trimmed_rows,
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
    if not chain_summary or not chain_summary.get("chain_available") or entry <= 0:
        return None
    sup = chain_summary.get("support_strike")
    res = chain_summary.get("resistance_strike")
    if sup is not None and res is not None and float(sup) == float(res):
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


def _select_capped_items(
    items: list[dict],
    max_items: int,
    enforce_risk_cap: bool = True,
    excluded_symbols: set[str] | None = None,
) -> list[dict]:
    selected: list[dict] = []
    seen_syms: set[str] = set()
    excluded = {str(s).strip().upper() for s in (excluded_symbols or set())}
    sector_counts: dict[str, int] = {}
    total_risk = 0.0
    portfolio_risk_cap = CAPITAL * MAX_PORTFOLIO_RISK_PCT if (CAPITAL_CONFIGURED and enforce_risk_cap) else float("inf")

    for item in items:
        if len(selected) >= max_items:
            break
        sym = str(item.get("symbol") or "").strip().upper()
        if not sym or sym in seen_syms or sym in excluded:
            continue
        sec = str(item.get("sector") or "OTHER").upper()
        if sector_counts.get(sec, 0) >= max(MAX_SECTOR_POSITIONS, 2):
            continue
        trade_risk = float(item.get("max_risk") or 0.0)
        if enforce_risk_cap and CAPITAL_CONFIGURED and trade_risk > 0 and (total_risk + trade_risk) > portfolio_risk_cap:
            continue
        selected.append(item)
        seen_syms.add(sym)
        sector_counts[sec] = sector_counts.get(sec, 0) + 1
        total_risk += trade_risk
    return selected


def _load_previous_morning_equity_symbols(session_date: str, current_mode: str, base_dir: str = "docs/data") -> set[str]:
    if str(current_mode or "").upper() != "BTST":
        return set()
    used: set[str] = set()
    history_dir = Path(base_dir) / "history"
    if not history_dir.exists():
        return used

    candidates = [
        history_dir / f"{session_date}_MORNING.json",
        history_dir / f"{session_date}.json",
    ]
    for p in candidates:
        if not p.exists():
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if p.name == f"{session_date}.json" and str(raw.get("mode") or "").upper() != "MORNING":
                continue
            for item in (raw.get("equity_intraday") or []):
                sym = str((item or {}).get("symbol") or "").strip().upper()
                if sym:
                    used.add(sym)
        except Exception:
            continue
    return used


def _btst_gapup_score(item: dict) -> float:
    """Dedicated BTST scoring: prioritizes stocks likely to gap-up or make a big overnight move."""
    chg = float(item.get("change_pct") or 0.0)
    rng = abs(float(item.get("avg_range_rs") or 0.0))
    rsi = float(item.get("rsi") or 50.0)
    vol = float(item.get("vol_ratio") or 1.0)
    close_pos = float(item.get("close_pos_ratio") or 0.5)
    has_oc = 1.0 if item.get("has_option_chain") else 0.0
    sent = str(((item.get("chain_analytics") or {}).get("sentiment_state")) or "").upper()

    gapup_flag = 1.0 if (
        chg >= 0.8
        and close_pos >= 0.60
        and vol >= 1.0
        and 50.0 <= rsi <= 78.0
    ) else 0.0

    oc_bull_bonus = 10.0 if ("BULLISH" in sent or "WTT" in sent) else (4.0 if has_oc else 0.0)
    move_bonus = 16.0 if rng >= 15.0 else (10.0 if rng >= 10.0 else 0.0)
    chg_bonus = min(max(chg, 0.0) * 3.5, 22.0)

    return (
        float(item.get("score") or 0.0)
        + (gapup_flag * 25.0)
        + chg_bonus
        + move_bonus
        + oc_bull_bonus
        + (close_pos * 10.0)
    )


def _select_btst_setups_10(
    bull_candidates: list[dict],
    bear_candidates: list[dict],
    excluded_symbols: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Dedicated BTST selector: strictly gap-up / big-overnight-mover candidates (no intraday pullback logic)."""
    excluded = {str(s).strip().upper() for s in (excluded_symbols or set())}

    gapup_pool = [
        it for it in bull_candidates
        if float(it.get("change_pct") or 0.0) >= 0.5
        and float(it.get("close_pos_ratio") or 0.5) >= 0.50
    ]
    ranked_bull = sorted(gapup_pool or bull_candidates, key=_btst_gapup_score, reverse=True)
    ranked_bear = sorted(
        [it for it in bear_candidates if float(it.get("change_pct") or 0.0) <= -1.0 and float(it.get("close_pos_ratio") or 0.5) <= 0.45],
        key=lambda x: abs(float(x.get("change_pct") or 0.0)) + abs(float(x.get("avg_range_rs") or 0.0)) * 0.2,
        reverse=True,
    )

    bull_picked: list[dict] = []
    bear_picked: list[dict] = []
    chosen_syms: set[str] = set()

    for it in ranked_bull:
        if len(bull_picked) >= 8 or (len(bull_picked) + len(bear_picked)) >= 10:
            break
        sym = str(it.get("symbol") or "").strip().upper()
        if not sym or sym in chosen_syms or sym in excluded:
            continue
        item = dict(it)
        chg_v = float(item.get("change_pct") or 0.0)
        rng_v = float(item.get("avg_range_rs") or 0.0)
        cpos = float(item.get("close_pos_ratio") or 0.5)
        item["btst_candidate"] = True
        item["btst_bias"] = "GAPUP_CANDIDATE"
        item["equity_bucket"] = f"BTST GAP-UP ({chg_v:+.2f}%, ClsPos {cpos:.2f}, Rng ₹{rng_v:.1f})"
        bull_picked.append(item)
        chosen_syms.add(sym)

    for it in ranked_bear:
        if (len(bull_picked) + len(bear_picked)) >= 10:
            break
        sym = str(it.get("symbol") or "").strip().upper()
        if not sym or sym in chosen_syms or sym in excluded:
            continue
        item = dict(it)
        chg_v = float(item.get("change_pct") or 0.0)
        rng_v = float(item.get("avg_range_rs") or 0.0)
        item["btst_candidate"] = True
        item["btst_bias"] = "GAPDOWN_WATCH"
        item["equity_bucket"] = f"BTST GAP-DOWN ({chg_v:+.2f}%, Rng ₹{rng_v:.1f})"
        bear_picked.append(item)
        chosen_syms.add(sym)

    if len(bull_picked) + len(bear_picked) < 10:
        for it in sorted(bull_candidates, key=_btst_gapup_score, reverse=True):
            if len(bull_picked) + len(bear_picked) >= 10:
                break
            sym = str(it.get("symbol") or "").strip().upper()
            if not sym or sym in chosen_syms:
                continue
            item = dict(it)
            chg_v = float(item.get("change_pct") or 0.0)
            rng_v = float(item.get("avg_range_rs") or 0.0)
            item["btst_candidate"] = True
            item["btst_bias"] = "GAPUP_CANDIDATE"
            item["equity_bucket"] = f"BTST GAP-UP ({chg_v:+.2f}%, Rng ₹{rng_v:.1f})"
            bull_picked.append(item)
            chosen_syms.add(sym)

    return bull_picked, bear_picked


def _select_intraday_equity_10(
    bull_candidates: list[dict],
    bear_candidates: list[dict],
    excluded_symbols: set[str] | None = None,
    mode_tag: str = "MORNING",
) -> tuple[list[dict], list[dict]]:
    m_up = str(mode_tag or "MORNING").upper()
    if m_up == "BTST":
        return _select_btst_setups_10(bull_candidates, bear_candidates, excluded_symbols=excluded_symbols)

    excluded = {str(s).strip().upper() for s in (excluded_symbols or set())}

    def _rank_pool(pool: list[dict]) -> list[dict]:
        return sorted(
            pool,
            key=lambda x: (
                1 if abs(float(x.get("avg_range_rs") or 0.0)) >= 10.0 else 0,
                float(x.get("score") or 0.0),
                abs(float(x.get("change_pct") or 0.0)),
            ),
            reverse=True,
        )

    bull_picked: list[dict] = []
    bear_picked: list[dict] = []
    chosen_syms: set[str] = set()

    for pool, target_list in (
        (_rank_pool(bull_candidates), bull_picked),
        (_rank_pool(bear_candidates), bear_picked),
    ):
        for it in pool:
            if len(bull_picked) + len(bear_picked) >= 10 or len(target_list) >= 6:
                break
            sym = str(it.get("symbol") or "").strip().upper()
            if not sym or sym in chosen_syms or sym in excluded:
                continue
            enriched_item = dict(it)
            rng_val = float(enriched_item.get("avg_range_rs") or 0.0)
            chg_val = float(enriched_item.get("change_pct") or 0.0)
            oc_tag = "OC" if enriched_item.get("has_option_chain") else "EQ"
            enriched_item["equity_bucket"] = f"{m_up} {oc_tag} ({chg_val:+.2f}%, Rng ₹{rng_val:.1f})"
            target_list.append(enriched_item)
            chosen_syms.add(sym)

    if len(bull_picked) + len(bear_picked) < 10:
        combined = _rank_pool(bull_candidates + bear_candidates)
        for it in combined:
            if len(bull_picked) + len(bear_picked) >= 10:
                break
            sym = str(it.get("symbol") or "").strip().upper()
            if not sym or sym in chosen_syms:
                continue
            enriched_item = dict(it)
            rng_val = float(enriched_item.get("avg_range_rs") or 0.0)
            chg_val = float(enriched_item.get("change_pct") or 0.0)
            oc_tag = "OC" if enriched_item.get("has_option_chain") else "EQ"
            enriched_item["equity_bucket"] = f"{m_up} {oc_tag} ({chg_val:+.2f}%, Rng ₹{rng_val:.1f})"
            if str(it.get("side") or "").upper() == "BULLISH":
                bull_picked.append(enriched_item)
            else:
                bear_picked.append(enriched_item)
            chosen_syms.add(sym)

    return bull_picked, bear_picked


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
    avg_range_rs: float = 0.0,
    close_pos_ratio: float = 0.5,
    is_btst: bool = False,
) -> dict:
    atr_safe = max(float(atr_val or 0.0), 0.05)
    anchor = float(vwap) if (vwap is not None and float(vwap) > 0) else ema20
    dist_from_anchor_atr = abs(close - anchor) / atr_safe

    if is_btst and is_bull:
        gapup_likely = (chg_pct >= 0.8 and close_pos_ratio >= 0.60 and vol_ratio >= 1.0)
        best_low = round(max(close - 0.5 * atr_safe, anchor), 2)
        best_high = round(close, 2)
        if best_low > best_high:
            best_low, best_high = best_high, best_low
        if gapup_likely:
            extension_status = "BTST GAP-UP CANDIDATE (Strong Close)"
            entry_action = "HOLD OVERNIGHT FOR NEXT-DAY GAP/MOVE"
        else:
            extension_status = "BTST WATCH / MODERATE MOMENTUM"
            entry_action = "VERIFY CLOSE & OI BEFORE OVERNIGHT HOLD"
    elif is_bull:
        overextended = (rsi >= 82.0) or (chg_pct >= 8.0) or (close > anchor and dist_from_anchor_atr >= 2.5)
        best_low = round(max(min(anchor, close) - 0.25 * atr_safe, close - 1.0 * atr_safe), 2)
        best_high = round(max(anchor, close - 0.35 * atr_safe), 2)
        if best_low > best_high:
            best_low, best_high = best_high, best_low
        if overextended:
            extension_status = "EXTENDED / PULLBACK WATCH"
            entry_action = "PULLBACK OR BREAKOUT CONFIRM"
        elif dist_from_anchor_atr > 1.1:
            extension_status = "EARLY RALLY / STRETCHED UP"
            entry_action = "PREFER PULLBACK TO BEST ZONE"
        else:
            extension_status = "EARLY RALLY / IN-ZONE"
            entry_action = "IMMEDIATE OR BEST ZONE"
    else:
        overextended = (rsi <= 18.0) or (chg_pct <= -8.0) or (close < anchor and dist_from_anchor_atr >= 2.5)
        best_low = round(min(anchor, close + 0.35 * atr_safe), 2)
        best_high = round(min(max(anchor, close) + 0.25 * atr_safe, close + 1.0 * atr_safe), 2)
        if best_low > best_high:
            best_low, best_high = best_high, best_low
        if overextended:
            extension_status = "EXTENDED DOWN / BOUNCE WATCH"
            entry_action = "BOUNCE OR BREAKDOWN CONFIRM"
        elif dist_from_anchor_atr > 1.1:
            extension_status = "EARLY DROP / STRETCHED DOWN"
            entry_action = "PREFER BOUNCE TO BEST ZONE"
        else:
            extension_status = "EARLY DROP / IN-ZONE"
            entry_action = "IMMEDIATE OR BEST ZONE"

    oi_txt = ""
    if chain_summary and chain_summary.get("oi_build_up"):
        oi_txt = f" OI: {chain_summary.get('oi_build_up')} (PCR {chain_summary.get('pcr')})."

    ca = chain_analytics or {}
    lvl_txt = ""
    if ca.get("eos") is not None or ca.get("eor") is not None:
        lvl_txt = f" EOS {ca.get('eos')} / EOR {ca.get('eor')} [{ca.get('sentiment_state')}]."

    rng_txt = f" AvgRng ₹{avg_range_rs:.1f}." if avg_range_rs > 0 else ""
    vwap_txt = f"VWAP {vwap:.2f}" if (vwap is not None and vwap > 0) else "VWAP N/A"
    dir_txt = "Bullish alignment" if is_bull else "Bearish alignment"
    btst_txt = f" ClsPos {close_pos_ratio:.2f}." if is_btst else ""
    why_trade = (
        f"{dir_txt} ({chg_pct:+.2f}%), {'above' if close >= ema20 else 'below'} EMA20 ({ema20:.2f}), "
        f"{vwap_txt}, RSI {rsi:.1f}, Vol {vol_ratio:.2f}x, "
        f"RS vs Nifty {rs_score:+.2f}% in {sector}.{btst_txt}{rng_txt}{oi_txt}{lvl_txt}"
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
    if close <= 0:
        return None
    atr_val = float(ind.get("atr") or max(close * 0.015, 1.0))
    vwap = ind.get("vwap")
    ema20 = float(ind.get("ema20") or close)
    ema50 = float(ind.get("ema50") or close)
    rsi = float(ind.get("rsi") or 50.0)
    vol_ratio = float(ind.get("vol_ratio") or 1.0)
    chg_pct = float(ind.get("change_pct") or 0.0)
    avg_turnover = float(ind.get("avg_turnover") or 0.0)
    avg_range_rs = float(ind.get("avg_range_rs") or 0.0)
    close_pos_ratio = float(ind.get("close_pos_ratio") or 0.5)
    has_oc = _has_option_chain_support(sym, chain_bundle)
    is_btst = str(MODE or "").upper() == "BTST"

    if avg_turnover > 0 and avg_turnover < max(MIN_DAILY_TURNOVER_RS * 0.5, 1_000_000):
        return None
    if vol_ratio > 0 and vol_ratio < 0.40:
        return None

    if is_btst and is_bull:
        # For BTST gap-up candidates: require positive move and price above EMA20
        if chg_pct < 0.0 or close < ema20:
            return None
        sl = round(close - 1.6 * atr_val, 2)
        pa_t1 = round(close + 2.0 * atr_val, 2)
        pa_t2 = round(close + 3.4 * atr_val, 2)
    elif is_bull:
        if chg_pct < -1.5 or close < ema20:
            return None
        sl = round(close - 1.5 * atr_val, 2)
        pa_t1 = round(close + 1.8 * atr_val, 2)
        pa_t2 = round(close + 3.0 * atr_val, 2)
    else:
        if chg_pct > 1.5 or close > ema20:
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

    sizing = _safe_position_size(entry=close, stop_loss=sl, lot_size=lot_sz)
    if int(sizing.get("qty") or 0) <= 0:
        return None

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
        avg_range_rs=avg_range_rs,
        close_pos_ratio=close_pos_ratio,
        is_btst=is_btst,
    )

    base_score = float(ind.get("bull_score" if is_bull else "bear_score") or 50.0)
    range_bonus = 18.0 if avg_range_rs >= 15.0 else (12.0 if avg_range_rs >= 10.0 else 0.0)
    oc_bonus = 8.0 if has_oc else 0.0
    if is_btst and is_bull:
        early_bonus = (12.0 if close_pos_ratio >= 0.65 else 0.0) + min(max(chg_pct, 0.0) * 2.5, 18.0)
    else:
        early_bonus = 6.0 if (1.0 <= abs(chg_pct) <= 5.5 and 45.0 <= rsi <= 76.0) else min(abs(chg_pct) * 1.5, 10.0)

    adj_score = round(
        max(0.0, min(100.0, 0.70 * base_score + 0.15 * (50.0 + rs_score * 1.5) + range_bonus + oc_bonus + early_bonus)),
        2,
    )

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
        "avg_range_rs": round(avg_range_rs, 2),
        "stop_loss": sl,
        "target_1": pa_t1,
        "target_2": pa_t2,
        "pa_target_1": pa_t1,
        "pa_target_2": pa_t2,
        "oi_target": oi_target,
        "rsi": round(rsi, 1),
        "vol_ratio": round(vol_ratio, 2),
        "vwap": round(float(vwap), 2) if (vwap is not None and float(vwap) > 0) else None,
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
        session_vwap = _compute_session_vwap(df, live_vwap=ov.get("vwap"), lookback_days=1)
        close_pos_ratio = _compute_close_position_ratio(df, spot)

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
        if not isinstance(plan, dict):
            plan = {}

        sl = round(spot - 1.5 * atr_val, 2) if is_bull else round(spot + 1.5 * atr_val, 2)
        pa_t1 = round(spot + 1.5 * atr_val, 2) if is_bull else round(max(spot - 1.5 * atr_val, 0.0), 2)
        pa_t2 = round(spot + 2.5 * atr_val, 2) if is_bull else round(max(spot - 2.5 * atr_val, 0.0), 2)
        oi_target = _compute_oi_target(chain_summary, spot, is_bull=is_bull)

        lot_size = _resolve_lot_size(idx_sym, plan)
        plan["lot_size"] = lot_size
        chain_analytics = _build_option_chain_analytics(chain_df, spot=spot, lot_size=lot_size)
        max_loss_lot = plan.get("max_loss_lot")
        sizing_status = "CAPITAL NOT CONFIGURED" if not CAPITAL_CONFIGURED else "1 Lot Risk Cap"

        rationale = _build_entry_and_rationale(
            sector="INDEX",
            is_bull=is_bull,
            close=spot,
            atr_val=atr_val,
            vwap=session_vwap,
            ema20=ema20,
            rsi=float(ind.get("rsi") or 50.0),
            vol_ratio=float(ind.get("vol_ratio") or 0.0),
            chg_pct=chg_pct,
            rs_score=0.0,
            chain_summary=chain_summary,
            chain_analytics=chain_analytics,
            close_pos_ratio=close_pos_ratio,
            is_btst=(str(MODE or "").upper() == "BTST"),
        )

        trades.append({
            "symbol": idx_sym,
            "sector": "INDEX",
            "has_option_chain": True,
            "close_pos_ratio": close_pos_ratio,
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
    enriched["mode"] = mode_tag
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
    candles, data_source, live_overrides = load_candles_1y()
    logging.info("Loaded candles for %d symbols using %s", len(candles), data_source)
    global_cues = fetch_global_cues()
    nse_live_snap = _fetch_nse_mcp_live_snapshot()

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

        ov = live_overrides.get(sym) or {}
        close = float(ov.get("close") or ind["close"])
        chg = float(ov.get("change_pct") if "change_pct" in ov else (ind.get("change_pct") or 0.0))
        ind["close"] = close
        ind["change_pct"] = chg
        ind["close_pos_ratio"] = _compute_close_position_ratio(df, close)

        session_vwap = _compute_session_vwap(df, live_vwap=ov.get("vwap"), lookback_days=1)
        avg_range_rs = _compute_avg_daily_range_rs(df, lookback=14)
        vol_20 = pd.to_numeric(df["Volume"], errors="coerce").tail(20).mean()
        close_20 = pd.to_numeric(df["Close"], errors="coerce").tail(20).mean()

        ind["vwap"] = session_vwap
        ind["avg_range_rs"] = avg_range_rs
        ind["avg_turnover"] = float((vol_20 or 0.0) * (close_20 or close))
        indicators_by_sym[sym] = ind

        total_stocks += 1
        if chg > 0:
            adv += 1
        elif chg < 0:
            dec += 1
        if close >= float(ind.get("ema20") or close):
            above20 += 1
        if close >= float(ind.get("ema50") or close):
            above50 += 1

    ranked_syms = sorted(
        indicators_by_sym.keys(),
        key=lambda s: (
            1.0 if int(LOT_SIZES.get(s.upper(), 0) or 0) > 1 else 0.0,
            1.0 if abs(float(indicators_by_sym[s].get("avg_range_rs") or 0.0)) >= 10.0 else 0.0,
            max(
                float(indicators_by_sym[s].get("bull_score") or 0.0),
                float(indicators_by_sym[s].get("bear_score") or 0.0),
            )
            + abs(float(indicators_by_sym[s].get("change_pct") or 0.0)) * 2.0,
        ),
        reverse=True,
    )

    bull_candidates, bear_candidates = [], []
    chain_cache: dict[str, dict] = {}
    chain_fetches = 0
    max_chain_fetches = 28

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

    for sym in ranked_syms:
        ind = indicators_by_sym[sym]
        b_score = float(ind.get("bull_score") or 0.0)
        s_score = float(ind.get("bear_score") or 0.0)
        rs_info = rs_map.get(sym, {})
        if b_score >= s_score and b_score >= 48.0:
            cb = _get_chain(sym, float(ind["close"]))
            setup = _build_stock_setup(sym, ind, rs_info, True, cb)
            if setup:
                bull_candidates.append(setup)
        elif s_score > b_score and s_score >= 48.0:
            cb = _get_chain(sym, float(ind["close"]))
            setup = _build_stock_setup(sym, ind, rs_info, False, cb)
            if setup:
                bear_candidates.append(setup)

    if str(MODE or "").upper() == "BTST":
        bull_candidates.sort(key=_btst_gapup_score, reverse=True)
    else:
        bull_candidates.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    bear_candidates.sort(key=lambda x: x.get("score", 0.0), reverse=True)

    options_bullish = _select_capped_items(bull_candidates, max_items=5, enforce_risk_cap=False)
    options_bearish = _select_capped_items(bear_candidates, max_items=5, enforce_risk_cap=False)

    option_symbols = {
        str(x.get("symbol") or "").strip().upper()
        for x in (options_bullish + options_bearish)
        if x.get("symbol")
    }
    session_date_now = dt.datetime.now(IST).date().isoformat()
    prev_morning_eq_syms = _load_previous_morning_equity_symbols(
        session_date=session_date_now,
        current_mode=MODE,
        base_dir="docs/data",
    )
    excluded_eq_syms = option_symbols | prev_morning_eq_syms

    intraday_bullish, intraday_bearish = _select_intraday_equity_10(
        bull_candidates=bull_candidates,
        bear_candidates=bear_candidates,
        excluded_symbols=excluded_eq_syms,
        mode_tag=MODE,
    )
    equity_intraday = intraday_bullish + intraday_bearish

    index_trades = _build_index_trades(candles, live_overrides=live_overrides)

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
            "nse_mcp_live_active": bool(nse_live_snap),
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
        "Dispatched %d total ideas (%d stock setups: %dB/%dS opt + %d eq [10 target]) using %s",
        total_ideas,
        stock_setups_count,
        len(options_bullish),
        len(options_bearish),
        len(equity_intraday),
        data_source,
    )


if __name__ == "__main__":
    run_screener()
