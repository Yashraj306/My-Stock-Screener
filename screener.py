
"""
Pre-Market Options Screener v2
- Technicals + 52w high/low + buying/selling pressure (yfinance)
- Global cues (Nasdaq, Dow, S&P, Nikkei, Hang Seng, Crude, Dollar, US 10Y, GIFT proxy)
- Sector strength + Nifty / BankNifty index contribution
- Option chain via Upstox (Analytics Token), fallback NSE
- OI buildup, best strike, Greeks
Output: HTML email
"""
import os, io, gzip, json, time, ssl, smtplib, datetime as dt
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import numpy as np
import pandas as pd
import requests
import yfinance as yf

EMAIL_USER = os.environ["EMAIL_USER"]
EMAIL_PASS = os.environ["EMAIL_PASS"]
EMAIL_TO = os.environ.get("EMAIL_TO", EMAIL_USER)
UPSTOX_TOKEN = os.environ.get("UPSTOX_TOKEN", "")

TOP_N_CHAIN = 15
TOP_N_REPORT = 10

# ---------- UNIVERSE: symbol -> sector ----------
SECTORS = {
    "BANK": ["HDFCBANK","ICICIBANK","SBIN","AXISBANK","KOTAKBANK","INDUSINDBK","BANKBARODA","PNB","CANBK","FEDERALBNK","IDFCFIRSTB","AUBANK"],
    "IT": ["TCS","INFY","WIPRO","HCLTECH","TECHM"],
    "AUTO": ["MARUTI","M&M","EICHERMOT","HEROMOTOCO","TVSMOTOR"],
    "METAL": ["TATASTEEL","JSWSTEEL","HINDALCO","VEDL","SAIL","NMDC","COALINDIA"],
    "PHARMA": ["SUNPHARMA","DRREDDY","CIPLA","LUPIN"],
    "ENERGY": ["RELIANCE","ONGC","BPCL","IOC","NTPC","POWERGRID","TATAPOWER","GAIL"],
    "FINANCE": ["BAJFINANCE","BAJAJFINSV","PFC","RECLTD"],
    "FMCG": ["ITC","HINDUNILVR"],
    "INFRA": ["LT","ADANIENT","ADANIPORTS","ULTRACEMCO","GRASIM","DLF","HAL","BEL","BHEL","SIEMENS","ABB"],
    "CONSUMER": ["TITAN","ASIANPAINT","TRENT","IRCTC","INDIGO"],
    "TELECOM": ["BHARTIARTL"],
}
SECTOR_OF = {s: k for k, v in SECTORS.items() for s in v}
STOCKS = list(SECTOR_OF.keys())
INDICES = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}

# Approximate index weights (%). UPDATE every March/September from niftyindices.com
NIFTY_W = {"HDFCBANK":13.0,"RELIANCE":8.5,"ICICIBANK":8.5,"INFY":5.0,"BHARTIARTL":5.0,"TCS":3.5,"ITC":3.5,"LT":3.5,
           "AXISBANK":3.0,"SBIN":3.0,"KOTAKBANK":2.7,"M&M":2.5,"BAJFINANCE":2.3,"HCLTECH":1.8,"SUNPHARMA":1.7,
           "MARUTI":1.7,"NTPC":1.5,"TITAN":1.5,"ULTRACEMCO":1.3,"TATASTEEL":1.2,"POWERGRID":1.2,"ASIANPAINT":1.0,
           "HINDUNILVR":2.0,"BAJAJFINSV":1.1,"ADANIPORTS":1.1,"JSWSTEEL":1.0,"ONGC":0.9,"COALINDIA":0.8,"WIPRO":0.7,
           "TECHM":0.7,"INDUSINDBK":0.6,"HINDALCO":0.9,"GRASIM":0.9,"CIPLA":0.6,"DRREDDY":0.6,"EICHERMOT":0.7,
           "HEROMOTOCO":0.5,"BPCL":0.5,"ADANIENT":0.8,"TRENT":0.8,"INDIGO":0.8}
