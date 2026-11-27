import argparse
import logging
import numpy as np
import pandas as pd
import yfinance as yf

from setups import btst_rally_score

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def _compute_daily_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    high = pd.to_numeric(out["High"], errors="coerce")
    low = pd.to_numeric(out["Low"], errors="coerce")
    close = pd.to_numeric(out["Close"], errors="coerce")
    vol = pd.to_numeric(out["Volume"], errors="coerce").fillna(0.0)

    tr = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr14"] = tr.rolling(14).mean()
    out["vol_ratio_20"] = vol / vol.rolling(20).mean().replace(0, np.nan)
    out["ema20"] = close.ewm(span=20, adjust=False).mean()
    out["ema50"] = close.ewm(span=50, adjust=False).mean()
    out["ret_5d_pct"] = close.pct_change(5) * 100.0
    span_hl = (high - low).replace(0, np.nan)
    out["close_pos_ratio"] = ((close - low) / span_hl).clip(0.0, 1.0).fillna(0.5)

    delta = close.diff()
    gain = delta.clip(lower=0.0).rolling(14).mean()
    loss = (-delta.clip(upper=0.0)).rolling(14).mean().replace(0, np.nan)
    rs = gain / loss
    out["rsi14"] = 100.0 - (100.0 / (1.0 + rs))
    return out


def run_btst_backtest_6m(symbols: list[str], months: int = 6) -> pd.DataFrame:
    period_str = f"{max(int(months), 1)}mo"
    nifty_raw = yf.download("^NSEI", period=period_str, interval="1d", progress=False, auto_adjust=False)
    nifty_close = pd.to_numeric(nifty_raw["Close"], errors="coerce").dropna() if nifty_raw is not None and not nifty_raw.empty else pd.Series(dtype=float)
    nifty_5d = nifty_close.pct_change(5) * 100.0 if not nifty_close.empty else pd.Series(dtype=float)

    trades: list[dict] = []
    for sym in symbols:
        raw = yf.download(f"{sym}.NS", period=period_str, interval="1d", progress=False, auto_adjust=False)
        if raw is None or raw.empty or len(raw) < 25:
            continue
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = [c[0] for c in raw.columns]
        feat = _compute_daily_features(raw)

        for i in range(21, len(feat) - 1):
            sub_hist = feat.iloc[: i + 1]
            nxt = feat.iloc[i + 1]
            cur = feat.iloc[i]

            close_val = float(cur["Close"] or 0.0)
            atr_val = float(cur["atr14"] or 0.0)
            low_val = float(cur["Low"] or close_val)
            if close_val <= 0 or atr_val <= 0:
                continue

            dt_idx = feat.index[i]
            n_5d_val = float(nifty_5d.get(dt_idx, 0.0) or 0.0) if not nifty_5d.empty else 0.0

            row_dict = {
                "close": close_val,
                "entry": close_val,
                "atr": atr_val,
                "day_low": low_val,
                "close_pos_ratio": float(cur["close_pos_ratio"] or 0.5),
                "vol_ratio": float(cur["vol_ratio_20"] or 0.0),
                "ema20": float(cur["ema20"] or close_val),
                "ema50": float(cur["ema50"] or close_val),
                "ret_5d_pct": float(cur["ret_5d_pct"] or 0.0),
                "rsi": None if pd.isna(cur["rsi14"]) else float(cur["rsi14"]),
            }
            res = btst_rally_score(row_dict, df=sub_hist, nifty_5d_pct=n_5d_val, sector_chg_pct=0.0)
            if not res:
                continue

            entry = float(res["entry"])
            t1 = float(res["target_1"])
            sl = float(res["stop_loss"])
            nxt_high = float(nxt["High"] or entry)
            nxt_low = float(nxt["Low"] or entry)
            nxt_close = float(nxt["Close"] or entry)

            # Next-day hit check: high >= Target_1 before low <= SL
            if nxt_low <= sl and nxt_high >= t1:
                hit = False
                exit_px = sl
            elif nxt_high >= t1:
                hit = True
                exit_px = t1
            elif nxt_low <= sl:
                hit = False
                exit_px = sl
            else:
                hit = nxt_close > entry
                exit_px = nxt_close

            ret_pct = ((exit_px - entry) / entry) * 100.0
            sc = float(res["btst_score"])
            bucket = "80-100" if sc >= 80 else ("60-79" if sc >= 60 else ("40-59" if sc >= 40 else "0-39"))
            trades.append({"symbol": sym, "score": sc, "bucket": bucket, "hit_t1": hit, "ret_pct": ret_pct})

    tdf = pd.DataFrame(trades)
    if tdf.empty:
        print("No BTST signals generated in backtest window.")
        return tdf

    summary = (
        tdf.groupby("bucket", observed=False)
        .agg(trades=("ret_pct", "count"), hit_rate_pct=("hit_t1", lambda s: round(float(s.mean()) * 100.0, 2)), avg_ret_pct=("ret_pct", lambda s: round(float(s.mean()), 2)))
        .reset_index()
    )
    print(summary.to_string(index=False))
    return tdf


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["RELIANCE", "TCS", "INFY", "TATASTEEL", "HDFCBANK"])
    parser.add_argument("--months", type=int, default=6)
    args = parser.parse_args()
    run_btst_backtest_6m(args.symbols, months=args.months)
