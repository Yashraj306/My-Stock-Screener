"""
Central configuration for My Stock Screener.

Capital is intentionally unset unless CAPITAL is explicitly configured.
This project generates signals only; it never places orders.
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------------
# Runtime mode
# ---------------------------------------------------------------------
# GitHub Actions sets MODE=MORNING at 9:00 AM IST and MODE=BTST at 2:00 PM.
# Manual runs can select either mode through workflow_dispatch.
MODE = os.getenv("MODE", "SWING").strip().upper()

VALID_MODES = {"MORNING", "BTST", "SWING", "INTRADAY"}
if MODE not in VALID_MODES:
    MODE = "SWING"


# ---------------------------------------------------------------------
# Capital and risk controls
# ---------------------------------------------------------------------
def _read_positive_float(name):
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


CAPITAL = _read_positive_float("CAPITAL")
CAPITAL_CONFIGURED = CAPITAL is not None

# Conservative defaults. These are risk-policy percentages, not a
# substitute for broker margin data or a guarantee of maximum loss.
RISK_PCT = 0.005                 # 0.5% risk budget per trade
DAILY_LOSS_LIMIT_PCT = 0.01      # 1.0% daily loss limit
MAX_ALLOCATION_PCT = 0.10        # 10% maximum allocation reference

# Do not calculate quantity or lot size when capital is not configured.
ALLOW_POSITION_SIZING = CAPITAL_CONFIGURED

# Signal-only mode: no order placement is implemented or permitted.
EXECUTION_MODE = "SIGNAL_ONLY"
AUTO_TRADING_ENABLED = False


# ---------------------------------------------------------------------
# Screener limits
# ---------------------------------------------------------------------
MAX_INTRADAY_BUY = 5
MAX_INTRADAY_SELL = 5
MAX_BTST_BULLISH = 5
MAX_BTST_BEARISH = 5

# These are maximum shortlist sizes, not guaranteed signal counts.
MIN_RISK_REWARD = 1.5
MIN_ADX = 18
MIN_VOLUME_RATIO = 1.2
MIN_ATR_PCT = 0.25
MAX_ATR_PCT = 8.0


# ---------------------------------------------------------------------
# Technical analysis settings
# ---------------------------------------------------------------------
RSI_PERIOD = 14
ATR_PERIOD = 14
ADX_PERIOD = 14

EMA_FAST = 20
EMA_MID = 50
EMA_SLOW = 200

VOLUME_AVERAGE_PERIOD = 20


# ---------------------------------------------------------------------
# Swing / BTST strategy settings
# ---------------------------------------------------------------------
SWING = {
    "RSI_PERIOD": RSI_PERIOD,
    "ATR_PERIOD": ATR_PERIOD,
    "ADX_PERIOD": ADX_PERIOD,
    "EMA_FAST": EMA_FAST,
    "EMA_MID": EMA_MID,
    "EMA_SLOW": EMA_SLOW,
    "MIN_ADX": MIN_ADX,
    "MIN_VOLUME_RATIO": MIN_VOLUME_RATIO,
    "MIN_RISK_REWARD": MIN_RISK_REWARD,
    "STOP_ATR_MULTIPLIER": 1.5,
    "TARGET_1_R_MULTIPLE": 1.5,
    "TARGET_2_R_MULTIPLE": 2.0,
    "MAX_ATR_PCT": MAX_ATR_PCT,
    "MIN_ATR_PCT": MIN_ATR_PCT,
}


# ---------------------------------------------------------------------
# Market data / API settings
# ---------------------------------------------------------------------
UPSTOX_BASE_URL = "https://api.upstox.com"
UPSTOX_HISTORICAL_URL = "https://api.upstox.com/v3/historical-candle"
UPSTOX_INTRADAY_URL = "https://api.upstox.com/v3/historical-candle/intraday"

REQUEST_TIMEOUT = 20
API_RETRY_COUNT = 3
API_RETRY_DELAY_SECONDS = 2


# ---------------------------------------------------------------------
# Time utilities
# ---------------------------------------------------------------------
IST = ZoneInfo("Asia/Kolkata")


def ist_now():
    """Return the current timezone-aware datetime in India Standard Time."""
    return datetime.now(IST)


def vol_factor(volume, average_volume):
    """
    Return current volume divided by average volume.
    Return None when either input is missing or invalid.
    """
    try:
        current = float(volume)
        average = float(average_volume)
        if current < 0 or average <= 0:
            return None
        return current / average
    except (TypeError, ValueError):
        return None


def capital_status():
    """Human-readable capital configuration status."""
    if CAPITAL_CONFIGURED:
        return "CONFIGURED"
    return "NOT_CONFIGURED"


def sizing_status():
    """Sizing is disabled until the user explicitly configures capital."""
    if not CAPITAL_CONFIGURED:
        return "CAPITAL_NOT_CONFIGURED"
    return "CAPITAL_CONFIGURED_REQUIRES_BROKER_MARGIN_CHECK"