BANKNIFTY_W = {"HDFCBANK":29.0,"ICICIBANK":24.0,"SBIN":9.5,"AXISBANK":8.5,"KOTAKBANK":7.5,"INDUSINDBK":3.0,
               "BANKBARODA":3.0,"PNB":2.5,"CANBK":2.5,"FEDERALBNK":2.5,"IDFCFIRSTB":2.0,"AUBANK":2.0}

GLOBAL = {
    "Nasdaq": ("^IXIC", 1.0), "Dow Jones": ("^DJI", 1.0), "S&P 500": ("^GSPC", 1.0),
    "Nikkei": ("^N225", 0.6), "Hang Seng": ("^HSI", 0.6),
    "Crude Oil": ("CL=F", -0.3), "US Dollar Idx": ("DX-Y.NYB", -0.4),
    "US 10Y Yield": ("^TNX", -0.3), "India VIX": ("^INDIAVIX", -0.5),
}

# ---------- helpers ----------
def rsi(s, n=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))

def pct(a, b):
    return (a / b - 1) * 100 if b else 0.0

# ---------- GLOBAL CUES ----------
def global_cues():
    out, score = [], 0.0
    for name, (tk, w) in GLOBAL.items():
        try:
            h = yf.Ticker(tk).history(period="5d")["Close"].dropna()
            if len(h) < 2:
                continue
            ch = pct(h.iloc[-1], h.iloc[-2])
            out.append((name, h.iloc[-1], ch))
            cap = max(-2.5, min(2.5, ch))
            score += cap * w if name != "India VIX" else cap * w * 0.3
        except Exception:
            continue
    if score >= 1.5:   tone, note = "POSITIVE", "Gap-up / firm open likely"
    elif score <= -1.5: tone, note = "NEGATIVE", "Gap-down / weak open likely"
    else:              tone, note = "MIXED", "Flat to mildly directional open"
    return out, score, tone, note

# ---------- TECHNICALS ----------
def analyze(d):
    d = d.dropna()
    if len(d) < 60:
        return None
    c, o, h, l, v = d["Close"], d["Open"], d["High"], d["Low"], d["Volume"]
    last = c.iloc[-1]
    ema20 = c.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = c.ewm(span=50, adjust=False).mean().iloc[-1]
    r = rsi(c).iloc[-1]
    chg = pct(last, c.iloc[-2])
    avgv = v.iloc[-21:-1].mean()
    vr = v.iloc[-1] / avgv if avgv > 0 else 1.0
    hi20, lo20 = h.iloc[-21:-1].max(), l.iloc[-21:-1].min()
    hi52, lo52 = h.iloc[-252:].max(), l.iloc[-252:].min()
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    rng = h.iloc[-1] - l.iloc[-1]
    close_pos = (last - l.iloc[-1]) / rng if rng > 0 else 0.5   # 1 = closed at high

    bull = bear = 0
    why = []
    if last > ema20 > ema50: bull += 2; why.append("Trend up: price above EMA20 and EMA50")
    elif last < ema20 < ema50: bear += 2; why.append("Trend down: price below EMA20 and EMA50")
    if 55 <= r <= 72: bull += 1; why.append(f"RSI {r:.0f} (strong momentum)")
    elif 28 <= r <= 45: bear += 1; why.append(f"RSI {r:.0f} (weak momentum)")
    if vr >= 1.5:
        if last > o.iloc[-1]: bull += 2; why.append(f"Volume {vr:.1f}x average on green candle (buying)")
        else: bear += 2; why.append(f"Volume {vr:.1f}x average on red candle (selling)")
    if last > hi20: bull += 2; why.append("Broke 20-day high")
    elif last < lo20: bear += 2; why.append("Broke 20-day low")
    if last >= hi52 * 0.98:
        bull += 1; why.append("At/near 52-week high" + (" (fresh high)" if last >= hi52 else ""))
    if last <= lo52 * 1.02:
        bear += 1; why.append("At/near 52-week low" + (" (fresh low)" if last <= lo52 else ""))
    if close_pos >= 0.8 and vr >= 1.2: bull += 1; why.append("Closed near day high with volume (buying pressure)")
    if close_pos <= 0.2 and vr >= 1.2: bear += 1; why.append("Closed near day low with volume (selling pressure)")
    if chg >= 1.5: bull += 1
    elif chg <= -1.5: bear += 1

    if close_pos >= 0.7 and vr >= 1.0: pressure = "BUYING"
    elif close_pos <= 0.3 and vr >= 1.0: pressure = "SELLING"
    else: pressure = "BALANCED"

    return dict(price=last, chg=chg, rsi=r, vr=vr, atr=atr, atr_pct=atr/last*100,
                hi52=hi52, lo52=lo52, pressure=pressure, bull=bull, bear=bear,
                diff=bull-bear, why=why)

