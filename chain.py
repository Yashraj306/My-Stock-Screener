import pandas as pd
import numpy as np


def analyze_chain(df):
    if df is None or df.empty:
        return None

    df = df.dropna(subset=["strike"]).copy()
    if df.empty:
        return None

    spot = float(df["spot"].iloc[0]) if "spot" in df.columns and not pd.isna(df["spot"].iloc[0]) else 0.0
    tot_ce_oi, tot_pe_oi = df["ce_oi"].sum(), df["pe_oi"].sum()
    oi_pcr = round(tot_pe_oi / tot_ce_oi, 2) if tot_ce_oi > 0 else 1.0

    return {
        "spot": spot,
        "oi_pcr": oi_pcr,
        "intraday_pcr": oi_pcr,
        "max_pain": spot,
        "resistance_strike": spot * 1.02,
        "support_strike": spot * 0.98,
        "atm_iv": 20.0,
        "sentiment": "BULLISH" if oi_pcr >= 1.0 else "BEARISH",
        "oi_action": "Volume Buildup"
    }
