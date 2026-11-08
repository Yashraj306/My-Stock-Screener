import os, io, gzip, json, time, ssl, smtplib, datetime as dt
import numpy as np
import pandas as pd
import requests
 
EMAIL_USER = os.environ["EMAIL_USER"]
EMAIL_PASS = os.environ["EMAIL_PASS"]
EMAIL_TO = os.environ.get("EMAIL_TO", EMAIL_USER)
UPSTOX_TOKEN = os.environ.get("UPSTOX_TOKEN", "")
MODE = os.environ.get("MODE", "MORNING")          # MORNING or BTST
TITLE = "BTST Options Screener" if MODE == "BTST" else "Pre-Market Options Screener"
 
INDICES = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}
CHAIN_LIMITS = {"PRE": 8, "POST": 6}   # candidates per side sent to option-chain analysis
SHOW_LIMITS = {"PRE": 5, "POST": 4}    # ideas shown per side
 
# used only if the Upstox instrument file cannot be read
FALLBACK_STOCKS = [
    "RELIANCE","TCS","HDFCBANK","ICICIBANK","INFY","SBIN","BHARTIARTL","ITC","LT","AXISBANK","KOTAKBANK",
    "HINDUNILVR","BAJFINANCE","MARUTI","SUNPHARMA","TATASTEEL","NTPC","POWERGRID","ONGC","ADANIENT",
    "ADANIPORTS","ASIANPAINT","TITAN","ULTRACEMCO","WIPRO","HCLTECH","TECHM","JSWSTEEL","HINDALCO",
    "COALINDIA","BAJAJFINSV","M&M","INDUSINDBK","DRREDDY","CIPLA","EICHERMOT","GRASIM","HEROMOTOCO",
    "BPCL","IOC","VEDL","DLF","PNB","BANKBARODA","CANBK","FEDERALBNK","IDFCFIRSTB","AUBANK","TATAPOWER",
    "SAIL","NMDC","GAIL","HAL","BEL","IRCTC","TRENT","LUPIN","TVSMOTOR","PFC","RECLTD","BHEL","INDIGO",
    "SIEMENS","ABB",
]
SECTORS = {
    "BANK": ["HDFCBANK","ICICIBANK","SBIN","AXISBANK","KOTAKBANK","INDUSINDBK","BANKBARODA","PNB","CANBK","FEDERALBNK","IDFCFIRSTB","AUBANK"],
    "IT": ["TCS","INFY","WIPRO","HCLTECH","TECHM"],
    "AUTO": ["MARUTI","M&M","EICHERMOT","HEROMOTOCO","TVSMOTOR"],
    "METAL": ["TATASTEEL","JSWSTEEL","HINDALCO","VEDL","SAIL","NMDC","COALINDIA"],
    "PHARMA": ["SUNPHARMA","DRREDDY","CIPLA","LUPIN"],
    "ENERGY": ["RELIANCE","ONGC","BPCL","IOC","NTPC","POWERGRID","TATAPOWER","GAIL"],
    "FINANCE": ["BAJFINANCE","BAJAJFINSV","PFC","RECLTD"],
    "FMCG": ["ITC","HINDUNILVR"],
    "INFRA": ["LT","ADANIENT","ADANIPORTS","ULTRACEMCO","GRASIM","DLF","HAL","BEL","BHEL","SIEMENS","ABB"],
    "CONSUMER": ["TITAN","ASIANPAINT","TRENT","IRCTC","INDIGO"],
    "TELECOM": ["BHARTIARTL"],
}
SECTOR_OF = {s: k for k, v in SECTORS.items() for s in v}
 
# Approximate index weights (%). Update every March/September from niftyindices.com
NIFTY_W = {"HDFCBANK":13.0,"RELIANCE":8.5,"ICICIBANK":8.5,"INFY":5.0,"BHARTIARTL":5.0,"TCS":3.5,"ITC":3.5,"LT":3.5,
    "AXISBANK":3.0,"SBIN":3.0,"KOTAKBANK":2.7,"M&M":2.5,"BAJFINANCE":2.3,"HCLTECH":1.8,"SUNPHARMA":1.7,"MARUTI":1.7,
    "NTPC":1.5,"TITAN":1.5,"ULTRACEMCO":1.3,"TATASTEEL":1.2,"POWERGRID":1.2,"ASIANPAINT":1.0,"HINDUNILVR":2.0,
    "BAJAJFINSV":1.1,"ADANIPORTS":1.1,"JSWSTEEL":1.0,"ONGC":0.9,"COALINDIA":0.8,"WIPRO":0.7,"TECHM":0.7,
    "INDUSINDBK":0.6,"HINDALCO":0.9,"GRASIM":0.9,"CIPLA":0.6,"DRREDDY":0.6,"EICHERMOT":0.7,"HEROMOTOCO":0.5,
    "BPCL":0.5,"ADANIENT":0.8,"TRENT":0.8,"INDIGO":0.8}
BANKNIFTY_W = {"HDFCBANK":29.0,"ICICIBANK":24.0,"SBIN":9.5,"AXISBANK":8.5,"KOTAKBANK":7.5,"INDUSINDBK":3.0,
    "BANKBARODA":3.0,"PNB":2.5,"CANBK":2.5,"FEDERALBNK":2.5,"IDFCFIRSTB":2.0,"AUBANK":2.0}
 
GLOBAL = {
    "Nasdaq": ("^IXIC", 1.0), "Dow Jones": ("^DJI", 1.0), "S&P 500": ("^GSPC", 1.0),
    "Nikkei": ("^N225", 0.6), "Hang Seng": ("^HSI", 0.6),
    "Crude Oil": ("CL=F", -0.3), "US Dollar Idx": ("DX-Y.NYB", -0.4),
    "US 10Y Yield": ("^TNX", -0.3), "India VIX": ("^INDIAVIX", -0.5),
}
 
def ist_now():
    return dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)
 
def vol_factor():
    """Fraction of the day's volume normally done by now (BTST runs mid-session)."""
    if MODE != "BTST": return 1.0
    n = ist_now()
    mins = n.hour * 60 + n.minute - (9 * 60 + 15)
    return min(1.0, max(0.3, mins / 375))
 
# ---------- RISK / ALERT SETTINGS (set as repo secrets or edit here) ----------
CAPITAL = float(os.environ.get("CAPITAL") or 100000)     # trading capital in Rs
RISK_PCT = float(os.environ.get("RISK_PCT") or 1)        # max loss per trade, % of capital
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
 
