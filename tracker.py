import csv
import datetime as dt
from pathlib import Path
from typing import Any

import pandas as pd

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
LOG_PATH = Path("signals_log.csv")

FIELDNAMES = [
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
    "qty",
    "max_risk",
    "status",
    "exit_price",
    "exit_date",
    "pnl_rs",
    "charges_rs",
    "net_pnl_rs",
    "sessions_open",
    "close_reason",
]


def _estimate_charges(entry: float, exit_px: float, qty: int) -> float:
    turnover = max(float(entry or 0.0), 0.0) * max(int(qty or 0), 0) + max(float(exit_px or 0.0), 0.0) * max(int(qty or 0), 0)
    return round(turnover * 0.0011, 2)


def _compute_gross_pnl(side: str, entry: float, exit_px: float, qty: int) -> float:
    side_u = str(side or "").strip().upper()
    q = max(int(qty or 0), 0)
    if side_u in {"BEARISH", "SELL (SHORT)", "PE", "SHORT"}:
        return round((float(entry) - float(exit_px)) * q, 2)
    return round((float(exit_px) - float(entry)) * q, 2)


def _read_rows(path: Path = LOG_PATH) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [{k: (v or "").strip() for k, v in row.items()} for row in reader]


def _write_rows(rows: list[dict[str, str]], path: Path = LOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({fn: r.get(fn, "") for fn in FIELDNAMES})


def _infer_category(item: dict[str, Any], mode_tag: str) -> str:
    sec = str(item.get("sector") or "").strip().upper()
    side = str(item.get("side") or "").strip().upper()
    if sec == "INDEX":
        return "INDEX"
    if item.get("option_plan") and isinstance(item.get("option_plan"), dict) and item["option_plan"].get("available"):
        return "OPT_BULL" if side == "BULLISH" else "OPT_BEAR"
    return "EQ_BTST" if str(mode_tag).upper() == "BTST" else "EQ_INTRA"


def log_signals(payload: dict[str, Any], path: Path = LOG_PATH) -> dict[str, Any]:
    """
    Logs new signals while skipping duplicates when an OPEN row already exists
    for the same (symbol, side, category). MORNING and BTST do not re-log identical open ideas.
    """
    now_ist = dt.datetime.now(IST)
    ts_str = now_ist.strftime("%Y-%m-%d %H:%M:%S IST")
    session_date = now_ist.date().isoformat()
    mode_tag = str(payload.get("mode") or "BTST").strip().upper()

    existing = _read_rows(path)
    open_keys: set[tuple[str, str, str]] = set()
    for r in existing:
        if str(r.get("status") or "").upper() == "OPEN":
            open_keys.add((
                str(r.get("symbol") or "").strip().upper(),
                str(r.get("side") or "").strip().upper(),
                str(r.get("category") or "").strip().upper(),
            ))

    candidates: list[dict[str, Any]] = []
    for idx_it in (payload.get("index_trades") or []):
        if isinstance(idx_it, dict):
            candidates.append(idx_it)
    for opt_it in (payload.get("options_bullish") or []) + (payload.get("options_bearish") or []):
        if isinstance(opt_it, dict):
            candidates.append(opt_it)
    for eq_it in (payload.get("equity_intraday") or []):
        if isinstance(eq_it, dict):
            candidates.append(eq_it)

    added_count = 0
    skipped_dupes = 0

    for it in candidates:
        sym = str(it.get("symbol") or "").strip().upper()
        side = str(it.get("side") or "").strip().upper()
        if not sym or not side:
            continue
        cat = _infer_category(it, mode_tag)
        dedupe_key = (sym, side, cat)
        if dedupe_key in open_keys:
            skipped_dupes += 1
            continue

        entry = float(it.get("entry") or it.get("close") or 0.0)
        sl = float(it.get("stop_loss") or 0.0)
        t1 = float(it.get("target_1") or 0.0)
        t2 = float(it.get("target_2") or 0.0)
        qty = int(it.get("qty") or 0)
        max_risk = float(it.get("max_risk") or 0.0)
        if entry <= 0 or qty <= 0:
            continue

        existing.append({
            "timestamp_ist": ts_str,
            "session_date": session_date,
            "mode": mode_tag,
            "category": cat,
            "symbol": sym,
            "sector": str(it.get("sector") or "OTHER").strip().upper(),
            "side": side,
            "entry": f"{entry:.2f}",
            "stop_loss": f"{sl:.2f}",
            "target_1": f"{t1:.2f}",
            "target_2": f"{t2:.2f}",
            "qty": str(qty),
            "max_risk": f"{max_risk:.2f}",
            "status": "OPEN",
            "exit_price": "",
            "exit_date": "",
            "pnl_rs": "",
            "charges_rs": "",
            "net_pnl_rs": "",
            "sessions_open": "0",
            "close_reason": "",
        })
        open_keys.add(dedupe_key)
        added_count += 1

    _write_rows(existing, path)
    return {"added": added_count, "skipped_duplicates": skipped_dupes}


def evaluate_signals_chronologically(
    candles_by_symbol: dict[str, pd.DataFrame],
    path: Path = LOG_PATH,
    max_open_sessions: int = 5,
) -> dict[str, Any]:
    """
    Evaluates OPEN signals chronologically against available daily bars.
    Applies SL/TP rules, time-stop rules (intraday same day, BTST next session, >5 sessions -> CLOSED_TIME),
    and returns a summary including stale OPEN rows.
    """
    rows = _read_rows(path)
    today_str = dt.datetime.now(IST).date().isoformat()
    closed_now = 0
    stale_open_rows: list[dict[str, str]] = []

    for r in rows:
        if str(r.get("status") or "").upper() != "OPEN":
            continue
        sym = str(r.get("symbol") or "").strip().upper()
        df = candles_by_symbol.get(sym)
        entry_date = str(r.get("session_date") or "")[:10]
        entry_px = float(r.get("entry") or 0.0)
        sl_px = float(r.get("stop_loss") or 0.0)
        t1_px = float(r.get("target_1") or 0.0)
        qty = int(r.get("qty") or 0)
        side = str(r.get("side") or "").strip().upper()
        cat = str(r.get("category") or "").strip().upper()
        mode_tag = str(r.get("mode") or "").strip().upper()
        is_bull = side not in {"BEARISH", "SELL (SHORT)", "PE", "SHORT"}

        if df is None or getattr(df, "empty", True) or entry_px <= 0:
            continue

        sub_df = df.copy()
        sub_dates = [str(idx)[:10] for idx in sub_df.index]
        later_bars = [(d, sub_df.iloc[i]) for i, d in enumerate(sub_dates) if d > entry_date]
        sessions_elapsed = len(later_bars)
        r["sessions_open"] = str(sessions_elapsed)

        exit_px = None
        exit_dt = None
        close_status = None
        close_reason = ""

        for bar_dt, bar in later_bars:
            b_high = float(bar.get("High") or 0.0)
            b_low = float(bar.get("Low") or 0.0)
            b_close = float(bar.get("Close") or entry_px)

            if is_bull:
                if sl_px > 0 and b_low <= sl_px:
                    exit_px, exit_dt, close_status, close_reason = sl_px, bar_dt, "CLOSED_SL", "Stop-loss hit"
                    break
                if t1_px > 0 and b_high >= t1_px:
                    exit_px, exit_dt, close_status, close_reason = t1_px, bar_dt, "CLOSED_TP1", "Target 1 hit"
                    break
            else:
                if sl_px > 0 and b_high >= sl_px:
                    exit_px, exit_dt, close_status, close_reason = sl_px, bar_dt, "CLOSED_SL", "Stop-loss hit"
                    break
                if t1_px > 0 and b_low <= t1_px:
                    exit_px, exit_dt, close_status, close_reason = t1_px, bar_dt, "CLOSED_TP1", "Target 1 hit"
                    break

            if cat == "EQ_INTRA" and sessions_elapsed >= 1:
                exit_px, exit_dt, close_status, close_reason = b_close, bar_dt, "CLOSED_TIME", "Intraday time-stop"
                break
            if (cat == "EQ_BTST" or mode_tag == "BTST") and sessions_elapsed >= 1:
                exit_px, exit_dt, close_status, close_reason = b_close, bar_dt, "CLOSED_TIME", "BTST next-session close"
                break

        if close_status is None and sessions_elapsed > max_open_sessions:
            last_dt, last_bar = later_bars[-1]
            c_px = float(last_bar.get("Close") or entry_px)
            exit_px, exit_dt, close_status, close_reason = c_px, last_dt, "CLOSED_TIME", f"Exceeded {max_open_sessions} sessions"

        if close_status is not None and exit_px is not None:
            gross = _compute_gross_pnl(side, entry_px, float(exit_px), qty)
            charges = _estimate_charges(entry_px, float(exit_px), qty)
            net = round(gross - charges, 2)
            r["status"] = close_status
            r["exit_price"] = f"{float(exit_px):.2f}"
            r["exit_date"] = str(exit_dt or today_str)[:10]
            r["pnl_rs"] = f"{gross:.2f}"
            r["charges_rs"] = f"{charges:.2f}"
            r["net_pnl_rs"] = f"{net:.2f}"
            r["close_reason"] = close_reason
            closed_now += 1
        elif sessions_elapsed >= 3:
            stale_open_rows.append({
                "symbol": sym,
                "category": cat,
                "session_date": entry_date,
                "sessions_open": str(sessions_elapsed),
            })

    _write_rows(rows, path)
    return {
        "closed_now": closed_now,
        "stale_open_count": len(stale_open_rows),
        "stale_open_rows": stale_open_rows[:25],
    }


def walk_forward_summary(path: Path = LOG_PATH, min_trades: int = 10) -> dict[str, Any]:
    rows = _read_rows(path)
    closed = [r for r in rows if str(r.get("status") or "").upper().startswith("CLOSED_")]
    total_closed = len(closed)
    if total_closed < min_trades:
        return {
            "available": False,
            "closed_trades": total_closed,
            "min_required": min_trades,
            "note": f"Insufficient closed sample ({total_closed}/{min_trades}).",
        }

    net_pnls = [float(r.get("net_pnl_rs") or 0.0) for r in closed]
    wins = sum(1 for x in net_pnls if x > 0)
    return {
        "available": True,
        "closed_trades": total_closed,
        "win_rate_pct": round((wins / total_closed) * 100.0, 2),
        "net_pnl_rs": round(sum(net_pnls), 2),
    }


def category_performance_summary(path: Path = LOG_PATH) -> dict[str, Any]:
    """
    Computes per-category performance metrics (EQ_BTST, EQ_INTRA, OPT_BULL, OPT_BEAR, INDEX)
    with sample-size warnings (< 100 closed trades) for the dashboard Performance tab.
    """
    rows = _read_rows(path)
    categories = ["EQ_BTST", "EQ_INTRA", "OPT_BULL", "OPT_BEAR", "INDEX"]
    stats_by_cat: dict[str, dict[str, Any]] = {}

    for cat in categories:
        cat_rows = [r for r in rows if str(r.get("category") or "").strip().upper() == cat]
        closed = [r for r in cat_rows if str(r.get("status") or "").strip().upper().startswith("CLOSED_")]
        open_cnt = sum(1 for r in cat_rows if str(r.get("status") or "").strip().upper() == "OPEN")
        closed_cnt = len(closed)
        net_pnls = [float(r.get("net_pnl_rs") or 0.0) for r in closed]
        wins = sum(1 for p in net_pnls if p > 0)
        losses = sum(1 for p in net_pnls if p < 0)
        win_rate = round((wins / closed_cnt) * 100.0, 2) if closed_cnt > 0 else 0.0
        net_total = round(sum(net_pnls), 2)

        stats_by_cat[cat] = {
            "category": cat,
            "closed_count": closed_cnt,
            "open_count": open_cnt,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": win_rate,
            "net_pnl_rs": net_total,
            "sample_warning": closed_cnt < 100,
        }

    all_closed = [r for r in rows if str(r.get("status") or "").strip().upper().startswith("CLOSED_")]
    all_open = [r for r in rows if str(r.get("status") or "").strip().upper() == "OPEN"]
    stale_open = [r for r in all_open if int(r.get("sessions_open") or 0) >= 3]

    return {
        "by_category": stats_by_cat,
        "total_closed": len(all_closed),
        "total_open": len(all_open),
        "stale_open_count": len(stale_open),
        "sample_threshold": 100,
    }
