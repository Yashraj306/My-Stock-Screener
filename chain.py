
import numpy as np
import pandas as pd


def _num(value):
    try:
        value = float(value)
        return value if np.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def analyze_chain(df):
    """Analyze option-chain OI without inventing missing values."""

    if not isinstance(df, pd.DataFrame) or df.empty:
        return None

    required = ["strike", "ce_oi", "pe_oi"]

    if not all(col in df.columns for col in required):
        return None

    data = df.copy()

    for col in required:
        data[col] = pd.to_numeric(data[col], errors="coerce")

    data = data.dropna(subset=required)
    data = data[data["strike"] > 0].copy()

    if data.empty:
        return None

    data["ce_oi"] = data["ce_oi"].clip(lower=0)
    data["pe_oi"] = data["pe_oi"].clip(lower=0)

    data = data.sort_values("strike").reset_index(drop=True)

    strikes = data["strike"].to_numpy(dtype=float)
    ce_oi = data["ce_oi"].to_numpy(dtype=float)
    pe_oi = data["pe_oi"].to_numpy(dtype=float)

    total_ce = float(ce_oi.sum())
    total_pe = float(pe_oi.sum())

    oi_pcr = (
        round(total_pe / total_ce, 2)
        if total_ce > 0 else None
    )

    # Intraday change in OI is calculated only when previous OI exists.
    intraday_pcr = None
    ce_change = None
    pe_change = None

    if {"ce_poi", "pe_poi"}.issubset(data.columns):
        data["ce_poi"] = pd.to_numeric(
            data["ce_poi"], errors="coerce"
        )
        data["pe_poi"] = pd.to_numeric(
            data["pe_poi"], errors="coerce"
        )

        if data[["ce_poi", "pe_poi"]].notna().all().all():
            ce_change = float(
                (data["ce_oi"] - data["ce_poi"]).sum()
            )
            pe_change = float(
                (data["pe_oi"] - data["pe_poi"]).sum()
            )

            if ce_change > 0:
                intraday_pcr = round(
                    pe_change / ce_change, 2
                )

    # Max Pain: minimum combined option-writer payout.
    max_pain = None

    if total_ce + total_pe > 0:
        losses = []

        for settlement in strikes:
            call_loss = (
                np.maximum(settlement - strikes, 0) * ce_oi
            )
            put_loss = (
                np.maximum(strikes - settlement, 0) * pe_oi
            )

            losses.append(
                float(call_loss.sum() + put_loss.sum())
            )

        max_pain = float(strikes[int(np.argmin(losses))])

    resistance = None
    support = None

    if total_ce > 0:
        resistance = float(
            data.loc[data["ce_oi"].idxmax(), "strike"]
        )

    if total_pe > 0:
        support = float(
            data.loc[data["pe_oi"].idxmax(), "strike"]
        )

    spot = None

    if "spot" in data.columns:
        spot_values = pd.to_numeric(
            data["spot"], errors="coerce"
        )
        valid_spot = spot_values[spot_values > 0]

        if not valid_spot.empty:
            spot = float(valid_spot.iloc[-1])

    atm_iv = None

    if spot is not None:
        atm_index = (data["strike"] - spot).abs().idxmin()
        atm_row = data.loc[atm_index]

        iv_values = []

        for column in ["ce_iv", "pe_iv"]:
            if column in data.columns:
                iv = _num(atm_row.get(column))

                if iv is not None and iv > 0:
                    iv_values.append(iv)

        if iv_values:
            atm_iv = round(
                sum(iv_values) / len(iv_values), 2
            )

    sentiment = "INSUFFICIENT_DATA"
    oi_action = "Insufficient option-chain data"

    if oi_pcr is not None and intraday_pcr is not None:
        if oi_pcr >= 1.15 and intraday_pcr >= 1.0:
            sentiment = "BULLISH"
            oi_action = "Put OI dominance with positive relative buildup"

        elif oi_pcr <= 0.80 and intraday_pcr <= 0.85:
            sentiment = "BEARISH"
            oi_action = "Call OI dominance with negative relative buildup"

        else:
            sentiment = "NEUTRAL"
            oi_action = "Mixed or balanced OI activity"

    return {
        "spot": spot,
        "oi_pcr": oi_pcr,
        "intraday_pcr": intraday_pcr,
        "ce_change_oi": ce_change,
        "pe_change_oi": pe_change,
        "max_pain": round(max_pain, 2) if max_pain is not None else None,
        "resistance_strike": round(resistance, 2) if resistance is not None else None,
        "support_strike": round(support, 2) if support is not None else None,
        "atm_iv": atm_iv,
        "sentiment": sentiment,
        "oi_action": oi_action
    }
