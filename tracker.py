import csv
import datetime as dt
import os
from pathlib import Path

import pandas as pd

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DEFAULT_LOG_PATH = Path(os.getenv("SIGNALS_LOG_PATH", "signals_log.csv"))
MIN_RELIABLE_SAMPLE_SIZE = 30

FIELDNAMES = [
    "signal_id",
    "logged_at_ist",
    "session_date",
    "mode",
    "symbol",
    "instrument_type",
    "side",
    "entry",
    "stop_loss",
    "target_1",
    "target_2",
    "qty",
    "status",
    "exit_date",
    "exit_price",
    "gross_pnl",
    "total_costs",
    "net_pnl",
    "r_multiple",
    "notes",
]


def _ist_now() -> dt.datetime:
    return dt.datetime.now(IST)


def _normalize_date_str(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=IST).date().isoformat()
        return value.astimezone(IST).date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return ""
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=IST).date().isoformat()
        return parsed.astimezone(IST).date().isoformat()
    except ValueError:
        return text[:10]


def _estimate_nse_bse_round_trip_cost(
    entry_price: float,
    exit_price: float,
    qty: int,
    instrument_type: str = "EQUITY_MIS",
    exchange: str = "NSE",
    slippage_bps_per_side: float = 5.0,
) -> dict:
    q = max(int(qty or 0), 0)
    if q <= 0 or entry_price <= 0 or exit_price <= 0:
        return {
            "slippage_cost": 0.0,
            "brokerage": 0.0,
            "stt": 0.0,
            "exchange_txn": 0.0,
            "sebi_fee": 0.0,
            "stamp_duty": 0.0,
            "gst": 0.0,
            "total_costs": 0.0,
        }

    slip_rate = max(float(slippage_bps_per_side), 0.0) / 10000.0
    slippage_cost = (entry_price * slip_rate + exit_price * slip_rate) * q

    buy_turnover = entry_price * q
    sell_turnover = exit_price * q
    total_turnover = buy_turnover + sell_turnover

    inst = str(instrument_type or "EQUITY_MIS").upper()
    exch = str(exchange or "NSE").upper()

    brokerage = min(20.0, buy_turnover * 0.0003) + min(20.0, sell_turnover * 0.0003)

    if "OPT" in inst or "OPTION" in inst:
        stt = sell_turnover * 0.0010
        exch_rate = 0.0003503 if exch == "NSE" else 0.000325
        stamp_duty = buy_turnover * 0.00003
    else:
        stt = sell_turnover * 0.00025
        exch_rate = 0.0000297 if exch == "NSE" else 0.0000375
        stamp_duty = buy_turnover * 0.00003

    exchange_txn = total_turnover * exch_rate
    sebi_fee = total_turnover * 1e-6
    gst = 0.18 * (brokerage + exchange_txn + sebi_fee)

    total_costs = round(
        slippage_cost + brokerage + stt + exchange_txn + sebi_fee + stamp_duty + gst,
        2,
    )
    return {
        "slippage_cost": round(slippage_cost, 2),
        "brokerage": round(brokerage, 2),
        "stt": round(stt, 2),
        "exchange_txn": round(exchange_txn, 2),
        "sebi_fee": round(sebi_fee, 2),
        "stamp_duty": round(stamp_duty, 2),
        "gst": round(gst, 2),
        "total_costs": total_costs,
    }


def ensure_log_file(log_path: Path = DEFAULT_LOG_PATH) -> Path:
    path = Path(log_path)
    if not path.parent.exists() and str(path.parent) not in ("", "."):
        path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
    return path


def _read_log_safe(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=FIELDNAMES)
    df = pd.read_csv(
        path,
        dtype=str,
        engine="python",
        on_bad_lines="skip",
    ).fillna("")
    for col in FIELDNAMES:
        if col not in df.columns:
            df[col] = ""
    return df[FIELDNAMES]


