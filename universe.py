"""
Core Indices & Constituent Stocks Universe for High-Speed F&O Screening.
Excluded: TATAMOTORS, LTIM (Yahoo 404 / Delisting issues).
"""

INDEX_MAP = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
    "SENSEX": "BSE_INDEX|SENSEX"
}

STOCKS_UNIVERSE = [
    # Heavyweights & Common Large Caps
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "BHARTIARTL", "SBIN",
    "ITC", "HINDUNILVR", "LT", "BAJFINANCE", "HCLTECH", "MARUTI", "SUNPHARMA",
    "ADANIENT", "KOTAKBANK", "TITAN", "NTPC", "AXISBANK",
    "POWERGRID", "ADANIPORTS", "ULTRACEMCO", "BAJAJFINSV", "WIPRO", "M&M",
    "JSWSTEEL", "TATASTEEL", "TECHM", "NESTLEIND",
    
    # Active F&O Names
    "COALINDIA", "GRASIM", "BPCL", "EICHERMOT", "CIPLA", "DRREDDY",
    "BRITANNIA", "APOLLOHOSP", "DIVISLAB", "HINDALCO", "SHRIRAMFIN",
    "TRENT", "BEL", "HEROMOTOCO", "ONGC", "ASIANPAINT",
    
    # Financial Services & Banks
    "INDUSINDBK", "BANKBARODA", "PNB", "CANBK", "FEDERALBNK", "IDFCFIRSTB", "AUBANK"
]

def eq_keys():
    mapping = {k: v for k, v in INDEX_MAP.items()}
    for s in STOCKS_UNIVERSE:
        mapping[s] = f"NSE_EQ|{s}"
    return mapping
