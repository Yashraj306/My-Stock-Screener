import numpy as np
import pandas as pd
from config import *

def buildup(price_chg, oi_chg_pct):
    if abs(oi_chg_pct) < 1: return "Neutral"
    if price_chg >= 0 and oi_chg_pct > 0: return "Long Buildup"
    if price_chg < 0 and oi_chg_pct > 0: return "Short Buildup"
    if price_chg >= 0 and oi_chg_pct < 0: return "Short Covering"
    return "Long Unwinding"

def _pick(sub, col, full):
    base = sub if (len(sub) and sub[col].max() > 0) else full
    return float(base.loc[base[col].idxmax(), "strike"])

def analyze_chain(df, exp, src, price_chg, direction):
    df = df.sort_values("strike").reset_index(drop=True)
    sp = df["spot"].dropna()
    if sp.empty or df["ce_oi"].sum() == 0: return None
    spot = float(sp.iloc[0])
    pcr = df["pe_oi"].sum() / max(df["ce_oi"].sum(), 1)
    sup = _pick(df[df["strike"] <= spot], "pe_oi", df)     # put wall at or below spot
    res = _pick(df[df["strike"] >= spot], "ce_oi", df)     # call wall at or above spot
    ce_chg, pe_chg = (df["ce_oi"] - df["ce_poi"]).sum(), (df["pe_oi"] - df["pe_poi"]).sum()
    tot, ptot = df["ce_oi"].sum() + df["pe_oi"].sum(), df["ce_poi"].sum() + df["pe_poi"].sum()
    oi_pct = (tot / ptot - 1) * 100 if ptot else 0
    k = df["strike"].values
    pain = [(df["ce_oi"].values * np.maximum(kk - k, 0)).sum() + (df["pe_oi"].values * np.maximum(k - kk, 0)).sum() for kk in k]
    atm_i = int(np.argmin(np.abs(k - spot)))
    iv = float(np.mean([x for x in (df.iloc[atm_i]["ce_iv"], df.iloc[atm_i]["pe_iv"]) if x > 0] or [0]))
    if pcr >= 1.1 and pe_chg > ce_chg: bias = "BULLISH"
    elif pcr <= 0.9 and ce_chg > pe_chg: bias = "BEARISH"
    else: bias = "NEUTRAL"
    dte = (dt.date.fromisoformat(exp) - ist_now().date()).days

    p = "ce" if direction == "BULLISH" else "pe"
    win = df.iloc[max(0, atm_i - 1): atm_i + 4]
    omax = win[f"{p}_oi"].max()
    scored = []
    for _, row in win.iterrows():
        ltp, oi, poi, vol = row[f"{p}_ltp"], row[f"{p}_oi"], row[f"{p}_poi"], row[f"{p}_vol"]
        if ltp <= 0 or oi <= 0 or oi < 0.3 * omax: continue
        oi_up = (oi / poi - 1) * 100 if poi else 0
        delta, theta, ivs = row[f"{p}_delta"], row[f"{p}_theta"], row[f"{p}_iv"]
        decay = abs(theta) / ltp * 100 if ltp else 0
        iv_ok = (8 <= ivs <= 100) if ivs else True
        sc = np.log1p(vol) + np.log1p(oi) + (3 if oi_up > 5 else 0)
        if delta: sc += 4 - abs(abs(delta) - 0.5) * 8
        sc -= decay * (1.0 if MODE == "BTST" else 0.5)
        if not iv_ok: sc -= 5
        scored.append((sc, dict(strike=float(row["strike"]), side=p.upper(), ltp=float(ltp), oi=float(oi), oi_up=float(oi_up),
                       vol=float(vol), iv=float(ivs), iv_ok=iv_ok, delta=float(delta), theta=float(theta),
                       gamma=float(row[f"{p}_gamma"]), vega=float(row[f"{p}_vega"]), decay=float(decay))))
    best = max(scored, key=lambda x: x[0])[1] if scored else None
    return dict(src=src, expiry=exp, dte=dte, spot=spot, pcr=float(pcr), support=sup, resistance=res,
                max_pain=float(k[int(np.argmin(pain))]), atm=float(k[atm_i]), iv=iv, bias=bias,
                buildup=buildup(price_chg, oi_pct), oi_chg_pct=float(oi_pct), best=best)