def log_signals(
    payload: dict,
    log_path: Path = DEFAULT_LOG_PATH,
    session_date: str | None = None,
) -> int:
    path = ensure_log_file(log_path)
    now_ist = _ist_now()
    sess_day = _normalize_date_str(session_date) or now_ist.date().isoformat()
    logged_ts = now_ist.isoformat()
    mode = str((payload or {}).get("mode") or "BTST").upper()

    rows_to_add: list[dict] = []

    for item in (payload or {}).get("equity_intraday") or []:
        action = str(item.get("action") or "")
        is_short = "SELL" in action.upper() or "SHORT" in action.upper()
        side = "SHORT" if is_short else "LONG"
        entry = float(item.get("entry") or 0.0)
        sl = float(item.get("stop_loss") or 0.0)
        t1 = float(item.get("target_1") or 0.0)
        t2 = float(item.get("target_2") or 0.0)
        qty = int(item.get("qty") or 0)
        sym = str(item.get("symbol") or "").strip()
        if not sym or entry <= 0 or qty <= 0:
            continue
        sig_id = f"{sess_day}-{mode}-EQ-{sym}-{side}"
        rows_to_add.append({
            "signal_id": sig_id,
            "logged_at_ist": logged_ts,
            "session_date": sess_day,
            "mode": mode,
            "symbol": sym,
            "instrument_type": "EQUITY_MIS",
            "side": side,
            "entry": round(entry, 2),
            "stop_loss": round(sl, 2) if sl > 0 else "",
            "target_1": round(t1, 2) if t1 > 0 else "",
            "target_2": round(t2, 2) if t2 > 0 else "",
            "qty": qty,
            "status": "OPEN",
            "exit_date": "",
            "exit_price": "",
            "gross_pnl": "",
            "total_costs": "",
            "net_pnl": "",
            "r_multiple": "",
            "notes": item.get("sizing_status") or "",
        })

    opt_items = (
        list((payload or {}).get("index_trades") or [])
        + list(((payload or {}).get("setups") or {}).get("bullish") or [])
        + list(((payload or {}).get("setups") or {}).get("bearish") or [])
    )

    for item in opt_items:
        sym = str(item.get("symbol") or "").strip()
        side_raw = str(item.get("side") or "").upper()
        is_put = "PUT" in side_raw or "PE" in side_raw
        side = "BUY_PE" if is_put else "BUY_CE"
        entry = float(item.get("entry") or item.get("close") or 0.0)
        sl = float(item.get("stop_loss") or 0.0)
        t1 = float(item.get("target_1") or 0.0)
        t2 = float(item.get("target_2") or 0.0)
        plan = item.get("option_plan") or item.get("hedge_plan") or {}
        if not plan.get("available"):
            continue
        lot_size = int(plan.get("lot_size") or 0)
        if not sym or entry <= 0 or lot_size <= 0:
            continue
        sig_id = f"{sess_day}-{mode}-OPT-{sym}-{side}"
        rows_to_add.append({
            "signal_id": sig_id,
            "logged_at_ist": logged_ts,
            "session_date": sess_day,
            "mode": mode,
            "symbol": sym,
            "instrument_type": "OPTION_PLAN",
            "side": side,
            "entry": round(entry, 2),
            "stop_loss": round(sl, 2) if sl > 0 else "",
            "target_1": round(t1, 2) if t1 > 0 else "",
            "target_2": round(t2, 2) if t2 > 0 else "",
            "qty": lot_size,
            "status": "OPEN",
            "exit_date": "",
            "exit_price": "",
            "gross_pnl": "",
            "total_costs": "",
            "net_pnl": "",
            "r_multiple": "",
            "notes": str(plan.get("strategy") or ""),
        })

    if not rows_to_add:
        return 0

    existing_df = _read_log_safe(path)
    existing_ids = set(existing_df["signal_id"].tolist()) if "signal_id" in existing_df.columns else set()
    fresh_rows = [r for r in rows_to_add if r["signal_id"] not in existing_ids]

    if not fresh_rows:
        return 0

    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writerows(fresh_rows)

    return len(fresh_rows)


def evaluate_signals_chronologically(
    candles_by_symbol: dict[str, pd.DataFrame],
    log_path: Path = DEFAULT_LOG_PATH,
    as_of_date: str | None = None,
    slippage_bps_per_side: float = 5.0,
) -> pd.DataFrame:
    path = ensure_log_file(log_path)
    df_log = _read_log_safe(path)
    if df_log.empty:
        return df_log

    cutoff_str = _normalize_date_str(as_of_date)

    for idx, row in df_log.iterrows():
        if row.get("status") != "OPEN":
            continue
        sym = row.get("symbol", "").strip()
        sig_date = _normalize_date_str(row.get("session_date"))
        candles = candles_by_symbol.get(sym)
        if candles is None or candles.empty or not sig_date:
            continue

        work = clean_ohlcv(candles, min_bars=1)
        if work is None:
            continue

        future_bars = work[work["Date"] > sig_date]
        if cutoff_str:
            future_bars = future_bars[future_bars["Date"] <= cutoff_str]
        if future_bars.empty:
            continue

        entry = float(row.get("entry") or 0.0)
        sl = float(row.get("stop_loss") or 0.0)
        t1 = float(row.get("target_1") or 0.0)
        qty = int(float(row.get("qty") or 0))
        side = str(row.get("side") or "LONG").upper()
        inst_type = str(row.get("instrument_type") or "EQUITY_MIS").upper()
        is_short = side in {"SHORT", "BUY_PE"}

        risk_per_unit = abs(entry - sl) if sl > 0 else 0.0
        exit_price = None
        exit_date = ""
        new_status = "OPEN"

        for _, bar in future_bars.iterrows():
            b_date = str(bar["Date"])
            b_high = float(bar["High"])
            b_low = float(bar["Low"])
            b_close = float(bar["Close"])

            if not is_short:
                hit_sl = (sl > 0) and (b_low <= sl)
                hit_t1 = (t1 > 0) and (b_high >= t1)
                if hit_sl:
                    exit_price, exit_date, new_status = sl, b_date, "STOP_HIT"
                    break
                if hit_t1:
                    exit_price, exit_date, new_status = t1, b_date, "TARGET_1_HIT"
                    break
            else:
                hit_sl = (sl > 0) and (b_high >= sl)
                hit_t1 = (t1 > 0) and (b_low <= t1)
                if hit_sl:
                    exit_price, exit_date, new_status = sl, b_date, "STOP_HIT"
                    break
                if hit_t1:
                    exit_price, exit_date, new_status = t1, b_date, "TARGET_1_HIT"
                    break

            exit_price = b_close
            exit_date = b_date

        if new_status == "OPEN" and exit_price is not None and len(future_bars) >= 3:
            new_status = "TIME_EXIT"

        if new_status != "OPEN" and exit_price is not None and entry > 0 and qty > 0:
            move_per_unit = (entry - exit_price) if is_short else (exit_price - entry)
            gross_pnl = round(move_per_unit * qty, 2)
            costs = _estimate_nse_bse_round_trip_cost(
                entry_price=entry,
                exit_price=exit_price,
                qty=qty,
                instrument_type=inst_type,
                slippage_bps_per_side=slippage_bps_per_side,
            )["total_costs"]
            net_pnl = round(gross_pnl - costs, 2)
            r_mult = round(move_per_unit / risk_per_unit, 2) if risk_per_unit > 0 else ""

            df_log.at[idx, "status"] = new_status
            df_log.at[idx, "exit_date"] = exit_date
            df_log.at[idx, "exit_price"] = round(float(exit_price), 2)
            df_log.at[idx, "gross_pnl"] = f"{gross_pnl:.2f}"
            df_log.at[idx, "total_costs"] = f"{costs:.2f}"
            df_log.at[idx, "net_pnl"] = f"{net_pnl:.2f}"
            df_log.at[idx, "r_multiple"] = "" if r_mult == "" else f"{r_mult:.2f}"

    df_log.to_csv(path, index=False)
    return df_log


