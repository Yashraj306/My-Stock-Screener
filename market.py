import yfinance as yf
import pandas as pd
import logging

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
    "TITAN": "OTHER", "TRENT": "OTHER", "BEL": "OTHER", "ASIANPAINT": "OTHER"
}


def fetch_global_cues():
    cues_tickers = {
        "GIFT Nifty": "IN=F", "Nasdaq": "^IXIC", "Dow Jones": "^DJI",
        "S&P 500": "^GSPC", "Nikkei": "^N225", "Hang Seng": "^HSI",
        "Crude Oil": "CL=F", "US Dollar Idx": "DX-Y.NYB",
        "US 10Y Yield": "^TNX", "India VIX": "^INDIAVIX"
    }

    results = []
    try:
        data = yf.download(
            " ".join(cues_tickers.values()), period="5d", interval="1d",
            group_by="ticker", threads=True, progress=False, timeout=15
        )
    except Exception as e:
        logging.error(f"Global cues error: {e}")
        return results

    is_multi = isinstance(data.columns, pd.MultiIndex)
    for name, sym in cues_tickers.items():
        try:
            df = data[sym].dropna(how="all").copy() if is_multi else data.copy()
            if not df.empty and len(df) >= 2:
                close = df["Close"].iloc[-1]
                prev = df["Close"].iloc[-2]
                chg = ((close / prev) - 1) * 100
                results.append({
                    "name": name,
                    "val": f"{close:,.1f}" if close > 100 else f"{close:.2f}",
                    "chg": f"{chg:+.2f}%",
                    "raw_val": float(close)
                })
        except Exception:
            continue
    return results


def compute_sector_strength(candles):
    sector_moves = {}
    for sym, sec in SECTOR_MAP.items():
        df = candles.get(sym)
        if df is not None and len(df) >= 2:
            chg = ((df["Close"].iloc[-1] / df["Close"].iloc[-2]) - 1) * 100
            sector_moves.setdefault(sec, []).append(chg)

    sector_summary = []
    for sec, moves in sector_moves.items():
        if not moves:
            continue
        avg = sum(moves) / len(moves)
        up_count = sum(1 for m in moves if m > 0)
        tot = len(moves)
        read = "Strong" if avg >= 0.3 and (up_count / tot) >= 0.5 else ("Weak" if avg <= -0.3 and (up_count / tot) <= 0.5 else "Mixed")
        sector_summary.append({
            "name": sec, "avg_move": f"{avg:+.2f}%",
            "up_down": f"{up_count}/{tot}", "read": read, "raw_avg": avg
        })
    return sorted(sector_summary, key=lambda x: x["raw_avg"], reverse=True)


def compute_point_contributors(candles, nifty_spot, bnf_spot):
    nifty_weights = {"HDFCBANK": 11.0, "RELIANCE": 9.5, "ICICIBANK": 8.0, "INFY": 5.5, "ITC": 4.0, "TCS": 3.8, "LT": 3.6, "SBIN": 3.2, "BHARTIARTL": 3.0}
    bnf_weights = {"HDFCBANK": 29.0, "ICICIBANK": 24.0, "SBIN": 10.0, "KOTAKBANK": 9.0, "AXISBANK": 9.0}

    def get_contribs(weights, spot):
        gains, losses = [], []
        for sym, wt in weights.items():
            df = candles.get(sym)
            if df is not None and len(df) >= 2:
                pct = ((df["Close"].iloc[-1] / df["Close"].iloc[-2]) - 1)
                pts = round((pct * (wt / 100)) * spot)
                if pts > 0: gains.append((sym, pts))
                elif pts < 0: losses.append((sym, pts))
        return {
            "lifting": sorted(gains, key=lambda x: x[1], reverse=True)[:3],
            "dragging": sorted(losses, key=lambda x: x[1])[:3]
        }

    return {
        "NIFTY": get_contribs(nifty_weights, nifty_spot),
        "BANKNIFTY": get_contribs(bnf_weights, bnf_spot)
    }
