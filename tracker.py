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
def _prepare_symbol_bars(raw_df: Any) -> pd.DataFrame | None:
    cleaned = clean_ohlcv(raw_df)
    if cleaned is None or cleaned.empty:
        return None
    df = cleaned.copy()
    if "Date" in df.columns:
        df["_date_str"] = df["Date"].astype(str).str[:10]
    else:
        df["_date_str"] = pd.to_datetime(df.index, errors="coerce").strftime("%Y-%m-%d")
    df = df[df["_date_str"].notna() & (df["_date_str"] != "NaT") & (df["_date_str"] != "")]
    if df.empty:
        return None
    df = df.sort_values("_date_str").reset_index(drop=True)
    return df


def evaluate_signals_chronologically(
    candles_by_symbol: dict[str, Any] | None = None,
    path: Path = SIGNALS_LOG_PATH,
) -> pd.DataFrame:
    df = _read_signals_df(path)
    if df.empty:
        return df

    prepared_candles: dict[str, pd.DataFrame] = {}
    for sym, raw_candles in (candles_by_symbol or {}).items():
        prep = _prepare_symbol_bars(raw_candles)
        if prep is not None and not prep.empty:
            prepared_candles[str(sym).strip().upper()] = prep

    df["_orig_idx"] = range(len(df))
    df = df.sort_values(
        by=["session_date", "timestamp_ist", "_orig_idx"],
        kind="mergesort",
    ).reset_index(drop=True)

    for idx, row in df.iterrows():
        status = str(row.get("status") or "OPEN").strip().upper()
        if status in {"CLOSED_TP1", "CLOSED_TP2", "CLOSED_SL", "CLOSED"}:
            e_px = _safe_float(row.get("entry"))
            x_px = _safe_float(row.get("exit_price"))
            q_val = _safe_int(row.get("qty"), 0)
            cur_chg = _safe_float(row.get("est_charges"))
            if e_px and x_px and q_val > 0 and (cur_chg is None or cur_chg == 0.0):
                side = str(row.get("side") or "").strip().upper()
                is_bull = "BULL" in side or "LONG" in side or "BUY" in side or side.endswith("CE")
                gross = ((x_px - e_px) * q_val) if is_bull else ((e_px - x_px) * q_val)
                charges = _estimate_nse_bse_round_trip_cost(e_px, x_px, q_val, str(row.get("category") or "EQUITY"))
                df.at[idx, "gross_pnl"] = f"{gross:.2f}"
                df.at[idx, "est_charges"] = f"{charges:.2f}"
                df.at[idx, "net_pnl"] = f"{gross - charges:.2f}"
            continue

        sym = str(row.get("symbol") or "").strip().upper()
        bars = prepared_candles.get(sym)
        if bars is None or bars.empty:
            continue

        entry = _safe_float(row.get("entry"))
        sl = _safe_float(row.get("stop_loss"))
        t1 = _safe_float(row.get("target_1"))
        t2 = _safe_float(row.get("target_2"))
        if entry is None or entry <= 0.0:
            continue

        side = str(row.get("side") or "").strip().upper()
        is_bull = "BULL" in side or "LONG" in side or "BUY" in side or side.endswith("CE")

        sig_date = str(row.get("session_date") or "").strip()[:10]
        sub_bars = bars[bars["_date_str"] >= sig_date] if sig_date else bars
        if sub_bars.empty:
            continue

        exit_price: float | None = None
        exit_date: str = ""
        new_status: str | None = None

        for _, bar in sub_bars.iterrows():
            b_date = str(bar.get("_date_str") or "")
            high = _safe_float(bar.get("High"))
            low = _safe_float(bar.get("Low"))
            if high is None or low is None:
                continue

            if is_bull:
                hit_sl = sl is not None and low <= sl
                hit_t2 = t2 is not None and high >= t2
                hit_t1 = t1 is not None and high >= t1
                if hit_sl and hit_t1:
                    exit_price = sl
                    exit_date = b_date
                    new_status = "CLOSED_SL"
                    break
                if hit_sl:
                    exit_price = sl
                    exit_date = b_date
                    new_status = "CLOSED_SL"
                    break
                if hit_t2:
                    exit_price = t2
                    exit_date = b_date
                    new_status = "CLOSED_TP2"
                    break
                if hit_t1:
                    exit_price = t1
                    exit_date = b_date
                    new_status = "CLOSED_TP1"
                    break
            else:
                hit_sl = sl is not None and high >= sl
                hit_t2 = t2 is not None and low <= t2
                hit_t1 = t1 is not None and low <= t1
                if hit_sl and hit_t1:
                    exit_price = sl
                    exit_date = b_date
                    new_status = "CLOSED_SL"
                    break
                if hit_sl:
                    exit_price = sl
                    exit_date = b_date
                    new_status = "CLOSED_SL"
                    break
                if hit_t2:
                    exit_price = t2
                    exit_date = b_date
                    new_status = "CLOSED_TP2"
                    break
                if hit_t1:
                    exit_price = t1
                    exit_date = b_date
                    new_status = "CLOSED_TP1"
                    break

        if new_status is not None and exit_price is not None:
            qty = _safe_int(row.get("qty"), 1)
            eff_qty = qty if qty > 0 else 1
            gross = ((exit_price - entry) * eff_qty) if is_bull else ((entry - exit_price) * eff_qty)
            charges = _estimate_nse_bse_round_trip_cost(
                entry_price=entry,
                exit_price=exit_price,
                qty=eff_qty,
                segment=str(row.get("category") or "EQUITY"),
            )
            net = gross - charges

            df.at[idx, "status"] = new_status
            df.at[idx, "exit_price"] = f"{exit_price:.2f}"
            df.at[idx, "exit_date"] = exit_date
            df.at[idx, "gross_pnl"] = f"{gross:.2f}"
            df.at[idx, "est_charges"] = f"{charges:.2f}"
            df.at[idx, "net_pnl"] = f"{net:.2f}"

    df = df.sort_values("_orig_idx", kind="mergesort").drop(columns=["_orig_idx"]).reset_index(drop=True)
    _write_signals_df(df, path)
    return df