def direction_of(t):
    if t["diff"] >= 3: return "BULLISH"
    if t["diff"] <= -3: return "BEARISH"
    return None

# ---------- SECTORS + INDEX CONTRIBUTION ----------
def sector_table(tech):
    rows = []
    for sec, syms in SECTORS.items():
        ch = [tech[s]["chg"] for s in syms if s in tech]
        if not ch: continue
        up = sum(1 for x in ch if x > 0)
        rows.append((sec, float(np.mean(ch)), up, len(ch)))
    rows.sort(key=lambda x: x[1], reverse=True)
    return rows

def contribution(tech, weights, index_level):
    items = []
    for s, w in weights.items():
        if s in tech:
            pts = tech[s]["chg"] / 100 * w / 100 * index_level
            items.append((s, tech[s]["chg"], w, pts))
    items.sort(key=lambda x: x[3], reverse=True)
    return items

# ---------- UPSTOX ----------
_instr = None
def load_instruments():
    global _instr
    if _instr is not None: return _instr
    try:
        r = requests.get("https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz", timeout=60)
        data = json.loads(gzip.decompress(r.content))
        m = {}
        for x in data:
            if x.get("segment") == "NSE_EQ" and x.get("instrument_type") == "EQ":
                m[x.get("trading_symbol")] = x.get("instrument_key")
        _instr = m
    except Exception as e:
        print("instrument load failed:", e); _instr = {}
    return _instr

def underlying_key(name):
    if name == "NIFTY": return "NSE_INDEX|Nifty 50"
    if name == "BANKNIFTY": return "NSE_INDEX|Nifty Bank"
    return load_instruments().get(name)

def upstox_get(path, params):
    h = {"Accept": "application/json", "Authorization": f"Bearer {UPSTOX_TOKEN}"}
    for _ in range(2):
        try:
            r = requests.get("https://api.upstox.com" + path, params=params, headers=h, timeout=20)
            if r.status_code == 200: return r.json()
            print("upstox", path, r.status_code, r.text[:150])
        except Exception as e:
            print("upstox err", e)
        time.sleep(1)
    return None

def nearest_expiry(key):
    j = upstox_get("/v2/option/contract", {"instrument_key": key})
    if not j or not j.get("data"): return None
    today = dt.date.today().isoformat()
    ex = sorted({x["expiry"][:10] for x in j["data"] if x.get("expiry")})
    ex = [e for e in ex if e >= today]
    return ex[0] if ex else None

def chain_upstox(name):
    if not UPSTOX_TOKEN: return None
    key = underlying_key(name)
    if not key: return None
    exp = nearest_expiry(key)
    if not exp: return None
    j = upstox_get("/v2/option/chain", {"instrument_key": key, "expiry_date": exp})
    if not j or not j.get("data"): return None
    rows = []
    for x in j["data"]:
        ce, pe = x.get("call_options", {}), x.get("put_options", {})
        cm, pm = ce.get("market_data", {}), pe.get("market_data", {})
        cg, pg = ce.get("option_greeks", {}), pe.get("option_greeks", {})
        rows.append(dict(strike=x["strike_price"], spot=x.get("underlying_spot_price"),
            ce_ltp=cm.get("ltp",0), pe_ltp=pm.get("ltp",0),
            ce_oi=cm.get("oi",0), pe_oi=pm.get("oi",0),
            ce_poi=cm.get("prev_oi",0), pe_poi=pm.get("prev_oi",0),
            ce_vol=cm.get("volume",0), pe_vol=pm.get("volume",0),
            ce_iv=cg.get("iv",0), pe_iv=pg.get("iv",0),
            ce_delta=cg.get("delta",0), pe_delta=pg.get("delta",0),
            ce_theta=cg.get("theta",0), pe_theta=pg.get("theta",0),
            ce_gamma=cg.get("gamma",0), pe_gamma=pg.get("gamma",0),
            ce_vega=cg.get("vega",0), pe_vega=pg.get("vega",0)))
    df = pd.DataFrame(rows)
    return df, exp, "Upstox"

