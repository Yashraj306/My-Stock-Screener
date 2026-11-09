"""
Core Indices & Constituent Stocks Universe for High-Speed F&O Screening.
Includes:
- Indices: NIFTY, BANKNIFTY, SENSEX
- All unique constituents belonging to Nifty 50, Bank Nifty, and Sensex baskets.
"""

INDEX_MAP = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "SENSEX": "BSE_INDEX|SENSEX"
}

# Unique high-liquidity F&O constituents across Nifty 50, Bank Nifty, and Sensex
STOCKS_UNIVERSE = [
    # Heavyweights & Common across Nifty 50 & Sensex
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "BHARTIARTL", "SBIN",
    "ITC", "HINDUNILVR", "LT", "BAJFINANCE", "HCLTECH", "MARUTI", "SUNPHARMA",
    "ADANIENT", "KOTAKBANK", "TITAN", "TATAMOTORS", "NTPC", "AXISBANK",
    "POWERGRID", "ADANIPORTS", "ULTRACEMCO", "BAJAJFINSV", "WIPRO", "M&M",
    "JSWSTEEL", "TATASTEEL", "TECHM", "NESTLEIND",
    
    # Remaining Nifty 50 stocks
    "COALINDIA", "GRASIM", "BPCL", "EICHERMOT", "CIPLA", "DRREDDY",
    "BRITANNIA", "APOLLOHOSP", "DIVISLAB", "HINDALCO", "SHRIRAMFIN",
    "TRENT", "BEL", "HEROMOTOCO", "ONGC", "LTIM", "ASIANPAINT",
    
    # Remaining Bank Nifty stocks
    "INDUSINDBK", "BANKBARODA", "PNB", "CANBK", "FEDERALBNK", "IDFCFIRSTB", "AUBANK"
]

def eq_keys():
    """Returns mapping for legacy modules if required."""
    mapping = {k: v for k, v in INDEX_MAP.items()}
    for s in STOCKS_UNIVERSE:
        mapping[s] = f"NSE_EQ|{s}"
    return mapping
