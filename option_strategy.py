# option_strategy.py
from universe import LOT_SIZES


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


def _liquidity_ok(row: dict, side: str, max_spread_pct: float = 8.0, min_oi: int = 10000, min_vol: int = 10000) -> tuple[bool, str]:
    prefix = "ce" if side == "CE" else "pe"
    ltp = float(row.get(f"{prefix}_ltp") or 0.0)
    bid = float(row.get(f"{prefix}_bid") or 0.0)
    ask = float(row.get(f"{prefix}_ask") or 0.0)
    oi = int(row.get(f"{prefix}_oi") or 0)
    vol = int(row.get(f"{prefix}_vol") or 0)
    iv = float(row.get(f"{prefix}_iv") or 0.0)

    if ltp <= 0:
        return False, "Missing LTP"
    if oi < min_oi and vol < min_vol:
        return False, f"Low liquidity (OI={oi}, Vol={vol})"
    if bid > 0 and ask > 0 and ask >= bid:
        mid = (bid + ask) / 2.0
        spread_pct = ((ask - bid) / mid) * 100.0 if mid > 0 else 100.0
        if spread_pct > max_spread_pct:
            return False, f"Wide bid-ask spread ({spread_pct:.1f}%)"
    if iv > 0 and iv > 85.0:
        return False, f"Extreme IV ({iv:.1f}%)"
    return True, "OK"


def select_option_strategy(
    symbol: str,
    side: str,
    spot: float,
    chain_df=None,
    expiry: str | None = None,
    prefer_spread: bool = True,
) -> dict:
    """
    Selects a single-leg option or a 2-leg debit spread (BULL_CALL_SPREAD / BEAR_PUT_SPREAD)
    from live option-chain rows using liquidity, bid-ask spread, IV, and Greeks.
    Does not fabricate option prices if live chain data is absent.
    """
    side_norm = "CE" if str(side).upper() in {"CE", "CALL", "BUY CALL (CE)"} else "PE"
    strategy_spread = "BULL_CALL_SPREAD" if side_norm == "CE" else "BEAR_PUT_SPREAD"
    lot_size = int(LOT_SIZES.get(symbol, 0))
    step = _strike_step(symbol, spot)

    if chain_df is None or getattr(chain_df, "empty", True) or spot <= 0:
        return {
            "available": False,
            "strategy": "UNAVAILABLE",
            "reason": "Live option chain unavailable; no synthetic option quotes generated.",
            "lot_size": lot_size,
            "expiry": expiry,
        }

    records = chain_df.to_dict("records")
    # Filter to strikes within +/- 6% of spot
    near = [r for r in records if abs(float(r.get("strike", 0.0)) - spot) <= max(spot * 0.06, step * 4)]
    if not near:
        near = records

    prefix = "ce" if side_norm == "CE" else "pe"
    valid_legs = []
    for r in near:
        ok, _ = _liquidity_ok(r, side_norm)
        delta_abs = abs(float(r.get(f"{prefix}_delta") or 0.0))
        if ok and (delta_abs == 0.0 or 0.15 <= delta_abs <= 0.85):
            valid_legs.append(r)

    if not valid_legs:
        return {
            "available": False,
            "strategy": "UNAVAILABLE",
            "reason": "No contracts passed liquidity, spread, IV, and delta filters.",
            "lot_size": lot_size,
            "expiry": expiry,
        }

    # Pick ATM/slightly-ITM directional leg near target delta ~0.50-0.60
    buy_leg = min(
        valid_legs,
        key=lambda r: (
            abs(abs(float(r.get(f"{prefix}_delta") or 0.5)) - 0.52),
            abs(float(r.get("strike", 0.0)) - spot),
        ),
    )
    buy_strike = float(buy_leg["strike"])
    buy_ltp = float(buy_leg[f"{prefix}_ltp"])
    buy_iv = float(buy_leg.get(f"{prefix}_iv") or 0.0)
    buy_delta = float(buy_leg.get(f"{prefix}_delta") or 0.0)
    buy_theta = float(buy_leg.get(f"{prefix}_theta") or 0.0)

    # Pick OTM hedge leg in direction of trade
    if side_norm == "CE":
        otm_candidates = [r for r in valid_legs if float(r["strike"]) > buy_strike and float(r[f"{prefix}_ltp"]) < buy_ltp]
    else:
        otm_candidates = [r for r in valid_legs if float(r["strike"]) < buy_strike and float(r[f"{prefix}_ltp"]) < buy_ltp]

    if prefer_spread and otm_candidates:
        target_offset = step * 2.0
        sell_leg = min(
            otm_candidates,
            key=lambda r: abs(abs(float(r["strike"]) - buy_strike) - target_offset),
        )
        sell_strike = float(sell_leg["strike"])
        sell_ltp = float(sell_leg[f"{prefix}_ltp"])
        net_debit = round(buy_ltp - sell_ltp, 2)
        width = round(abs(sell_strike - buy_strike), 2)
        max_profit_unit = round(max(width - net_debit, 0.0), 2)
        breakeven = round(buy_strike + net_debit, 2) if side_norm == "CE" else round(buy_strike - net_debit, 2)

        return {
            "available": True,
            "strategy": strategy_spread,
            "option_type": side_norm,
            "expiry": expiry,
            "lot_size": lot_size,
            "buy_leg": {
                "strike": int(buy_strike) if buy_strike.is_integer() else buy_strike,
                "ltp": buy_ltp,
                "iv": buy_iv or None,
                "delta": buy_delta or None,
                "theta": buy_theta or None,
            },
            "sell_leg": {
                "strike": int(sell_strike) if sell_strike.is_integer() else sell_strike,
                "ltp": sell_ltp,
            },
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
        "strike": int(buy_strike) if buy_strike.is_integer() else buy_strike,
        "expiry": expiry,
        "lot_size": lot_size,
        "entry_premium": buy_ltp,
        "iv": buy_iv or None,
        "delta": buy_delta or None,
        "theta": buy_theta or None,
        "maximum_loss_per_unit": buy_ltp,
        "max_loss_lot": round(buy_ltp * lot_size, 2) if lot_size > 0 else None,
    }