# ---------- NSE FALLBACK (no greeks / ltp-level detail) ----------
def chain_nse(name, sess):
    try:
        kind = "indices" if name in INDICES else "equities"
        r = sess.get(f"https://www.nseindia.com/api/option-chain-{kind}", params={"symbol": name}, timeout=15)
        if r.status_code != 200: return None
        rec = r.json()["records"]
        exp = rec["expiryDates"][0]
        spot = rec["underlyingValue"]
        rows = []
        for x in rec["data"]:
            if x.get("expiryDate") != exp: continue
            ce, pe = x.get("CE", {}), x.get("PE", {})
            rows.append(dict(strike=x["strikePrice"], spot=spot,
                ce_ltp=ce.get("lastPrice",0), pe_ltp=pe.get("lastPrice",0),
                ce_oi=ce.get("openInterest",0), pe_oi=pe.get("openInterest",0),
                ce_poi=ce.get("openInterest",0)-ce.get("changeinOpenInterest",0),
                pe_poi=pe.get("openInterest",0)-pe.get("changeinOpenInterest",0),
                ce_vol=ce.get("totalTradedVolume",0), pe_vol=pe.get("totalTradedVolume",0),
                ce_iv=ce.get("impliedVolatility",0), pe_iv=pe.get("impliedVolatility",0),
                ce_delta=0, pe_delta=0, ce_theta=0, pe_theta=0, ce_gamma=0, pe_gamma=0, ce_vega=0, pe_vega=0))
        return pd.DataFrame(rows), exp, "NSE"
    except Exception:
        return None

# ---------- CHAIN ANALYSIS ----------
def buildup(price_chg, oi_chg_pct):
    if abs(oi_chg_pct) < 1: return "Neutral"
    if price_chg >= 0 and oi_chg_pct > 0: return "Long Buildup"
    if price_chg < 0 and oi_chg_pct > 0: return "Short Buildup"
    if price_chg >= 0 and oi_chg_pct < 0: return "Short Covering"
    return "Long Unwinding"

