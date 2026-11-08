import numpy as np
import yfinance as yf
from config import *
from universe import fo_stocks, lot_sizes
from tech import analyze, direction_of
from setups import prerally, levels, flags_for, btst_gate, grade
from market import global_cues, sector_table, contribution, freshness_note, breadth, is_trading_day
from notify import send_telegram, telegram_text
from options_data import chain_upstox, chain_nse, nse_session
from chain import analyze_chain
from tracker import log_signals, evaluate
from report import build_html, build_stale_html, send_mail

def fetch(tickers, period="1y"):
    out = {}
    tickers = list(tickers)
    for part in np.array_split(tickers, max(1, -(-len(tickers) // 80))):
        part = list(part)
        try:
            f = yf.download(part, period=period, interval="1d", group_by="ticker", auto_adjust=True, progress=False, threads=True)
            have = set(f.columns.get_level_values(0))
            out.update({t: f[t] for t in part if t in have})
        except Exception as e:
            print("download chunk failed:", e)
    return out

def main():
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule" and not is_trading_day(ist_now().date()):
        print("Market closed today, no report."); return
    stocks, uni_note = fo_stocks()
    lots = lot_sizes()
    symbols = {**INDICES, **{s: f"{s}.NS" for s in stocks}}
    data = fetch(symbols.values())
    tech, dfs = {}, {}
    for name, tk in symbols.items():
        if tk in data:
            t = analyze(data[tk], is_index=name in INDICES)
            if t: tech[name], dfs[name] = t, data[tk]
    today = ist_now().strftime("%A, %d %b %Y")
    if "NIFTY" not in tech:
        send_mail(build_stale_html("Nifty price data could not be loaded."), f"{TITLE} | data problem | {today}"); return
    fresh_txt, level = freshness_note(tech["NIFTY"]["asof"])
    if level == "stale":
        send_mail(build_stale_html(fresh_txt), f"CHECK DATA | {TITLE} | {today}"); return

    gl, gscore, gtone, gnote = global_cues()
    secs = sector_table(tech)
    sec_avg = {s: a for s, a, _, _ in secs}
    nf, bf = tech["NIFTY"], tech.get("BANKNIFTY")
    br = breadth(tech)
    nc = contribution(tech, NIFTY_W, nf["price"])
    bc = contribution(tech, BANKNIFTY_W, bf["price"] if bf else 0)

    cands = []
    for name, t in tech.items():
        idx = name in INDICES
        base = dict(t, name=name, is_index=idx, sector=SECTOR_OF.get(name, "INDEX" if idx else "OTHER"), oc=None, flags=[])
        pre = prerally(dfs[name], t, idx) if abs(t["chg"]) < 2.5 else None
        if pre: cands.append(dict(base, setup="PRE", direction=pre["direction"], trigger=pre["trigger"],
                                  pre_score=pre["pre_score"], why=pre["tags"], rank=pre["pre_score"]))
        d = direction_of(t)
        if d: cands.append(dict(base, setup="POST", direction=d, rank=abs(t["diff"])))

    sel = []
    for kind in ("PRE", "POST"):
        for side in ("BULLISH", "BEARISH"):
            grp = [r for r in cands if r["setup"] == kind and r["direction"] == side]
            sel += sorted(grp, key=lambda r: r["rank"], reverse=True)[:CHAIN_LIMITS[kind]]

    sess, used = None, set()
    for r in sel:
        res = chain_upstox(r["name"]) if UPSTOX_TOKEN else None
        if res is None:
            sess = sess or nse_session()
            res = chain_nse(r["name"], sess) if sess else None
        if res:
            r["oc"] = analyze_chain(res[0], res[1], res[2], r["chg"], r["direction"])
            if r["oc"]: used.add(res[2])
        r["lot"] = lots.get(r["name"])
        r["lv"] = levels(r)
        r["flags"] = flags_for(r)
        time.sleep(0.3)

    nifty_up = nf["price"] > nf["ema20"]
    ideas = {k: {"BULLISH": [], "BEARISH": []} for k in ("PRE", "POST")}
    for r in sel:
        ok, _ = btst_gate(r, nf["chg"], nifty_up)
        if not ok: continue
        g, chk, nconf = grade(r, nf["chg"], nf["ret20"], sec_avg.get(r["sector"]))
        if not g: continue
        r.update(grade=g, checks=chk, nconf=nconf)
        ideas[r["setup"]][r["direction"]].append(r)
    for k in ideas:
        for s in ideas[k]:
            ideas[k][s] = sorted(ideas[k][s], key=lambda r: (r["grade"], -r["rank"]))[:SHOW_LIMITS[k]]
    all_ideas = sorted([r for k in ideas for s in ideas[k] for r in ideas[k][s]], key=lambda r: (r["grade"], -r["rank"]))

    stats = evaluate()
    log_signals(all_ideas)
    ctx = dict(today=today, breadth=br, nifty=nf, bank=bf, nc=nc, bc=bc, secs=secs, gl=gl, gtone=gtone, gnote=gnote,
               ideas=ideas, all_ideas=all_ideas, n_scanned=len(tech), n_pre=sum(len(v) for v in ideas["PRE"].values()),
               n_post=sum(len(v) for v in ideas["POST"].values()), stats=stats, uni_note=uni_note, fresh_level=level,
               fresh_txt=fresh_txt, fresh_txt_ok=fresh_txt,
               src_note="Prices: Yahoo Finance | Options: " + (", ".join(sorted(used)) if used else "unavailable"))
    subj = f"{TITLE} | {len(all_ideas)} ideas | {today}" if MODE == "BTST" else f"{TITLE} | {gtone} global cues | {len(all_ideas)} ideas | {today}"
    html = build_html(ctx)          # builds cards, which also fills position sizes
    send_mail(html, subj)
    send_telegram(telegram_text(TITLE, today, nf, br, all_ideas))
    print("sent", [(r["name"], r["setup"], r["direction"], r["grade"]) for r in all_ideas])

if __name__ == "__main__":
    main()
