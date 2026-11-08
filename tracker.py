import os
import pandas as pd
from config import *

LOG = "signals_log.csv"
COLS = ["date", "mode", "name", "direction", "setup", "grade", "entry", "stop", "t1", "result", "ret_pct", "evaluated"]

def _read():
    if os.path.exists(LOG):
        try: return pd.read_csv(LOG, dtype=str).fillna("")
        except Exception: pass
    return pd.DataFrame(columns=COLS)

def log_signals(items):
    if not items: return
    df, today = _read(), ist_now().date().isoformat()
    seen = set(zip(df["date"], df["mode"], df["name"]))
    new = [dict(date=today, mode=MODE, name=r["name"], direction=r["direction"], setup=r["setup"], grade=r["grade"],
                entry=f"{r['lv']['entry']:.2f}", stop=f"{r['lv']['stop']:.2f}", t1=f"{r['lv']['t1']:.2f}",
                result="", ret_pct="", evaluated="") for r in items if (today, MODE, r["name"]) not in seen]
    pd.concat([df, pd.DataFrame(new, columns=COLS)], ignore_index=True).to_csv(LOG, index=False)

def _outcome(row, bar):
    s = 1 if row["direction"] == "BULLISH" else -1
    entry, stop, t1 = float(row["entry"]), float(row["stop"]), float(row["t1"])
    hi, lo, cl = float(bar["High"]), float(bar["Low"]), float(bar["Close"])
    if row["setup"] == "PRE" and row["mode"] == "MORNING":
        if not ((hi >= entry) if s == 1 else (lo <= entry)): return "NO TRIGGER", 0.0
    hit_t = (hi >= t1) if s == 1 else (lo <= t1)
    hit_s = (lo <= stop) if s == 1 else (hi >= stop)
    if hit_s: return "STOP", (stop / entry - 1) * s * 100        # stop first when both touched (conservative)
    if hit_t: return "T1", (t1 / entry - 1) * s * 100
    return "CLOSE", (cl / entry - 1) * s * 100

def evaluate():
    import yfinance as yf
    df = _read()
    if df.empty: return None
    today, now = ist_now().date(), ist_now()
    for name in df.loc[df["evaluated"] != "Y", "name"].unique():
        try:
            h = yf.download(INDICES.get(name, f"{name}.NS"), period="3mo", interval="1d", auto_adjust=True, progress=False)
            if isinstance(h.columns, pd.MultiIndex): h.columns = h.columns.get_level_values(0)
            h.index = [x.date() for x in h.index]
        except Exception:
            continue
        for i in df.index[(df["name"] == name) & (df["evaluated"] != "Y")]:
            row = df.loc[i]
            sd = dt.date.fromisoformat(row["date"])
            days = [x for x in h.index if (x > sd if row["mode"] == "BTST" else x >= sd)]
            if not days: continue
            sess = days[0]
            if not (sess < today or (sess == today and now.hour >= 16)): continue
            res, ret = _outcome(row, h.loc[sess])
            df.loc[i, ["result", "ret_pct", "evaluated"]] = [res, f"{ret:.2f}", "Y"]
    df.to_csv(LOG, index=False)
    return summarize(df)

def _stat(x):
    if x.empty: return None
    r = x["ret_pct"].astype(float)
    return dict(n=len(x), win=float((r > 0).mean() * 100), t1=float((x["result"] == "T1").mean() * 100),
                stop=float((x["result"] == "STOP").mean() * 100), avg=float(r.mean()))

def summarize(df):
    ev = df[(df["evaluated"] == "Y") & (df["result"] != "NO TRIGGER")].tail(60)
    if ev.empty: return None
    return dict(all=_stat(ev), bull=_stat(ev[ev["direction"] == "BULLISH"]), bear=_stat(ev[ev["direction"] == "BEARISH"]),
                btst=_stat(ev[ev["mode"] == "BTST"]), morning=_stat(ev[ev["mode"] == "MORNING"]))
