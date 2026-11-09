import pandas as pd
import numpy as np


def analyze_chain(df):
    """
    Analyzes option chain data incorporating both Cumulative OI 
    and Intraday Change in OI for real institutional sentiment.
    """
    if df is None or df.empty:
        return None

    # Filter out empty strikes
    df = df.dropna(subset=["strike"]).copy()
    if df.empty:
        return None

    spot = df["spot"].iloc[0] if "spot" in df.columns and not pd.isna(df["spot"].iloc[0]) else 0

    tot_ce_oi = df["ce_oi"].sum()
    tot_pe_oi = df["pe_oi"].sum()
    oi_pcr = round(tot_pe_oi / tot_ce_oi, 2) if tot_ce_oi > 0 else 1.0

    # Intraday Change in OI PCR
    ce_chg_oi = (df["ce_oi"] - df["ce_poi"]).sum()
    pe_chg_oi = (df["pe_oi"] - df["pe_poi"]).sum()
    
    if ce_chg_oi > 0:
        intraday_pcr = round(pe_chg_oi / ce_chg_oi, 2)
    else:
        intraday_pcr = 1.5 if pe_chg_oi > 0 else 0.5

    # Max Pain Calculation
    strikes = df["strike"].values
    loss_list = []
    for s in strikes:
        call_loss = np.maximum(0, s - strikes) * df["ce_oi"].values
        put_loss = np.maximum(0, strikes - s) * df["pe_oi"].values
        loss_list.append(call_loss.sum() + put_loss.sum())
    
    max_pain = strikes[np.argmin(loss_list)] if len(loss_list) > 0 else spot

    # Major Support and Resistance Strikes (based on OI concentration)
    res_idx = df["ce_oi"].idxmax()
    sup_idx = df["pe_oi"].idxmax()
    resistance_strike = df.loc[res_idx, "strike"] if not pd.isna(res_idx) else spot
    support_strike = df.loc[sup_idx, "strike"] if not pd.isna(sup_idx) else spot

    # ATM Implied Volatility
    atm_idx = (df["strike"] - spot).abs().idxmin()
    atm_iv = round(float(df.loc[atm_idx, "ce_iv"]), 1) if ("ce_iv" in df.columns and not pd.isna(df.loc[atm_idx, "ce_iv"])) else 20.0

    # Institutional Sentiment Read
    if oi_pcr >= 1.15 and intraday_pcr >= 1.0:
        sentiment = "BULLISH"
        oi_action = "Long Buildup (Call Absorption)"
    elif oi_pcr <= 0.80 and intraday_pcr <= 0.85:
        sentiment = "BEARISH"
        oi_action = "Short Buildup (Heavy Call Writing)"
    else:
        sentiment = "NEUTRAL"
        oi_action = "Unwinding / Balanced Activity"

    return {
        "spot": spot,
        "oi_pcr": oi_pcr,
        "intraday_pcr": intraday_pcr,
        "max_pain": int(max_pain),
        "resistance_strike": int(resistance_strike),
        "support_strike": int(support_strike),
        "atm_iv": atm_iv,
        "sentiment": sentiment,
        "oi_action": oi_action
    }