def analyze_chain(df, exp, src, price_chg, direction):
    df = df.copy()
    spot = float(df["spot"].dropna().iloc[0]) if df["spot"].notna().any() else None
    if spot is None or df["ce_oi"].sum() == 0: return None
    pcr = df["pe_oi"].sum() / max(df["ce_oi"].sum(), 1)
    res = df.loc[df["ce_oi"].idxmax(), "strike"]
    sup = df.loc[df["pe_oi"].idxmax(), "strike"]
    ce_chg = (df["ce_oi"] - df["ce_poi"]).sum()
    pe_chg = (df["pe_oi"] - df["pe_poi"]).sum()
    tot_oi = df["ce_oi"].sum() + df["pe_oi"].sum()
    tot_poi = df["ce_poi"].sum() + df["pe_poi"].sum()
    oi_chg_pct = (tot_oi / tot_poi - 1) * 100 if tot_poi else 0
    bu = buildup(price_chg, oi_chg_pct)
    k = df["strike"].values
    pain = [(df["ce_oi"].values * np.maximum(kk - k, 0)).sum() + (df["pe_oi"].values * np.maximum(k - kk, 0)).sum() for kk in k]
    max_pain = k[int(np.argmin(pain))]
    atm_i = int(np.argmin(np.abs(k - spot)))
    atm = k[atm_i]
    iv = np.mean([x for x in (df.iloc[atm_i]["ce_iv"], df.iloc[atm_i]["pe_iv"]) if x > 0] or [0])
    if pcr >= 1.1 and pe_chg > ce_chg: bias = "BULLISH"
    elif pcr <= 0.9 and ce_chg > pe_chg: bias = "BEARISH"
    else: bias = "NEUTRAL"

    # ---- best strike: score ATM-1 .. ATM+2 on the side of the trade ----
    side = "CE" if direction == "BULLISH" else "PE"
    best = None
    cands = df.iloc[max(0, atm_i - 1): atm_i + 3].copy()
    scored = []
    for _, row in cands.iterrows():
        p = "ce" if side == "CE" else "pe"
        ltp, oi, poi, vol = row[f"{p}_ltp"], row[f"{p}_oi"], row[f"{p}_poi"], row[f"{p}_vol"]
        if ltp <= 0 or oi <= 0: continue
        oi_up = (oi / poi - 1) * 100 if poi else 0
        delta = abs(row[f"{p}_delta"])
        theta = abs(row[f"{p}_theta"])
        liq = np.log1p(vol) + np.log1p(oi)
        decay = theta / ltp * 100 if ltp else 0          # % of premium lost per day
        sc = liq * 1.0 + (3 if oi_up > 5 else 0)
        if delta: sc += 4 - abs(delta - 0.5) * 8          # prefer delta near 0.5
        sc -= decay * 0.5
        scored.append((sc, row, oi_up, delta, theta, decay))
    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        sc, row, oi_up, delta, theta, decay = scored[0]
        p = "ce" if side == "CE" else "pe"
        best = dict(strike=row["strike"], side=side, ltp=row[f"{p}_ltp"], oi=row[f"{p}_oi"],
                    oi_up=oi_up, vol=row[f"{p}_vol"], iv=row[f"{p}_iv"], delta=row[f"{p}_delta"],
                    theta=row[f"{p}_theta"], gamma=row[f"{p}_gamma"], vega=row[f"{p}_vega"], decay=decay)
    return dict(src=src, expiry=exp, spot=spot, pcr=pcr, support=sup, resistance=res, max_pain=max_pain,
                atm=atm, iv=iv, bias=bias, buildup=bu, oi_chg_pct=oi_chg_pct, best=best)

# ---------- SIGNAL ----------
def make_signal(r, gtone):
    d = r["direction"]
    score = abs(r["diff"]) * 10
    oc = r.get("oc")
    reasons = list(r["why"])
    if oc:
        if oc["bias"] == d: score += 25; reasons.append(f"Option chain agrees: PCR {oc['pcr']:.2f}, {oc['buildup']}")
        elif oc["bias"] == "NEUTRAL": reasons.append(f"Option chain neutral: PCR {oc['pcr']:.2f}, {oc['buildup']}")
        else: score -= 20; reasons.append(f"Option chain disagrees: PCR {oc['pcr']:.2f}")
        if d == "BULLISH" and oc["buildup"] in ("Long Buildup", "Short Covering"): score += 10
        if d == "BEARISH" and oc["buildup"] in ("Short Buildup", "Long Unwinding"): score += 10
        px = r["price"]
        if d == "BULLISH" and oc["resistance"] and oc["resistance"] <= px * 1.01:
            score -= 10; reasons.append(f"Heavy call OI wall at {oc['resistance']:.0f} just above price")
        if d == "BEARISH" and oc["support"] and oc["support"] >= px * 0.99:
            score -= 10; reasons.append(f"Heavy put OI floor at {oc['support']:.0f} just below price")
    if gtone == "POSITIVE" and d == "BULLISH": score += 8
    if gtone == "NEGATIVE" and d == "BEARISH": score += 8
    if gtone == "POSITIVE" and d == "BEARISH": score -= 8
    if gtone == "NEGATIVE" and d == "BULLISH": score -= 8
    action = "BUY CALL (CE)" if d == "BULLISH" else "BUY PUT (PE)"
    conf = "HIGH" if score >= 90 else "MEDIUM" if score >= 60 else "LOW"
    r["score"], r["reasons"], r["action"], r["conf"] = score, reasons, action, conf

# ---------- HTML ----------
def col(x): return "#0a7d2c" if x >= 0 else "#c0392b"

