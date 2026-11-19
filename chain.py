import numpy as np
import pandas as pd


def analyze_chain(df: pd.DataFrame, spot_override: float = 0.0, prev_spot: float = 0.0) -> dict | None:
    if df is None or df.empty:
        return None

    c = df.copy()
    for col in ["strike", "ce_oi", "ce_poi", "pe_oi", "pe_poi", "ce_ltp", "pe_ltp", "ce_iv", "pe_iv"]:
        if col in c.columns:
            c[col] = pd.to_numeric(c[col], errors="coerce").fillna(0.0)
        else:
            c[col] = 0.0

    c = c[c["strike"] > 0].sort_values("strike").reset_index(drop=True)
    if c.empty:
        return None

    chain_spot = float(pd.to_numeric(c["spot"], errors="coerce").dropna().iloc[0]) if "spot" in c.columns and not pd.to_numeric(c["spot"], errors="coerce").dropna().empty else 0.0
    spot = float(spot_override) if spot_override and spot_override > 0 else chain_spot

    tot_ce_oi = float(c["ce_oi"].sum())
    tot_pe_oi = float(c["pe_oi"].sum())
    oi_pcr = round(tot_pe_oi / tot_ce_oi, 2) if tot_ce_oi > 0 else None

    c["ce_chg_oi"] = c["ce_oi"] - c["ce_poi"]
    c["pe_chg_oi"] = c["pe_oi"] - c["pe_poi"]
    tot_ce_chg = float(c["ce_chg_oi"].sum())
    tot_pe_chg = float(c["pe_chg_oi"].sum())
    net_oi_chg = tot_ce_chg + tot_pe_chg

    if tot_ce_chg > 0:
        intraday_pcr = round(tot_pe_chg / tot_ce_chg, 2)
    else:
        intraday_pcr = None

    # Strike-wise OI concentration around active strikes (or +/- 7% of spot if spot > 0)
    focus = c[(c["strike"] >= spot * 0.93) & (c["strike"] <= spot * 1.07)] if spot > 0 else c
    if focus.empty:
        focus = c

    total_focus_oi = float(focus["ce_oi"].sum() + focus["pe_oi"].sum())
    res_row = focus.loc[focus["ce_oi"].idxmax()]
    sup_row = focus.loc[focus["pe_oi"].idxmax()]

    res_strike = float(res_row["strike"])
    sup_strike = float(sup_row["strike"])
    res_conc_pct = round((float(res_row["ce_oi"]) / tot_ce_oi) * 100.0, 1) if tot_ce_oi > 0 else 0.0
    sup_conc_pct = round((float(sup_row["pe_oi"]) / tot_pe_oi) * 100.0, 1) if tot_pe_oi > 0 else 0.0
    focus_share_pct = round((total_focus_oi / (tot_ce_oi + tot_pe_oi)) * 100.0, 1) if (tot_ce_oi + tot_pe_oi) > 0 else 0.0

    # Max Pain calculation across candidate settlement strikes
    strikes = c["strike"].to_numpy(dtype=float)
    ce_oi_arr = c["ce_oi"].to_numpy(dtype=float)
    pe_oi_arr = c["pe_oi"].to_numpy(dtype=float)

    payoffs = []
    for s in strikes:
        call_payout = np.maximum(0.0, s - strikes) * ce_oi_arr
        put_payout = np.maximum(0.0, strikes - s) * pe_oi_arr
        payoffs.append(float(call_payout.sum() + put_payout.sum()))

    max_pain = float(strikes[int(np.argmin(payoffs))]) if payoffs else spot

    # ATM IV
    atm_iv = None
    if spot > 0:
        atm_idx = int((c["strike"] - spot).abs().idxmin())
        iv_vals = [float(x) for x in [c.loc[atm_idx, "ce_iv"], c.loc[atm_idx, "pe_iv"]] if x > 0]
        if iv_vals:
            atm_iv = round(float(np.mean(iv_vals)), 1)

    # 4-Quadrant OI + Price pairing
    price_up = (spot > prev_spot) if (spot > 0 and prev_spot > 0) else (tot_pe_chg >= tot_ce_chg)
    price_down = (spot < prev_spot) if (spot > 0 and prev_spot > 0) else (tot_ce_chg > tot_pe_chg)

    if net_oi_chg > 0 and price_up:
        oi_quadrant = "Long Buildup"
        sentiment = "BULLISH"
    elif net_oi_chg > 0 and price_down:
        oi_quadrant = "Short Buildup"
        sentiment = "BEARISH"
    elif net_oi_chg < 0 and price_up:
        oi_quadrant = "Short Covering"
        sentiment = "BULLISH"
    elif net_oi_chg < 0 and price_down:
        oi_quadrant = "Long Unwinding"
        sentiment = "BEARISH"
    else:
        oi_quadrant = "Balanced / Flat"
        sentiment = "NEUTRAL"

    fmt_s = lambda x: int(x) if float(x).is_integer() else round(float(x), 2)

    return {
        "spot": spot,
        "oi_pcr": oi_pcr,
        "intraday_pcr": intraday_pcr,
        "max_pain": fmt_s(max_pain) if max_pain > 0 else None,
        "resistance_strike": fmt_s(res_strike),
        "resistance_oi_pct": res_conc_pct,
        "support_strike": fmt_s(sup_strike),
        "support_oi_pct": sup_conc_pct,
        "focus_oi_share_pct": focus_share_pct,
        "atm_iv": atm_iv,
        "oi_quadrant": oi_quadrant,
        "sentiment": sentiment,
        "net_oi_change": int(round(net_oi_chg)),
    }
