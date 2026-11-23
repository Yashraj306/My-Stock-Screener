import csv
import datetime as dt
import math
import os
from pathlib import Path
from typing import Any

import pandas as pd

from tech import clean_ohlcv

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SIGNALS_LOG_PATH = Path(os.getenv("SIGNALS_LOG_PATH", "signals_log.csv"))

SIGNAL_COLUMNS = [
    "signal_id",
    "timestamp_ist",
    "session_date",
    "mode",
    "category",
    "symbol",
    "sector",
    "side",
    "entry",
    "stop_loss",
    "target_1",
    "target_2",
    "oi_target",
    "qty",
    "status",
    "exit_price",
    "exit_date",
    "gross_pnl",
    "est_charges",
    "net_pnl",
]


def _safe_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        num = float(val)
        return None if math.isnan(num) or math.isinf(num) else num
    except (TypeError, ValueError):
        return None


def _safe_int(val: Any, default: int = 0) -> int:
    num = _safe_float(val)
    return int(round(num)) if num is not None else default


def _normalize_row(row: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for col in SIGNAL_COLUMNS:
        raw_val = row.get(col, "")
        normalized[col] = "" if raw_val is None else str(raw_val).strip()
    if not normalized["status"]:
        normalized["status"] = "OPEN"
    return normalized


def _read_signals_df(path: Path = SIGNALS_LOG_PATH) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=SIGNAL_COLUMNS)
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except Exception:
        return pd.DataFrame(columns=SIGNAL_COLUMNS)
    for col in SIGNAL_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[SIGNAL_COLUMNS].copy()
    df["status"] = df["status"].replace("", "OPEN")
    return df


def _write_signals_df(df: pd.DataFrame, path: Path = SIGNALS_LOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df[SIGNAL_COLUMNS].copy() if not df.empty else pd.DataFrame(columns=SIGNAL_COLUMNS)
    out.to_csv(path, index=False, quoting=csv.QUOTE_MINIMAL)


def _estimate_nse_bse_round_trip_cost(
    entry_price: float,
    exit_price: float,
    qty: int,
    segment: str = "EQUITY",
) -> float:
    e_px = max(_safe_float(entry_price) or 0.0, 0.0)
    x_px = max(_safe_float(exit_price) or 0.0, 0.0)
    q = max(_safe_int(qty, 0), 0)
    if q <= 0 or (e_px <= 0.0 and x_px <= 0.0):
        return 0.0

    turnover = (e_px * q) + (x_px * q)
    seg = (segment or "EQUITY").upper()

    if "OPT" in seg or "INDEX" in seg:
        stt_rate = 0.000625
        exchange_rate = 0.00053
        sebi_rate = 10.0 / 10_000_000.0
        stamp_rate = 0.00003
        gst_rate = 0.18
        brokerage = 40.0
    else:
        stt_rate = 0.00025
        exchange_rate = 0.0000325
        sebi_rate = 10.0 / 10_000_000.0
        stamp_rate = 0.00003
        gst_rate = 0.18
        brokerage = min(40.0, max(0.0, turnover * 0.0006))

    statutory_and_txn = turnover * (stt_rate + exchange_rate + sebi_rate + stamp_rate)
    gst = gst_rate * (brokerage + (turnover * exchange_rate))
    total_cost = brokerage + statutory_and_txn + gst
    return round(float(total_cost), 2)


def log_signals(payload: dict[str, Any], path: Path = SIGNALS_LOG_PATH) -> int:
    data = payload or {}
    now_ist = dt.datetime.now(IST)
    ts_str = now_ist.strftime("%Y-%m-%d %H:%M:%S IST")
    session_date = str(data.get("session_date") or now_ist.date().isoformat())
    mode = str(data.get("mode") or "RUN").upper()

    buckets = [
        ("INDEX", data.get("index_trades") or []),
        ("OPT_BULL", data.get("options_bullish") or (data.get("setups") or {}).get("bullish") or []),
        ("OPT_BEAR", data.get("options_bearish") or (data.get("setups") or {}).get("bearish") or []),
        ("EQ_INTRA", data.get("equity_intraday") or []),
    ]

    existing_df = _read_signals_df(path)
    existing_ids = set(existing_df["signal_id"].astype(str).tolist()) if not existing_df.empty else set()

    new_rows: list[dict[str, str]] = []
    for category, items in buckets:
        for item in items:
            if not isinstance(item, dict):
                continue
            sym = str(item.get("symbol") or "").strip().upper()
            side = str(item.get("side") or "").strip().upper()
            entry = _safe_float(item.get("entry") if item.get("entry") is not None else item.get("close"))
            sl = _safe_float(item.get("stop_loss"))
            t1 = _safe_float(item.get("target_1"))
            t2 = _safe_float(item.get("target_2"))
            oi_t = _safe_float(item.get("oi_target"))
            qty = _safe_int(item.get("qty"), 0)

            if not sym or entry is None:
                continue

            sig_id = f"{session_date}|{mode}|{category}|{sym}|{side}|{entry:.2f}"
            if sig_id in existing_ids:
                continue
            existing_ids.add(sig_id)

            new_rows.append(
                _normalize_row(
                    {
                        "signal_id": sig_id,
                        "timestamp_ist": ts_str,
                        "session_date": session_date,
                        "mode": mode,
                        "category": category,
                        "symbol": sym,
                        "sector": str(item.get("sector") or "").strip(),
                        "side": side,
                        "entry": f"{entry:.2f}",
                        "stop_loss": "" if sl is None else f"{sl:.2f}",
                        "target_1": "" if t1 is None else f"{t1:.2f}",
                        "target_2": "" if t2 is None else f"{t2:.2f}",
                        "oi_target": "" if oi_t is None else f"{oi_t:.2f}",
                        "qty": str(qty) if qty > 0 else "",
                        "status": "OPEN",
                        "exit_price": "",
                        "exit_date": "",
                        "gross_pnl": "",
                        "est_charges": "",
                        "net_pnl": "",
                    }
                )
            )

    if new_rows:
        combined = pd.concat([existing_df, pd.DataFrame(new_rows, columns=SIGNAL_COLUMNS)], ignore_index=True)
        _write_signals_df(combined, path)
    elif not path.exists():
        _write_signals_df(existing_df, path)

    return len(new_rows)
