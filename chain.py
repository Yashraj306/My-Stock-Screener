import pandas as pd


def analyze_chain(chain_df: pd.DataFrame | None, spot: float = 0.0) -> dict:
    empty_out = {
        "chain_available": False,
        "pcr": None,
        "support_strike": None,
        "resistance_strike": None,
        "max_pain": None,
        "oi_build_up": "Chain Unavailable",
    }
    if chain_df is None or getattr(chain_df, "empty", True):
        return empty_out

    df = chain_df.copy()
    if "strike" not in df.columns:
        return empty_out

    for col in ["strike", "ce_oi", "pe_oi", "ce_prev_oi", "pe_prev_oi", "ce_ltp", "pe_ltp"]:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    df = df[df["strike"] > 0].sort_values("strike").reset_index(drop=True)
    if df.empty:
        return empty_out

    total_ce_oi = float(df["ce_oi"].sum())
    total_pe_oi = float(df["pe_oi"].sum())
    if total_ce_oi <= 0.0 and total_pe_oi <= 0.0:
        return {
            "chain_available": False,
            "pcr": None,
            "support_strike": None,
            "resistance_strike": None,
            "max_pain": None,
            "oi_build_up": "No Active OI",
        }

    pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else None

    sup_row = df.loc[df["pe_oi"].idxmax()] if total_pe_oi > 0 else None
    res_row = df.loc[df["ce_oi"].idxmax()] if total_ce_oi > 0 else None

    support_strike = float(sup_row["strike"]) if sup_row is not None and float(sup_row["pe_oi"]) > 0 else None
    resistance_strike = float(res_row["strike"]) if res_row is not None and float(res_row["ce_oi"]) > 0 else None

    strikes = df["strike"].tolist()
    min_pain = None
    max_pain_strike = None
    for k in strikes:
        ce_pain = ((k - df["strike"]).clip(lower=0.0) * df["ce_oi"]).sum()
        pe_pain = ((df["strike"] - k).clip(lower=0.0) * df["pe_oi"]).sum()
        tot_pain = float(ce_pain + pe_pain)
        if min_pain is None or tot_pain < min_pain:
            min_pain = tot_pain
            max_pain_strike = float(k)

    ce_chg = float((df["ce_oi"] - df["ce_prev_oi"]).sum())
    pe_chg = float((df["pe_oi"] - df["pe_prev_oi"]).sum())

    if pe_chg > 0 and ce_chg <= 0:
        oi_build_up = "Put Writing / Bullish OI"
    elif ce_chg > 0 and pe_chg <= 0:
        oi_build_up = "Call Writing / Bearish OI"
    elif pe_chg > 0 and ce_chg > 0 and pcr is not None and pcr >= 1.0:
        oi_build_up = "Long Buildup Bias"
    elif ce_chg > 0 and pe_chg > 0 and pcr is not None and pcr < 1.0:
        oi_build_up = "Short Buildup Bias"
    else:
        oi_build_up = "Balanced / Unwinding"

    fmt_s = lambda v: int(v) if v is not None and float(v).is_integer() else (round(float(v), 2) if v is not None else None)

    return {
        "chain_available": True,
        "pcr": pcr,
        "support_strike": fmt_s(support_strike),
        "resistance_strike": fmt_s(resistance_strike),
        "max_pain": fmt_s(max_pain_strike),
        "oi_build_up": oi_build_up,
    }
