import yfinance as yf
import pandas as pd


GLOBAL_TICKERS = {
    "Nasdaq": "^IXIC",
    "Dow Jones": "^DJI",
    "S&P 500": "^GSPC",
    "Nikkei": "^N225",
    "Hang Seng": "^HSI",
    "Crude Oil": "CL=F",
    "US Dollar Idx": "DX-Y.NYB",
    "US 10Y Yield": "^TNX",
    "India VIX": "^INDIAVIX"
}

SECTOR_MAP = {
    "RELIANCE": "ENERGY", "ONGC": "ENERGY", "BPCL": "ENERGY", "IOC": "ENERGY",
    "HDFCBANK": "BANK", "ICICIBANK": "BANK", "SBIN": "BANK", "KOTAKBANK": "BANK", "AXISBANK": "BANK", "BANKBARODA": "BANK", "PNB": "BANK",
    "TCS": "IT", "INFY": "IT", "HCLTECH": "IT", "WIPRO": "IT", "TECHM": "IT", "LTIM": "IT",
    "SUNPHARMA": "PHARMA", "CIPLA": "PHARMA", "DRREDDY": "PHARMA", "DIVISLAB": "PHARMA",
    "TATASTEEL": "METAL", "JSWSTEEL": "METAL", "HINDALCO": "METAL", "VEDL": "METAL",
    "MARUTI": "AUTO", "TATAMOTORS": "AUTO", "M&M": "AUTO", "EICHERMOT": "AUTO", "HEROMOTOCO": "AUTO",
    "ITC": "FMCG", "HINDUNILVR": "FMCG", "NESTLEIND": "FMCG", "BRITANNIA": "FMCG",
    "LT": "INFRA", "POWERGRID": "INFRA", "NTPC": "INFRA",
    "BHARTIARTL": "TELECOM"
}

INDEX_WEIGHTS_NIFTY = {
    "HDFCBANK": 11.5, "RELIANCE": 9.2, "ICICIBANK": 7.9, "INFY": 5.8, "ITC": 4.3,
    "TCS": 4.1, "BHARTIARTL": 3.9, "LT": 3.6, "AXISBANK": 3.4, "SBIN": 3.1
}

INDEX_WEIGHTS_BNF = {
    "HDFCBANK": 29.1, "ICICIBANK": 23.4, "SBIN": 11.2, "AXISBANK": 9.8, "KOTAKBANK": 9.1
}


def fetch_global_cues():
    """Fetches world indices moves for Pre-market cues."""
    cues = []
    try:
        data = yf.download(" ".join(GLOBAL_TICKERS.values()), period="5d", interval="1d", progress=False)
        if isinstance(data.columns, pd.MultiIndex):
            close = data["Close"]
        else:
            close = data

        for name, sym in GLOBAL_TICKERS.items():
            if sym in close.columns:
                s = close[sym].dropna()
                if len(s) >= 2:
                    curr, prev = s.iloc[-1], s.iloc[-2]
                    chg = ((curr / prev) - 1) * 100
                    cues.append({
                        "name": name,
                        "val": f"{curr:,.1f}",
                        "chg": f"{'+' if chg>=0 else ''}{chg:.2f}%"
                    })
    except Exception:
        pass
    return cues


def compute_sector_strength(stock_candles):
    """Calculates sector average move and Read (Strong / Mixed / Weak)."""
    sector_moves = {}
    for sym, df in stock_candles.items():
        sec = SECTOR_MAP.get(sym, "OTHER")
        if df is not None and len(df) >= 2:
            c, p = df["Close"].iloc[-1], df["Close"].iloc[-2]
            move = ((c / p) - 1) * 100
            sector_moves.setdefault(sec, []).append(move)

    res = []
    for sec, moves in sector_moves.items():
        if not moves:
            continue
        avg = sum(moves) / len(moves)
        up = sum(1 for m in moves if m > 0)
        tot = len(moves)
        read = "Strong" if avg >= 0.5 and (up / tot) >= 0.6 else ("Weak" if avg <= -0.5 else "Mixed")
        res.append({
            "name": sec,
            "avg_move": f"{'+' if avg>=0 else ''}{avg:.2f}%",
            "up_down": f"{up}/{tot}",
            "read": read,
            "raw_avg": avg
        })
    return sorted(res, key=lambda x: x["raw_avg"], reverse=True)


def compute_point_contributors(stock_candles, nifty_spot, bnf_spot):
    """Computes approx lifting vs dragging points on Nifty and Bank Nifty."""
    def _get_contributors(weight_map, base_spot):
        lifting, dragging = [], []
        for sym, weight in weight_map.items():
            df = stock_candles.get(sym)
            if df is not None and len(df) >= 2:
                pct = ((df["Close"].iloc[-1] / df["Close"].iloc[-2]) - 1)
                pts = round(pct * (weight / 100.0) * base_spot)
                if pts > 0:
                    lifting.append((sym, pts))
                elif pts < 0:
                    dragging.append((sym, pts))
        lifting = sorted(lifting, key=lambda x: x[1], reverse=True)[:3]
        dragging = sorted(dragging, key=lambda x: x[1])[:3]
        return lifting, dragging

    n_lift, n_drag = _get_contributors(INDEX_WEIGHTS_NIFTY, nifty_spot)
    b_lift, b_drag = _get_contributors(INDEX_WEIGHTS_BNF, bnf_spot)

    return {
        "NIFTY": {"lifting": n_lift, "dragging": n_drag},
        "BANKNIFTY": {"lifting": b_lift, "dragging": b_drag}
    }