def build_html(today, gl, gscore, gtone, gnote, secs, nc, bc, top, src_note, nifty_row, bank_row):
    css = ("font-family:Arial,Helvetica,sans-serif;color:#222;")
    h = [f'<html><body style="{css}max-width:760px;margin:auto">']
    h.append(f'<div style="background:#12263f;color:#fff;padding:14px 18px;border-radius:6px">'
             f'<div style="font-size:18px;font-weight:bold">Pre-Market Options Screener</div>'
             f'<div style="font-size:12px;opacity:.8">{today} | Data source: {src_note}</div></div>')

    tcol = {"POSITIVE": "#0a7d2c", "NEGATIVE": "#c0392b", "MIXED": "#b9770e"}[gtone]
    h.append(f'<h3 style="margin:18px 0 6px">1. Global cues: <span style="color:{tcol}">{gtone}</span></h3>')
    h.append(f'<div style="font-size:13px;margin-bottom:6px">{gnote}. Score {gscore:+.1f}</div>')
    h.append('<table cellpadding="5" style="border-collapse:collapse;font-size:12px;width:100%">')
    cells = "".join(f'<td style="border:1px solid #ddd;text-align:center"><b>{n}</b><br>{v:,.1f}<br>'
                    f'<span style="color:{col(c)}">{c:+.2f}%</span></td>' for n, v, c in gl)
    h.append(f'<tr>{cells}</tr></table>')
    h.append('<div style="font-size:11px;color:#777;margin-top:4px">US markets close after India does, so '
             'their move shows how India may open. Crude, dollar and US yields move opposite to Indian equities.</div>')

    h.append('<h3 style="margin:18px 0 6px">2. Index view</h3>')
    for nm, row, contrib in (("NIFTY", nifty_row, nc), ("BANKNIFTY", bank_row, bc)):
        if not row: continue
        up = contrib[:3]; dn = contrib[-3:][::-1]
        ups = ", ".join(f"{s} ({p:+.0f} pts)" for s, c, w, p in up)
        dns = ", ".join(f"{s} ({p:+.0f} pts)" for s, c, w, p in dn)
        h.append(f'<div style="border:1px solid #ddd;padding:8px;margin-bottom:6px;font-size:13px">'
                 f'<b>{nm}</b> {row["price"]:.0f} ({row["chg"]:+.2f}%) | RSI {row["rsi"]:.0f} | '
                 f'Pressure: {row["pressure"]}<br>'
                 f'<span style="color:#0a7d2c">Biggest support: {ups}</span><br>'
                 f'<span style="color:#c0392b">Biggest drag: {dns}</span></div>')

    h.append('<h3 style="margin:18px 0 6px">3. Sector strength (yesterday)</h3>')
    h.append('<table cellpadding="5" style="border-collapse:collapse;font-size:12px;width:100%">'
             '<tr style="background:#eee"><th align="left">Sector</th><th>Avg move</th><th>Stocks up</th><th>Read</th></tr>')
    for sec, avg, up, n in secs:
        read = "Strong" if avg > 0.7 and up / n > 0.6 else "Weak" if avg < -0.7 and up / n < 0.4 else "Mixed"
        h.append(f'<tr><td style="border:1px solid #ddd">{sec}</td>'
                 f'<td style="border:1px solid #ddd;color:{col(avg)};text-align:center">{avg:+.2f}%</td>'
                 f'<td style="border:1px solid #ddd;text-align:center">{up}/{n}</td>'
                 f'<td style="border:1px solid #ddd;text-align:center">{read}</td></tr>')
    h.append('</table>')

    h.append('<h3 style="margin:18px 0 6px">4. Trade ideas</h3>')
    if not top:
        h.append('<p>No strong setups today.</p>')
    for i, r in enumerate(top, 1):
        dc = "#0a7d2c" if r["direction"] == "BULLISH" else "#c0392b"
        cc = {"HIGH": "#0a7d2c", "MEDIUM": "#b9770e", "LOW": "#777"}[r["conf"]]
        h.append(f'<div style="border:1px solid #ccc;border-left:5px solid {dc};padding:10px;margin-bottom:10px;border-radius:4px">')
        h.append(f'<div style="font-size:15px"><b>{i}. {r["name"]}</b> '
                 f'<span style="color:{dc}"><b>{r["action"]}</b></span> '
                 f'<span style="float:right;color:{cc}">Confidence: <b>{r["conf"]}</b> ({r["score"]:.0f})</span></div>')
        h.append(f