def walk_forward_summary(path: Path = SIGNALS_LOG_PATH, min_sample_size: int = 20) -> dict[str, Any]:
    df = _read_signals_df(path)
    total_signals = int(len(df))
    if df.empty:
        return {
            "total_signals": 0,
            "open_signals": 0,
            "closed_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "net_win_rate_pct": None,
            "total_gross_pnl": 0.0,
            "total_costs": 0.0,
            "total_est_charges": 0.0,
            "total_net_pnl": 0.0,
            "max_drawdown_net_pnl": 0.0,
            "min_sample_size": min_sample_size,
            "low_sample": True,
            "sample_warning": (
                f"Low sample size (0 closed trades vs {min_sample_size} minimum): "
                "metrics are preliminary and unverified for statistical reliability."
            ),
        }

    closed_mask = df["status"].astype(str).str.upper().str.startswith("CLOSED")
    open_count = int((~closed_mask).sum())
    closed_df = df[closed_mask].copy()
    closed_count = int(len(closed_df))

    net_pnls = closed_df["net_pnl"].apply(_safe_float).dropna()
    gross_pnls = closed_df["gross_pnl"].apply(_safe_float).dropna()
    charges_vals = closed_df["est_charges"].apply(_safe_float).dropna()

    wins = int((net_pnls > 0.0).sum())
    losses = int((net_pnls <= 0.0).sum())
    win_rate = round((wins / len(net_pnls)) * 100.0, 2) if len(net_pnls) > 0 else None

    cum_pnl = 0.0
    peak_pnl = 0.0
    max_dd = 0.0
    for v in net_pnls.tolist():
        cum_pnl += float(v)
        if cum_pnl > peak_pnl:
            peak_pnl = cum_pnl
        dd = peak_pnl - cum_pnl
        if dd > max_dd:
            max_dd = dd

    total_costs_val = round(float(charges_vals.sum()), 2) if len(charges_vals) else 0.0
    low_sample = closed_count < min_sample_size
    sample_warning = (
        f"Low sample size ({closed_count} closed trades < {min_sample_size} minimum): "
        "results have limited statistical confidence and do not represent guaranteed performance."
        if low_sample
        else ""
    )

    return {
        "total_signals": total_signals,
        "open_signals": open_count,
        "closed_trades": closed_count,
        "winning_trades": wins,
        "losing_trades": losses,
        "net_win_rate_pct": win_rate,
        "total_gross_pnl": round(float(gross_pnls.sum()), 2) if len(gross_pnls) else 0.0,
        "total_costs": total_costs_val,
        "total_est_charges": total_costs_val,
        "total_net_pnl": round(float(net_pnls.sum()), 2) if len(net_pnls) else 0.0,
        "max_drawdown_net_pnl": round(float(max_dd), 2),
        "min_sample_size": min_sample_size,
        "low_sample": low_sample,
        "sample_warning": sample_warning,
    }


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        test_csv = Path(tmpdir) / "signals_log.csv"
        sample_df = pd.DataFrame(
            [
                {
                    "Date": "2026-11-20",
                    "Open": 100.0,
                    "High": 102.0,
                    "Low": 99.0,
                    "Close": 101.0,
                    "Volume": 100000,
                }
            ]
        )
        sample_payload = {
            "session_date": "2026-11-20",
            "mode": "MORNING",
            "eq_intraday": [
                {
                    "symbol": "TESTSYM",
                    "sector": "TEST",
                    "side": "BULLISH",
                    "entry": 100.0,
                    "stop_loss": 98.0,
                    "target_1": 103.0,
                    "target_2": 105.0,
                    "qty": 1,
                }
            ],
        }
        log_signals(sample_payload, path=test_csv)
        evaluate_signals_chronologically(candles_by_symbol={"TESTSYM": sample_df}, path=test_csv)
        summary = walk_forward_summary(path=test_csv)
        print("Smoke check passed. Summary:", summary)

