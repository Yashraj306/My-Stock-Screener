import time
import datetime as dt
import requests
import yfinance as yf
from config import *
from tech import *
from options_data import *
from report import *

# ---------- DATA FRESHNESS ----------
def expected_session(ist):
    """Last market session whose daily candle should be complete at this IST time."""
    d = ist.date()
    if ist.weekday() < 5 and ist.hour * 60 + ist.minute >= 15 * 60 + 45:
        return d
    d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d

def freshness_note(asof):
    ist = dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)
    exp = expected_session(ist)
    got = dt.date.fromisoformat(asof)
    if got < exp:
        return f"Price data as of {asof}. WARNING: STALE, expected {exp} (ignore if market holiday)", True
    if got > exp:
        return f"Price data as of {asof}. Includes a live, unfinished candle, levels may change", True
    return f"Price data as of {asof}", False

# ---------- MAIN ----------
def main():
    gl, gscore, gtone, gnote = global_cues()

    symbols = {**INDICES, **{s: f"{s}.NS" for s in STOCKS}}
    data = yf.download(list(symbols.values()), period="1y", interval="1d", group_by="ticker",
                       auto_adjust=True, progress=False, threads=True)
    avail = set(data.columns.get_level_values(0))
    tech = {}
    for name, tk in symbols.items():
        if tk in avail:
            t = analyze(data[tk])
            if t: tech[name] = t

    secs = sector_table(tech)
    nifty_lvl = tech["NIFTY"]["price"] if "NIFTY" in tech else 24000
    bank_lvl = tech["BANKNIFTY"]["price"] if "BANKNIFTY" in tech else 52000
    nc = contribution(tech, NIFTY_W, nifty_lvl)
    bc = contribution(tech, BANKNIFTY_W, bank_lvl)

    cands = []
    for name, t in tech.items():
        strong = direction_of(t)
        d = strong or direction_weak(t)
        if d:
            r = dict(t); r.update(name=name, direction=d, weak=strong is None, is_index=name in INDICES,
                                  sector=SECTOR_OF.get(name, "INDEX"))
            cands.append(r)
    cands.sort(key=lambda x: abs(x["diff"]), reverse=True)
    short = [c for c in cands if c["direction"] == "BULLISH"][:8] + [c for c in cands if c["direction"] == "BEARISH"][:8]
    for r in cands:
        if r["is_index"] and r not in short: short.append(r)

    nse_sess = None
    used_src = set()
    for r in short:
        res = chain_upstox(r["name"]) if UPSTOX_TOKEN else None
        if res is None:
            if nse_sess is None:
                try:
                    nse_sess = requests.Session()
                    nse_sess.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                                             "Accept": "application/json", "Referer": "https://www.nseindia.com/option-chain"})
                    nse_sess.get("https://www.nseindia.com", timeout=15)
                except Exception:
                    nse_sess = False
            if nse_sess:
                res = chain_nse(r["name"], nse_sess)
        r["oc"] = None
        if res:
            df, exp, src = res
            r["oc"] = analyze_chain(df, exp, src, r["chg"], r["direction"])
            if r["oc"]: used_src.add(src)
        make_signal(r, gtone)
        if r["weak"]:
            r["conf"] = "LOW"
            r["action"] += " (WATCH)"
            r["reasons"].insert(0, "Weaker setup: fewer confirmations, watchlist only")
        time.sleep(0.4)

    short.sort(key=lambda x: (not x["weak"], x["score"]), reverse=True)
    top = [x for x in short if x["direction"] == "BULLISH"][:5] + [x for x in short if x["direction"] == "BEARISH"][:5]
    nifty_row = tech.get("NIFTY"); bank_row = tech.get("BANKNIFTY")
    today = dt.datetime.now().strftime("%A, %d %b %Y")
    src_note = "Yahoo Finance + " + (", ".join(sorted(used_src)) if used_src else "no option chain")
    asof = (nifty_row or next(iter(tech.values())))["asof"]
    fresh_txt, fresh_bad = freshness_note(asof)
    src_note += " | " + fresh_txt
    html = build_html(today, gl, gscore, gtone, gnote, secs, nc, bc, top, src_note, nifty_row, bank_row)
    send_mail(html, ("CHECK DATA | " if fresh_bad else "") + f"Pre-Market Screener | {gtone} global cues | {today}")
    print("sent", [t["name"] for t in top])

if __name__ == "__main__":
    main()

