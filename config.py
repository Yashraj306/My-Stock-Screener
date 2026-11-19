import os

MODE = os.getenv("MODE", "BTST").strip().upper()
if MODE not in {"MORNING", "BTST", "SWING"}:
    MODE = "BTST"

SWING = "SWING"
TITLE = f"{MODE} NSE/BSE Quant & Hedging Desk"

_raw_capital = os.getenv("CAPITAL", "100000").strip()
try:
    CAPITAL = max(float(_raw_capital), 0.0)
except ValueError:
    CAPITAL = 100000.0

CAPITAL_CONFIGURED = CAPITAL > 0.0

_raw_risk = os.getenv("RISK_PCT", "0.01").strip()
try:
    _parsed_risk = float(_raw_risk)
    RISK_PCT = _parsed_risk / 100.0 if _parsed_risk > 1.0 else _parsed_risk
except ValueError:
    RISK_PCT = 0.01

MAX_ALLOCATION_PCT = float(os.getenv("MAX_ALLOCATION_PCT", "0.25"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "6"))
MIS_LEVERAGE = float(os.getenv("MIS_LEVERAGE", "5.0"))
ALLOW_MIS_LEVERAGE = os.getenv("ALLOW_MIS_LEVERAGE", "true").strip().lower() in {"1", "true", "yes"}
