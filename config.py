import os

MODE = os.getenv("MODE", "BTST").strip().upper()
if MODE not in ["MORNING", "BTST"]:
    MODE = "BTST"

TITLE = f"{MODE} Institutional Quant & Hedging Desk"

CAPITAL = float(os.getenv("CAPITAL", "100000"))
RISK_PCT = float(os.getenv("RISK_PCT", "0.02"))
MIS_MARGIN_PER_TRADE = float(os.getenv("MIS_MARGIN_PER_TRADE", "20000"))
LEVERAGE_MULTIPLIER = 5
