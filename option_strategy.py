"""Conservative stock-options selector. Signal generation only; no orders."""
import math
import pandas as pd

def _n(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except (TypeError,ValueError):
        return None

def _quote(row,prefix):
    keys=("ltp","bid","ask","oi","vol","iv","delta","theta")
    q={k:_n(row.get(f"{prefix}_{k}")) for k in keys}
    q["instrument_key"]=row.get(f"{prefix}_instrument_key")
    if not q["instrument_key"] or any(q[k] is None for k in keys):
        return None
    if min(q["ltp"],q["bid"],q["ask"],q["oi"],q["vol"],q["iv"])<=0:
        return None
    if q["ask"]<q["bid"]:
        return None
    mid=(q["ask"]+q["bid"])/2
    if mid<=0 or (q["ask"]-q["bid"])/mid*100>5:
        return None
    return q

def _candidate(df,prefix,spot,long=True,reference=None):
    found=[]
    for _,row in df.iterrows():
        strike=_n(row.get("strike"))
        q=_quote(row,prefix)
        if strike is None or strike<=0 or q is None: continue
        delta=abs(q["delta"])
        if long and not 0.35<=delta<=0.75: continue
        if not long and not 0.10<=delta<=0.40: continue
        if long and abs(strike-spot)/spot*100>8: continue
        if reference is not None:
            if prefix=="ce" and strike<=reference: continue
            if prefix=="pe" and strike>=reference: continue
        found.append({"strike":strike,"quote":q})
    if not found:return None
    if long:
        found.sort(key=lambda x:(abs(abs(x["quote"]["delta"])-0.55),
                                 (x["quote"]["ask"]-x["quote"]["bid"])/((x["quote"]["ask"]+x["quote"]["bid"])/2)))
    else:
        found.sort(key=lambda x:(abs(abs(x["quote"]["delta"])-0.25),
                                 (x["quote"]["ask"]-x["quote"]["bid"])/((x["quote"]["ask"]+x["quote"]["bid"])/2)))
    return found[0]

def select_option_strategy(chain_result,signal,lot_size=None):
    """
    chain_result must be (DataFrame, expiry, spot).
    Requires real bid/ask, LTP, OI, volume, IV, delta, theta and instrument key.
    """
    if not isinstance(chain_result,(tuple,list)) or len(chain_result)!=3:return None
    df,expiry,spot=chain_result
    if not isinstance(df,pd.DataFrame) or df.empty or not expiry:return None
    spot=_n(spot)
    if spot is None or spot<=0:return None
    side=str(signal).upper().strip()
    if side in ("BUY","BULLISH"): prefix,otype="ce","CE"
    elif side in ("SELL","BEARISH"): prefix,otype="pe","PE"
    else:return None
    long_leg=_candidate(df,prefix,spot,long=True)
    if long_leg is None:return None
    q=long_leg["quote"]
    prefer_spread=q["iv"]>=35 or abs(q["theta"])>=1.5 or ((q["ask"]-q["bid"])/((q["ask"]+q["bid"])/2)*100)>2.5
    hedge=_candidate(df,prefix,spot,long=False,reference=long_leg["strike"])
    if prefer_spread and hedge:
        hq=hedge["quote"]
        debit=q["ask"]-hq["bid"]
        width=abs(long_leg["strike"]-hedge["strike"])
        if 0<debit<width:
            return {
                "strategy":"BULL_CALL_SPREAD" if otype=="CE" else "BEAR_PUT_SPREAD",
                "option_type":otype,"expiry":expiry,
                "buy_leg":{"strike":long_leg["strike"],"instrument_key":q["instrument_key"],"entry_premium":q["ask"]},
                "sell_leg":{"strike":hedge["strike"],"instrument_key":hq["instrument_key"],"entry_premium":hq["bid"]},
                "net_debit_per_unit":round(debit,2),"spread_width":round(width,2),
                "maximum_loss_per_unit":round(debit,2),"maximum_profit_per_unit":round(width-debit,2),
                "breakeven":round(long_leg["strike"]+debit if otype=="CE" else long_leg["strike"]-debit,2),
                "quantity":None,"lot_size":None,"capital_configured":False,
                "selection_reason":"Defined-risk debit spread selected from live bid/ask and Greeks",
                "execution":"SIGNAL_ONLY"
            }
    spread_pct=(q["ask"]-q["bid"])/((q["ask"]+q["bid"])/2)*100
    if spread_pct<=2.5 and q["iv"]<45:
        return {
            "strategy":"OPTION_BUYING","option_type":otype,"expiry":expiry,
            "strike":long_leg["strike"],"instrument_key":q["instrument_key"],
            "entry_premium":q["ask"],"bid":q["bid"],"ask":q["ask"],
            "iv":q["iv"],"delta":q["delta"],"theta":q["theta"],
            "oi":q["oi"],"volume":q["vol"],
            "maximum_loss_per_unit":q["ask"],"quantity":None,"lot_size":None,
            "capital_configured":False,
            "selection_reason":"Liquid option with acceptable IV and bid-ask spread",
            "execution":"SIGNAL_ONLY"
        }
    return None
