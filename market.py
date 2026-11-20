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
    "PERSISTENT": "IT", "COFORGE": "IT",
    "SUNPHARMA": "PHARMA", "CIPLA": "PHARMA", "DRREDDY": "PHARMA", "DIVISLAB": "PHARMA",
    "MAXHEALTH": "PHARMA", "LUPIN": "PHARMA", "AUROPHARMA": "PHARMA",
    "MARUTI": "AUTO", "M&M": "AUTO", "EICHERMOT": "AUTO", "HEROMOTOCO": "AUTO", "TVSMOTOR": "AUTO",
    "RELIANCE": "ENERGY", "NTPC": "ENERGY", "POWERGRID": "ENERGY",
    "BPCL": "ENERGY", "ONGC": "ENERGY", "COALINDIA": "ENERGY",
    "TATASTEEL": "METAL", "JSWSTEEL": "METAL", "HINDALCO": "METAL",
    "ITC": "FMCG", "HINDUNILVR": "FMCG", "NESTLEIND": "FMCG", "BRITANNIA": "FMCG",
    "JUBLFOOD": "FMCG", "PAGEIND": "FMCG",
    "LT": "INFRA", "BHARTIARTL": "TELECOM",
    "ADANIENT": "OTHER", "ADANIPORTS": "OTHER", "ULTRACEMCO": "OTHER", "GRASIM": "OTHER",
    "TITAN": "OTHER", "TRENT": "OTHER", "BEL": "OTHER", "ASIANPAINT": "OTHER",
    "HAL": "OTHER", "DIXON": "OTHER", "POLYCAB": "OTHER", "CUMMINSIND": "OTHER",
    "INDHOTEL": "OTHER", "IRCTC": "OTHER", "ASTRAL": "OTHER",
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


def compute_relative_strength(candles: dict) -> dict:
    """Computes 20-day and 5-day excess return (%) vs NIFTY for each symbol."""
    nifty_df = candles.get("NIFTY")
    if nifty_df is None or len(nifty_df) < 6:
        return {}

    n_close = pd.to_numeric(nifty_df["Close"], errors="coerce").dropna()
    if len(n_close) < 6 or float(n_close.iloc[-1]) <= 0:
        return {}

    n_5d = ((float(n_close.iloc[-1]) / float(n_close.iloc[-6])) - 1.0) * 100.0
    n_20d = (
        ((float(n_close.iloc[-1]) / float(n_close.iloc[-21])) - 1.0) * 100.0
        if len(n_close) >= 21 and float(n_close.iloc[-21]) > 0
        else n_5d
    )

    rs_map = {}
    for sym, df in candles.items():
        if sym in {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"} or df is None or len(df) < 6:
            continue
        s_close = pd.to_numeric(df["Close"], errors="coerce").dropna()
        if len(s_close) < 6 or float(s_close.iloc[-1]) <= 0:
            continue
        s_5d = ((float(s_close.iloc[-1]) / float(s_close.iloc[-6])) - 1.0) * 100.0
        s_20d = (
            ((float(s_close.iloc[-1]) / float(s_close.iloc[-21])) - 1.0) * 100.0
            if len(s_close) >= 21 and float(s_close.iloc[-21]) > 0
            else s_5d
        )
        rs_5d = round(s_5d - n_5d, 2)
        rs_20d = round(s_20d - n_20d, 2)
        rs_map[sym] = {
            "rs_5d": rs_5d,
            "rs_20d": rs_20d,
            "rs_score": round(0.6 * rs_20d + 0.4 * rs_5d, 2),
        }
    return rs_map


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
