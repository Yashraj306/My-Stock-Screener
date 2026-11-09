import numpy as np
import pandas as pd
from datetime import datetime


def calculate_pcr(df):
    """Calculates Put-Call Ratio based on Open Interest and Traded Volume."""
    if df is None or df.empty:
        return 0.0, 0.0
    tot_ce_oi = df["ce_oi"].sum()
    tot_pe_oi = df["pe_oi"].sum()
    tot_ce_vol = df["ce_vol"].sum()
    tot_pe_vol = df["pe_vol"].sum()

    oi_pcr = round(tot_pe_oi / tot_ce_oi, 2) if tot_ce_oi > 0 else 0.0
    vol_pcr = round(tot_pe_vol / tot_ce_vol, 2) if tot_ce_vol > 0 else 0.0
    return oi_pcr, vol_pcr


def max_pain(df):
    """Calculates strike price where option writers experience minimum cumulative loss."""
    if df is None or df.empty or "strike" not in df.columns:
        return None
    strikes = df["strike"].values
    loss_list = []
    for s in strikes:
        ce_loss = np.maximum(0, s - df["strike"].values) * df["ce_oi"].values
        pe_loss = np.maximum(0, df["strike"].values - s) * df["pe_oi"].values
        loss_list.append(ce_loss.sum() + pe_loss.sum())
    return strikes[int(np.argmin(loss_list))] if loss_list else None


def identify_buildup(row):
    """Identifies long/short buildup or unwinding based on price and OI change."""
    def _buildup(price_chg, oi_chg):
        if price_chg > 0 and oi_chg > 0:
            return "Long Buildup"
        if price_chg < 0 and oi_chg > 0:
            return "Short Buildup"
        if price_chg < 0 and oi_chg < 0:
            return "Long Unwinding"
        if price_chg > 0 and oi_chg < 0:
            return "Short Covering"
        return "Neutral"

    ce_chg_price = row.get("ce_ltp", 0) - row.get("ce_prev_ltp", row.get("ce_ltp", 0))
    ce_chg_oi = row.get("ce_oi", 0) - row.get("ce_poi", 0)
    pe_chg_price = row.get("pe_ltp", 0) - row.get("pe_prev_ltp", row.get("pe_ltp", 0))
    pe_chg_oi = row.get("pe_oi", 0) - row.get("pe_poi", 0)

    return _buildup(ce_chg_price, ce_chg_oi), _buildup(pe_chg_price, pe_chg_oi)


def analyze_chain(df, spot_price=None):
    """
    Complete interpretation of the Option Chain:
    - PCR (OI & Vol)
    - Key Resistance (highest Call OI) & Support (highest Put OI)
    - ATM Strike & Max Pain
    """
    if df is None or df.empty:
        return None

    spot = spot_price if spot_price else (df["spot"].iloc[0] if "spot" in df.columns else None)
    if spot is None or spot <= 0:
        return None

    # Sort strikes
    df = df.sort_values(by="strike").reset_index(drop=True)

    # ATM Strike selection
    atm_idx = (df["strike"] - spot).abs().idxmin()
    atm_strike = df.loc[atm_idx, "strike"]

    # Support / Resistance via Open Interest walls
    res_row = df.loc[df["ce_oi"].idxmax()]
    sup_row = df.loc[df["pe_oi"].idxmax()]

    oi_pcr, vol_pcr = calculate_pcr(df)
    mp = max_pain(df)

    # Market sentiment flag based on PCR
    sentiment = "NEUTRAL"
    if oi_pcr >= 1.25:
        sentiment = "STRONG_BULLISH"
    elif oi_pcr >= 1.0:
        sentiment = "MILD_BULLISH"
    elif oi_pcr <= 0.70:
        sentiment = "STRONG_BEARISH"
    elif oi_pcr <= 0.85:
        sentiment = "MILD_BEARISH"

    return {
        "spot": spot,
        "atm_strike": atm_strike,
        "max_pain": mp,
        "resistance_strike": res_row["strike"],
        "support_strike": sup_row["strike"],
        "max_ce_oi": int(res_row["ce_oi"]),
        "max_pe_oi": int(sup_row["pe_oi"]),
        "oi_pcr": oi_pcr,
        "vol_pcr": vol_pcr,
        "sentiment": sentiment
    }
