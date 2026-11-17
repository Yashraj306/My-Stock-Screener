
import numpy as np
import pandas as pd


def _number(value, default=np.nan):
    """Safely convert values to numeric."""
    try:
        result = float(value)
        return result if np.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def analyze_chain(df):
    """
    Analyze option-chain OI, PCR, Max Pain, support,
    resistance and ATM IV.

    Missing or invalid data is not replaced with fake values.
    """

    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None

    required = ["strike", "ce_oi", "pe_oi"]

    if not all(col in df.columns for col in required):
        return None

    data = df.copy()

    # Convert required columns safely.
    for col in required:
        data[col] = pd.to_numeric(data[col], errors="coerce")

    data = data.dropna(subset=["strike"])
    data = data[data["strike"] > 0].copy()

    if data.empty:
        return None

    # Missing OI is treated as unavailable, not as real zero OI.
    data = data.dropna(subset=["ce_oi", "pe_oi"])

    if data.empty:
        return None

    data["ce_oi"] = data["ce_oi"].clip(lower=0)
    data["pe_oi"] = data["pe_oi"].clip(lower=0)

    data = data.sort_values("strike").reset_index(drop=True)

    if data.empty:
        return None

    # Spot price.
    spot = None

    if "spot" in data.columns:
        spot_values = pd.to_numeric(data["spot"], errors="coerce")
        valid_spot = spot_values[spot_values > 0]

        if not valid_spot.empty:
            spot = float(valid_spot.iloc[-1])

    # Total open interest.
    total_ce_oi = float(data["ce_oi"].sum())
    total_pe_oi = float(data["pe_oi"].sum())

    oi_pcr = (
        round(total_pe_oi / total_ce_oi, 2)
        if total_ce_oi > 0
        else None
    )

    # Intraday change in OI.
    intraday_pcr = None
    ce_change = None
    pe_change = None

    previous_columns_available = (
        "ce_poi" in data.columns
        and "pe_poi" in data.columns
    )

    if previous_columns_available:
        data["ce_poi"] = pd.to_numeric(
            data["ce_poi"], errors="coerce"
        )
        data["pe_poi"] = pd.to_numeric(
            data["pe_poi"], errors="coerce"
        )

        if data["ce_poi"].notna().all() and data["pe_poi"].notna().all():
            ce_change = float(
                (data["ce_oi"] - data["ce_poi"]).sum()
            )
            pe_change = float(
                (data["pe_oi"] - data["pe_poi"]).sum()
            )

            if ce_change > 0:
                intraday_pcr = round(pe_change / ce_change, 2)

    # Max Pain:
    # Call writer loss = max(settlement - strike, 0) * Call OI
    # Put writer loss  = max(strike - settlement, 0) * Put OI
    strikes = data["strike"].to_numpy(dtype=float)
    ce_oi = data["ce_oi"].to_numpy(dtype=float)
    pe_oi = data["pe_oi"].to_numpy(dtype=float)

    max_pain = None

    if len(strikes) > 0 and (ce_oi.sum() + pe_oi.sum()) > 0:
        total_writer_loss = []

        for settlement in strikes:
            call_loss = np.maximum(
                settlement - strikes, 0
            ) * ce_oi

            put_loss = np.maximum(
                strikes - settlement, 0
            ) * pe_oi

            total_writer_loss.append(
                float(call_loss.sum() + put_loss.sum())
            )

        max_pain = float(
            strikes[int(np.argmin(total_writer_loss))]
        )

    # Highest Call OI = resistance candidate.
    # Highest Put OI = support candidate.
    resistance_strike = None
    support_strike = None

    if total_ce_oi > 0:
        idx = data["ce_oi"].idxmax()
        resistance_strike = float(data.loc[idx, "strike"])

    if total_pe_oi > 0:
        idx = data["pe_oi"].idxmax()
        support_strike = float(data.loc[idx, "strike"])

    # ATM implied volatility.
    atm_iv = None

    if spot is not None:
        atm_idx = (data["strike"] - spot).abs().idxmin()
        atm_row = data.loc[atm_idx]

        iv_values = []

        for col in ["ce_iv", "pe_iv"]:
            if col in data.columns:
                iv = _number(atm_row.get(col))

                if np.isfinite(iv) and iv > 0:
                    iv_values.append(iv)

        if iv_values:
            atm_iv = round(
                float(sum(iv_values) / len(iv_values)), 2
            )

    # Sentiment is only classified when both PCR readings exist.
    sentiment = "INSUFFICIENT_DATA"
    oi_action = "Option-chain data is incomplete"

    if oi_pcr is not None and intraday_pcr is not None:

        if oi_pcr >= 1.15 and intraday_pcr >= 1.0:
            sentiment = "BULLISH"
            oi_action = "Put OI dominance with positive OI buildup"

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
        "max_pain": (
            round(max_pain, 2)
            if max_pain is not None else None
        ),
        "resistance_strike": (
            round(resistance_strike, 2)
            if resistance_strike is not None else None
        ),
        "support_strike": (
            round(support_strike, 2)
            if support_strike is not None else None
        ),
        "atm_iv": atm_iv,
        "sentiment": sentiment,
        "oi_action": oi_action
    }