def walk_forward_summary(
    log_path: Path = DEFAULT_LOG_PATH,
    min_sample_size: int = MIN_RELIABLE_SAMPLE_SIZE,
) -> dict:
    path = ensure_log_file(log_path)
    df = _read_log_safe(path)
    closed_statuses = {"TARGET_1_HIT", "STOP_HIT", "TIME_EXIT"}
    closed = df[df["status"].isin(closed_statuses)].copy() if not df.empty else pd.DataFrame()

    n_closed = len(closed)
    low_sample_warning = n_closed < int(min_sample_size)

    if n_closed == 0:
        return {
            "closed_trades": 0,
            "net_win_rate_pct": None,
            "total_gross_pnl": 0.0,
            "total_costs": 0.0,
            "total_net_pnl": 0.0,
            "avg_r_multiple": None,
            "max_drawdown_net_pnl": 0.0,
            "statistically_reliable": False,
            "sample_warning": (
                f"LOW SAMPLE WARNING: 0 closed post-cost trades logged (< {min_sample_size} minimum). "
                "Do not claim win rate or accuracy."
            ),
        }

    closed["net_pnl_num"] = pd.to_numeric(closed["net_pnl"], errors="coerce").fillna(0.0)
    closed["gross_pnl_num"] = pd.to_numeric(closed["gross_pnl"], errors="coerce").fillna(0.0)
    closed["costs_num"] = pd.to_numeric(closed["total_costs"], errors="coerce").fillna(0.0)
    closed["r_num"] = pd.to_numeric(closed["r_multiple"], errors="coerce")

    wins = int((closed["net_pnl_num"] > 0).sum())
    win_rate = round((wins / n_closed) * 100.0, 2)
    total_gross = round(float(closed["gross_pnl_num"].sum()), 2)
    total_costs = round(float(closed["costs_num"].sum()), 2)
    total_net = round(float(closed["net_pnl_num"].sum()), 2)

    valid_r = closed["r_num"].dropna()
    avg_r = round(float(valid_r.mean()), 2) if not valid_r.empty else None

    equity_curve = closed["net_pnl_num"].cumsum()
    equity_with_zero = pd.concat([pd.Series([0.0]), equity_curve.reset_index(drop=True)], ignore_index=True)
    running_peak = equity_with_zero.cummax()
    drawdown = equity_with_zero - running_peak
    max_dd = round(float(drawdown.min()), 2)

    warning_msg = (
        f"LOW SAMPLE WARNING: Only {n_closed} closed post-cost trades logged (minimum {min_sample_size} recommended before drawing statistical conclusions)."
        if low_sample_warning
        else "Sample size meets minimum threshold; interpret results strictly out-of-sample after transaction costs."
    )

    return {
        "closed_trades": n_closed,
        "net_win_rate_pct": win_rate,
        "total_gross_pnl": total_gross,
        "total_costs": total_costs,
        "total_net_pnl": total_net,
        "avg_r_multiple": avg_r,
        "max_drawdown_net_pnl": max_dd,
        "statistically_reliable": not low_sample_warning,
        "sample_warning": warning_msg,
    }
    
