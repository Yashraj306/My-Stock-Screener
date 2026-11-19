import logging
import pandas as pd
import yfinance as yf

SECTOR_MAP = {
    "HDFCBANK": "BANK", "ICICIBANK": "BANK", "SBIN": "BANK", "KOTAKBANK": "BANK",
    "AXISBANK": "BANK", "INDUSINDBK": "BANK", "BANKBARODA": "BANK", "PNB": "BANK",
    "CANBK": "BANK", "FEDERALBNK": "BANK", "IDFCFIRSTB": "BANK", "AUBANK": "BANK",
    "BAJFINANCE": "FINANCE", "BAJAJFINSV": "FINANCE", "SHRIRAMFIN": "FINANCE",
    "CHOLAFIN": "FINANCE", "MUTHOOTFIN": "FINANCE", "PFC": "FINANCE", "RECLTD": "FINANCE",
    "HDFCLIFE": "FINANCE", "SBILIFE": "FINANCE", "ICICIPRULI": "FINANCE", "ICICIGI": "FINANCE",
    "TCS": "IT", "INFY": "IT", "HCLTECH": "IT", "WIPRO": "IT", "TECHM": "IT",
    "SUNPHARMA": "PHARMA", "CIPLA": "PHARMA", "DRREDDY": "PHARMA", "DIVISLAB": "PHARMA",
    "APOLLOHOSP": "PHARMA", "MARUTI": "AUTO", "M&M": "AUTO", "EICHERMOT": "AUTO",
    "HEROMOTOCO": "AUTO", "RELIANCE": "ENERGY", "NTPC": "ENERGY", "POWERGRID": "ENERGY",
    "BPCL": "ENERGY", "ONGC": "ENERGY", "COALINDIA": "ENERGY", "TATASTEEL": "METAL",
    "JSWSTEEL": "METAL", "HINDALCO": "METAL", "ITC": "FMCG", "HINDUNILVR": "FMCG",
    "NESTLEIND": "FMCG", "BRITANNIA": "FMCG", "LT": "INFRA", "BHARTIARTL": "TELECOM",
    "ADANIENT": "OTHER", "ADANIPORTS": "OTHER", "ULTRACEMCO": "OTHER", "GRASIM": "OTHER",
    "TITAN": "OTHER", "TRENT": "OTHER", "BEL": "OTHER", "ASIANPAINT": "OTHER",
}


def fetch_global_cues():
    cues_tickers = {
        "Nasdaq": "^IXIC",
        "Dow Jones": "^DJI",
        "S&P 500": "^GSPC",
        "Nikkei": "^N225",
        "Hang Seng": "^HSI",
        "Crude Oil": "CL=F",
        "US Dollar Idx": "DX-Y.NYB",
        "US 10Y Yield": "^TNX",
        "India VIX": "^INDIAVIX",
    }

    results = []
    for name, sym in cues_tickers.items():
        try:
            df = yf.download(sym, period="5d", interval="1d", progress=False, timeout=8)
            if df is not None and not df.empty and len(df) >= 2:
                if isinstance(df.columns, pd.MultiIndex):
                    close_series = df[("Close", sym)] if ("Close", sym) in df.columns else df["Close"].iloc[:, 0]
                else:
                    close_series = df["Close"]
                close_series = pd.to_numeric(close_series, errors="coerce").dropna()
                if len(close_series) >= 2:
                    close = float(close_series.iloc[-1])
                    prev = float(close_series.iloc[-2])
                    if prev != 0:
                        chg = ((close / prev) - 1.0) * 100.0
                        results.append({
                            "name": name,
                            "val": f"{close:,.1f}" if close > 100 else f"{close:.2f}",
                            "chg": f"{chg:+.2f}%",
                            "raw_val": close,
                        })
        except Exception as exc:
            logging.debug("Global cue fetch failed for %s (%s): %s", name, sym, exc)
            continue
    return results


def compute_sector_strength(candles):
    sector_moves = {}
    for sym, sec in SECTOR_MAP.items():
        df = candles.get(sym)
        if df is not None and len(df) >= 2:
            c_last = float(df["Close"].iloc[-1])
            c_prev = float(df["Close"].iloc[-2])
            if c_prev != 0:
                chg = ((c_last / c_prev) - 1.0) * 100.0
                sector_moves.setdefault(sec, []).append(chg)

    sector_summary = []
    for sec, moves in sector_moves.items():
        if not moves:
            continue
        avg = sum(moves) / len(moves)
        up_count = sum(1 for m in moves if m > 0)
        tot = len(moves)
        read = (
            "Strong"
            if avg >= 0.3 and (up_count / tot) >= 0.5
            else ("Weak" if avg <= -0.3 and (up_count / tot) <= 0.5 else "Mixed")
        )
        sector_summary.append({
            "name": sec,
            "avg_move": f"{avg:+.2f}%",
            "up_down": f"{up_count}/{tot}",
            "read": read,
            "raw_avg": avg,
        })
    return sorted(sector_summary, key=lambda x: x["raw_avg"], reverse=True)


def compute_point_contributors(candles, nifty_spot, bnf_spot):
    nifty_weights = {
        "HDFCBANK": 11.0, "RELIANCE": 9.5, "ICICIBANK": 8.0, "INFY": 5.5,
        "ITC": 4.0, "TCS": 3.8, "LT": 3.6, "SBIN": 3.2, "BHARTIARTL": 3.0,
    }
    bnf_weights = {
        "HDFCBANK": 29.0, "ICICIBANK": 24.0, "SBIN": 10.0, "KOTAKBANK": 9.0, "AXISBANK": 9.0,
    }

    def get_contribs(weights, spot):
        gains, losses = [], []
        if not spot or spot <= 0:
            return {"lifting": [], "dragging": []}
        for sym, wt in weights.items():
            df = candles.get(sym)
            if df is not None and len(df) >= 2:
                c_last = float(df["Close"].iloc[-1])
                c_prev = float(df["Close"].iloc[-2])
                if c_prev != 0:
                    pct_move = (c_last / c_prev) - 1.0
                    pts = round(pct_move * (wt / 100.0) * float(spot))
                    if pts > 0:
                        gains.append((sym, pts))
                    elif pts < 0:
                        losses.append((sym, pts))
        return {
            "lifting": sorted(gains, key=lambda x: x[1], reverse=True)[:3],
            "dragging": sorted(losses, key=lambda x: x[1])[:3],
        }

    return {
        "NIFTY": get_contribs(nifty_weights, nifty_spot),
        "BANKNIFTY": get_contribs(bnf_weights, bnf_spot),
    }
