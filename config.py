
import os
import datetime as dt

import numpy as np
import pandas as pd
import requests

# ==============================
# API / NOTIFICATION SETTINGS
# ==============================

EMAIL_USER = os.environ.get("EMAIL_USER", "")
EMAIL_PASS = os.environ.get("EMAIL_PASS", "")
EMAIL_TO = os.environ.get("EMAIL_TO", EMAIL_USER)

UPSTOX_TOKEN = os.environ.get("UPSTOX_TOKEN", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ==============================
# SCREENER MODE
# ==============================

MODE = os.environ.get("MODE", "SWING").upper()

if MODE not in {"SWING", "MORNING", "BTST"}:
    raise ValueError("MODE must be SWING, MORNING or BTST")

TITLE = {
    "SWING": "Professional Swing Trading Screener",
    "BTST": "BTST Options Screener",
    "MORNING": "Pre-Market Options Screener"
}[MODE]

INDICES = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK"
}

# ==============================
# STOCK LIMITS
# ==============================

CHAIN_LIMITS = {"PRE": 8, "POST": 6}
SHOW_LIMITS = {"PRE": 5, "POST": 4}

# ==============================
# RISK MANAGEMENT
# ==============================

CAPITAL = float(os.environ.get("CAPITAL") or 100000)
RISK_PCT = float(os.environ.get("RISK_PCT") or 1)

MAX_ALLOCATION_PCT = float(
    os.environ.get("MAX_ALLOCATION_PCT") or 20
)

MAX_DAILY_LOSS_PCT = float(
    os.environ.get("MAX_DAILY_LOSS_PCT") or 2
)

if CAPITAL <= 0:
    raise ValueError("CAPITAL must be positive")

if not (0 < RISK_PCT <= 5):
    raise ValueError("RISK_PCT must be between 0 and 5")

if not (0 < MAX_ALLOCATION_PCT <= 100):
    raise ValueError("Invalid allocation limit")

if not (0 < MAX_DAILY_LOSS_PCT <= 10):
    raise ValueError("Invalid daily loss limit")

# ==============================
# SWING TRADING SETTINGS
# ==============================

SWING = {
    "EMA_FAST": 20,
    "EMA_MID": 50,
    "EMA_SLOW": 200,

    "RSI_PERIOD": 14,
    "RSI_BULL_MIN": 50,
    "RSI_BULL_MAX": 72,
    "RSI_BEAR_MIN": 28,
    "RSI_BEAR_MAX": 50,

    "ADX_MIN": 18,

    "ATR_PERIOD": 14,
    "ATR_STOP_MULTIPLIER": 2.0,

    "TARGET_1_R": 1.5,
    "TARGET_2_R": 2.5,

    "MIN_RELATIVE_STRENGTH": 8,

    "MIN_VOLUME_RATIO": 1.2,

    "MAX_ATR_PERCENT": 8,
}

# ==============================
# STOCK UNIVERSE
# ==============================

FALLBACK_STOCKS = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK",
    "INFY", "SBIN", "BHARTIARTL", "ITC", "LT",
    "AXISBANK", "KOTAKBANK", "HINDUNILVR",
    "BAJFINANCE", "MARUTI", "SUNPHARMA",
    "TATASTEEL", "NTPC", "POWERGRID", "ONGC",
    "ADANIENT", "ADANIPORTS", "ASIANPAINT",
    "TITAN", "ULTRACEMCO", "WIPRO", "HCLTECH",
    "TECHM", "JSWSTEEL", "HINDALCO",
    "COALINDIA", "BAJAJFINSV", "M&M",
    "INDUSINDBK", "DRREDDY", "CIPLA",
    "EICHERMOT", "GRASIM", "HEROMOTOCO",
    "BPCL", "IOC", "VEDL", "DLF", "PNB",
    "BANKBARODA", "CANBK", "FEDERALBNK",
    "IDFCFIRSTB", "AUBANK", "TATAPOWER",
    "SAIL", "NMDC", "GAIL", "HAL", "BEL",
    "IRCTC", "TRENT", "LUPIN", "TVSMOTOR",
    "PFC", "RECLTD", "BHEL", "INDIGO",
    "SIEMENS", "ABB"
]

# ==============================
# SECTOR CLASSIFICATION
# ==============================

SECTORS = {
    "BANK": [
        "HDFCBANK", "ICICIBANK", "SBIN",
        "AXISBANK", "KOTAKBANK", "INDUSINDBK",
        "BANKBARODA", "PNB", "CANBK",
        "FEDERALBNK", "IDFCFIRSTB", "AUBANK"
    ],

    "IT": [
        "TCS", "INFY", "WIPRO",
        "HCLTECH", "TECHM"
    ],

    "AUTO": [
        "MARUTI", "M&M", "EICHERMOT",
        "HEROMOTOCO", "TVSMOTOR"
    ],

    "METAL": [
        "TATASTEEL", "JSWSTEEL", "HINDALCO",
        "VEDL", "SAIL", "NMDC", "COALINDIA"
    ],

    "PHARMA": [
        "SUNPHARMA", "DRREDDY", "CIPLA", "LUPIN"
    ],

    "ENERGY": [
        "RELIANCE", "ONGC", "BPCL", "IOC",
        "NTPC", "POWERGRID", "TATAPOWER", "GAIL"
    ],

    "FINANCE": [
        "BAJFINANCE", "BAJAJFINSV", "PFC", "RECLTD"
    ],

    "FMCG": [
        "ITC", "HINDUNILVR"
    ],

    "INFRA": [
        "LT", "ADANIENT", "ADANIPORTS",
        "ULTRACEMCO", "GRASIM", "DLF",
        "HAL", "BEL", "BHEL", "SIEMENS", "ABB"
    ],

    "CONSUMER": [
        "TITAN", "ASIANPAINT", "TRENT",
        "IRCTC", "INDIGO"
    ],

    "TELECOM": [
        "BHARTIARTL"
    ]
}

SECTOR_OF = {
    stock: sector
    for sector, stocks in SECTORS.items()
    for stock in stocks
}

# ==============================
# TIME SETTINGS
# ==============================

def ist_now():
    return (
        dt.datetime.now(dt.timezone.utc)
        .astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30)))
        .replace(tzinfo=None)
    )


def vol_factor():
    if MODE != "BTST":
        return 1.0

    n = ist_now()

    mins = n.hour * 60 + n.minute - (9 * 60 + 15)

    return min(1.0, max(0.3, mins / 375))
