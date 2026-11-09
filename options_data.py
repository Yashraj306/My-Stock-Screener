import yfinance as yf

def fetch_upstox_candles(symbol, interval="day", days=90):
    """
    Tries Upstox direct first. If Token is missing or invalid, seamlessly falls back to Yahoo.
    """
    # 1. Try Upstox API
    if UPSTOX_TOKEN:
        key = underlying_key(symbol)
        if key:
            to_date = ist_now().strftime("%Y-%m-%d")
            from_date = (ist_now() - dt.timedelta(days=days)).strftime("%Y-%m-%d")
            path = f"/v2/historical-candle/{key}/{interval}/{to_date}/{from_date}"
            res = upstox_get(path)
            if res and res.get("status") == "success" and res.get("data", {}).get("candles"):
                candles = res["data"]["candles"]
                df = pd.DataFrame(candles, columns=["Date", "Open", "High", "Low", "Close", "Volume", "OI"])
                df["Date"] = pd.to_datetime(df["Date"])
                df = df.sort_values(by="Date").reset_index(drop=True)
                for col in ["Open", "High", "Low", "Close", "Volume"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                return df

    # 2. Reliable Fast Fallback (Yahoo Finance)
    ticker_sym = "^NSEI" if symbol == "NIFTY" else ("^NSEBANK" if symbol == "BANKNIFTY" else ("^BSESN" if symbol == "SENSEX" else f"{symbol}.NS"))
    try:
        ydf = yf.download(ticker_sym, period="3mo", interval="1d", progress=False)
        if not ydf.empty and len(ydf) >= 15:
            if isinstance(ydf.columns, pd.MultiIndex):
                ydf.columns = ydf.columns.get_level_values(0)
            ydf = ydf.reset_index().rename(columns=lambda c: str(c).capitalize())
            return ydf
    except Exception:
        pass

    return None
