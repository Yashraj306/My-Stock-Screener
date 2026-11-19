import datetime as dt
from universe import LOT_SIZES

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _strike_step(symbol: str, spot: float) -> float:
    if symbol in {"BANKNIFTY", "SENSEX"}:
        return 100.0
    if symbol in {"NIFTY", "FINNIFTY"}:
        return 50.0
    if spot >= 3000:
        return 50.0
    if spot >= 1000:
        return 20.0
    if spot >= 300:
        return 10.0
    return 5.0


def _is_expired_or_stale(expiry: str | None) -> bool:
    if not expiry:
        return False
    today_str = dt.datetime.now(IST).date().isoformat()
    return str(expiry).strip() < today_str


def _leg_filter(
    row: dict,
    side: str,
    min_ltp: float = 0.25,
    max_spread_pct: float = 8.0,
    min_oi: int = 10000,
    min_vol: int = 10000,
    max_iv: float = 85.0,
) -> bool:
    p = "ce" if side == "CE" else "pe"
    ltp = float(row.get(f"{p}_ltp") or 0.0)
    bid = float(row.get(f"{p}_bid") or 0.0)
    ask = float(row.get(f"{p}_ask") or 0.0)
    oi = int(row.get(f"{p}_oi") or 0)
    vol = int(row.get(f"{p}_vol") or 0)
    iv = float(row.get(f"{p}_iv") or 0.0)

    if ltp < min_ltp:
        return False
    if oi < min_oi and vol < min_vol:
        return False
    if bid > 0 and ask > 0 and ask >= bid:
        mid = (bid + ask) / 2.0
        if mid > 0 and (((ask - bid) / mid) * 100.0) > max_spread_pct:
            return False
    if iv > max_iv:
        return False
    return True


def select_option_strategy(
    symbol: str,
    side: str,
    spot: float,
    chain_df=None,
    expiry: str | None = None,
    prefer_spread: bool = True,
) -> dict:
    side_norm = "CE" if str(side).upper() in {"CE", "CALL", "BUY CALL (CE)"} else "PE"
    lot_size = int(LOT_SIZES.get(symbol, 0))
    step = _strike_step(symbol, spot)

    if _is_expired_or_stale(expiry):
        return {
            "available": False,
            "strategy": "UNAVAILABLE",
            "reason": f"Expiry {expiry} is earlier than current IST date.",
            "lot_size": lot_size,
            "expiry": expiry,
        }

    if chain_df is None or getattr(chain_df, "empty", True) or spot <= 0:
        return {
            "available": False,
            "strategy": "UNAVAILABLE",
            "reason": "Live option chain unavailable; synthetic option pricing disabled.",
            "lot_size": lot_size,
            "expiry": expiry,
        }

    records = chain_df.to_dict("records")
    near = [r for r in records if abs(float(r.get("strike", 0.0)) - spot) <= max(spot * 0.06, step * 4)] or records
    p = "ce" if side_norm == "CE" else "pe"

    valid = []
    for r in near:
        if not _leg_filter(r, side_norm):
            continue
        delta_abs = abs(float(r.get(f"{p}_delta") or 0.0))
        theta_val = float(r.get(f"{p}_theta") or 0.0)
        ltp_val = float(r[f"{p}_ltp"])
        if delta_abs > 0 and not (0.15 <= delta_abs <= 0.85):
            continue
        if theta_val < 0 and ltp_val > 0 and (abs(theta_val) / ltp_val) > 0.35:
            continue
        valid.append(r)

    if not valid:
        return {
            "available": False,
            "strategy": "UNAVAILABLE",
            "reason": "No active contracts met liquidity, minimum premium, spread, IV, and Greek filters.",
            "lot_size": lot_size,
            "expiry": expiry,
        }

    buy_row = min(
        valid,
        key=lambda r: (
            abs(abs(float(r.get(f"{p}_delta") or 0.52)) - 0.52),
            abs(float(r.get("strike", 0.0)) - spot),
        ),
    )
    buy_strike = float(buy_row["strike"])
    buy_ltp = float(buy_row[f"{p}_ltp"])
    buy_iv = float(buy_row.get(f"{p}_iv") or 0.0) or None
    buy_delta = float(buy_row.get(f"{p}_delta") or 0.0) or None
    buy_theta = float(buy_row.get(f"{p}_theta") or 0.0) or None

    if side_norm == "CE":
        otm = [r for r in valid if float(r["strike"]) > buy_strike and float(r[f"{p}_ltp"]) < buy_ltp]
    else:
        otm = [r for r in valid if float(r["strike"]) < buy_strike and float(r[f"{p}_ltp"]) < buy_ltp]

    fmt_s = lambda x: int(x) if float(x).is_integer() else round(float(x), 2)

    if prefer_spread and otm:
        sell_row = min(otm, key=lambda r: abs(abs(float(r["strike"]) - buy_strike) - (step * 2.0)))
        sell_strike = float(sell_row["strike"])
        sell_ltp = float(sell_row[f"{p}_ltp"])
        net_debit = round(buy_ltp - sell_ltp, 2)
        if net_debit > 0:
            width = round(abs(sell_strike - buy_strike), 2)
            max_profit_unit = round(max(width - net_debit, 0.0), 2)
            breakeven = round(buy_strike + net_debit, 2) if side_norm == "CE" else round(buy_strike - net_debit, 2)
            return {
                "available": True,
                "strategy": "BULL_CALL_SPREAD" if side_norm == "CE" else "BEAR_PUT_SPREAD",
                "option_type": side_norm,
                "expiry": expiry,
                "lot_size": lot_size,
                "buy_leg": {"strike": fmt_s(buy_strike), "ltp": buy_ltp, "iv": buy_iv, "delta": buy_delta, "theta": buy_theta},
                "sell_leg": {"strike": fmt_s(sell_strike), "ltp": sell_ltp},
                "net_debit_per_unit": net_debit,
                "maximum_loss_per_unit": net_debit,
                "maximum_profit_per_unit": max_profit_unit,
                "max_loss_lot": round(net_debit * lot_size, 2) if lot_size > 0 else None,
                "max_profit_lot": round(max_profit_unit * lot_size, 2) if lot_size > 0 else None,
                "breakeven": breakeven,
            }

    return {
        "available": True,
        "strategy": "OPTION_BUYING",
        "option_type": side_norm,
        "strike": fmt_s(buy_strike),
        "expiry": expiry,
        "lot_size": lot_size,
        "entry_premium": buy_ltp,
        "iv": buy_iv,
        "delta": buy_delta,
        "theta": buy_theta,
        "maximum_loss_per_unit": buy_ltp,
        "max_loss_lot": round(buy_ltp * lot_size, 2) if lot_size > 0 else None,
    }
