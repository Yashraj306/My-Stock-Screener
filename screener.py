"""
Options Screener - Nifty, BankNifty + F&O stocks
Technicals (yfinance) + Option chain (NSE) -> HTML email report
"""
import os
import time
import smtplib
import ssl
import datetime as dt
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import numpy as np
import pandas as pd
import requests
import yfinance as yf

# ---------------- CONFIG ----------------
EMAIL_USER = os.environ["EMAIL_USER"]          # your gmail
EMAIL_PASS = os.environ["EMAIL_PASS"]          # gmail APP password
EMAIL_TO = os.environ.get("EMAIL_TO", EMAIL_USER)

TOP_N_FOR_OPTION_CHAIN = 15   # option chain only for best technical candidates
TOP_N_IN_REPORT = 10

INDICES = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}

# F&O stock list - verify/update periodically (NSE changes it)
STOCKS = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "BHARTIARTL",
    "ITC", "LT", "AXISBANK", "KOTAKBANK", "HINDUNILVR", "BAJFINANCE", "MARUTI",
    "SUNPHARMA", "TATASTEEL", "NTPC", "POWERGRID", "ONGC", "ADANIENT",
    "ADANIPORTS", "ASIANPAINT", "TITAN", "ULTRACEMCO", "WIPRO", "HCLTECH",
    "TECHM", "JSWSTEEL", "HINDALCO", "COALINDIA", "BAJAJFINSV", "M&M",
    "INDUSINDBK", "DRREDDY", "CIPLA", "EICHERMOT", "GRASIM", "HEROMOTOCO",
    "BPCL", "IOC", "VEDL", "DLF", "PNB", "BANKBARODA", "CANBK", "FEDERALBNK",
    "IDFCFIRSTB", "AUBANK", "TATAPOWER", "SAIL", "NMDC", "GAIL", "HAL", "BEL",
    "IRCTC", "TRENT", "LUPIN", "TVSMOTOR", "PFC", "RECLTD", "BHEL", "INDIGO",
    "SIEMENS", "ABB",
]


# ---------------- TECHNICALS ----------------
def rsi(series, n=14):
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def analyze_technicals(d):
    d = d.dropna()
    if len(d) < 60:
        return None
    c, o, h, l, v = d["Close"], d["Open"], d["High"], d["Low"], d["Volume"]

    ema20 = c.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = c.ewm(span=50, adjust=False).mean().iloc[-1]
    r = rsi(c).iloc[-1]
    last = c.iloc[-1]
    chg = (last / c.iloc[-2] - 1) * 100
    avg_vol = v.iloc[-21:-1].mean()
    vol_ratio = v.iloc[-1] / avg_vol if avg_vol > 0 else 1.0
    hi20 = h.iloc[-21:-1].max()
    lo20 = l.iloc[-21:-1].min()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_pct = tr.rolling(14).mean().iloc[-1] / last * 100

    bull, bear, why = 0, 0, []

    if last > ema20 > ema50:
        bull += 2; why.append("Price > EMA20 > EMA50")
    elif last < ema20 < ema50:
        bear += 2; why.append("Price < EMA20 < EMA50")

    if 55 <= r <= 72:
        bull += 1; why.append(f"RSI {r:.0f} strong")
    elif 28 <= r <= 45:
        bear += 1; why.append(f"RSI {r:.0f} weak")

    if vol_ratio >= 1.5:
        if last > o.iloc[-1]:
            bull += 2; why.append(f"Volume {vol_ratio:.1f}x (green candle)")
        else:
            bear += 2; why.append(f"Volume {vol_ratio:.1f}x (red candle)")

    if last > hi20:
        bull += 2; why.append("20-day high breakout")
    elif last < lo20:
        bear += 2; why.append("20-day low breakdown")

    if chg >= 1.5:
        bull += 1; why.append(f"Day change +{chg:.1f}%")
    elif chg <= -1.5:
        bear += 1; why.append(f"Day change {chg:.1f}%")

    diff = bull - bear
    if diff >= 3:
        direction = "BULLISH"
    elif diff <= -3:
        direction = "BEARISH"
    else:
        return None

    return {
        "price": last, "chg": chg, "rsi": r, "vol_ratio": vol_ratio,
        "atr_pct": atr_pct, "direction": direction,
        "tech_score": abs(diff), "why": why,
    }


# ---------------- OPTION CHAIN (NSE) ----------------
def nse_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36"),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/option-chain",
    })
    s.get("https://www.nseindia.com", timeout=15)
    return s


def fetch_chain(s, symbol, is_index):
    kind = "indices" if is_index else "equities"
    url = f"https://www.nseindia.com/api/option-chain-{kind}"
    for _ in range(2):
        try:
            r = s.get(url, params={"symbol": symbol}, timeout=15)
            if r.status_code == 200:
                return r.json()["records"]
        except Exception:
            pass
        time.sleep(1.5)
    return None


def analyze_chain(rec):
    exp = rec["expiryDates"][0]
    spot = rec["underlyingValue"]
    rows = []
    for x in rec["data"]:
        if x.get("expiryDate") != exp:
            continue
        ce, pe = x.get("CE", {}), x.get("PE", {})
        rows.append({
            "strike": x["strikePrice"],
            "ce_oi": ce.get("openInterest", 0), "pe_oi": pe.get("openInterest", 0),
            "ce_chg": ce.get("changeinOpenInterest", 0),
            "pe_chg": pe.get("changeinOpenInterest", 0),
            "ce_iv": ce.get("impliedVolatility", 0), "pe_iv": pe.get("impliedVolatility", 0),
        })
    df = pd.DataFrame(rows)
    if df.empty or df["ce_oi"].sum() == 0:
        return None

    pcr = df["pe_oi"].sum() / df["ce_oi"].sum()
    resistance = df.loc[df["ce_oi"].idxmax(), "strike"]
    support = df.loc[df["pe_oi"].idxmax(), "strike"]
    ce_chg, pe_chg = df["ce_chg"].sum(), df["pe_chg"].sum()

    atm_row = df.iloc[(df["strike"] - spot).abs().argsort().iloc[0]]
    atm = atm_row["strike"]
    iv = np.mean([x for x in (atm_row["ce_iv"], atm_row["pe_iv"]) if x > 0] or [0])

    strikes = df["strike"].values
    pain = [
        (df["ce_oi"].values * np.maximum(k - strikes, 0)).sum()
        + (df["pe_oi"].values * np.maximum(strikes - k, 0)).sum()
        for k in strikes
    ]
    max_pain = strikes[int(np.argmin(pain))]

    if pcr >= 1.1 and pe_chg > ce_chg:
        bias = "BULLISH"
    elif pcr <= 0.9 and ce_chg > pe_chg:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    return {
        "expiry": exp, "spot": spot, "pcr": pcr, "support": support,
        "resistance": resistance, "max_pain": max_pain, "atm": atm,
        "iv": iv, "ce_chg": ce_chg, "pe_chg": pe_chg, "oc_bias": bias,
    }


# ---------------- REPORT ----------------
def build_html(results, today):
    rows = ""
    for r in results:
        color = "#0a7d2c" if r["direction"] == "BULLISH" else "#c0392b"
        oc = r.get("oc")
        if oc:
            side = "CE" if r["direction"] == "BULLISH" else "PE"
            confirm = ("✅ Confirmed" if oc["oc_bias"] == r["direction"]
                       else "⚠️ Mixed" if oc["oc_bias"] == "NEUTRAL" else "❌ Opposite")
            oc_txt = (f"PCR {oc['pcr']:.2f} | Sup {oc['support']:.0f} | Res {oc['resistance']:.0f}<br>"
                      f"MaxPain {oc['max_pain']:.0f} | IV {oc['iv']:.1f}<br>"
                      f"OI bias: {oc['oc_bias']} {confirm}<br>"
                      f"<b>Idea:</b> {oc['atm']:.0f} {side} ({oc['expiry']})")
        else:
            oc_txt = "Option chain unavailable"
        rows += (
            f"<tr><td><b>{r['name']}</b></td>"
            f"<td style='color:{color}'><b>{r['direction']}</b></td>"
            f"<td>{r['price']:.2f}<br>({r['chg']:+.1f}%)</td>"
            f"<td>{r['final']:.0f}</td>"
            f"<td>RSI {r['rsi']:.0f}<br>Vol {r['vol_ratio']:.1f}x<br>ATR {r['atr_pct']:.1f}%</td>"
            f"<td style='font-size:12px'>{oc_txt}</td>"
            f"<td style='font-size:12px'>{'; '.join(r['why'])}</td></tr>"
        )
    return f"""
    <html><body style="font-family:Arial,sans-serif">
    <h3>📊 Pre-Market Options Screener — {today}</h3>
    <table border="1" cellpadding="6" cellspacing="0"
           style="border-collapse:collapse;font-size:13px">
      <tr style="background:#eee"><th>Symbol</th><th>Bias</th><th>Prev Close</th>
      <th>Score</th><th>Tech</th><th>Option Chain</th><th>Reasons</th></tr>
      {rows}
    </table>
    <p style="font-size:11px;color:#777">
    Based on previous-day data. Ideas for your watchlist only — not advice.
    Confirm with live price action after open, and use a stop-loss.</p>
    </body></html>"""


def send_mail(html, subject):
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, EMAIL_USER, EMAIL_TO
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as srv:
        srv.login(EMAIL_USER, EMAIL_PASS)
        srv.sendmail(EMAIL_USER, EMAIL_TO.split(","), msg.as_string())


# ---------------- MAIN ----------------
def main():
    symbols = {**INDICES, **{s: f"{s}.NS" for s in STOCKS}}
    tickers = list(symbols.values())
    data = yf.download(tickers, period="6mo", interval="1d", group_by="ticker",
                       auto_adjust=True, progress=False, threads=True)
    available = set(data.columns.get_level_values(0))

    cands = []
    for name, tk in symbols.items():
        if tk not in available:
            continue
        res = analyze_technicals(data[tk])
        if res:
            res["name"] = name
            res["is_index"] = name in INDICES
            cands.append(res)

    cands.sort(key=lambda x: x["tech_score"], reverse=True)
    shortlist = cands[:TOP_N_FOR_OPTION_CHAIN]
    for idx in cands:                       # always include indices if they qualified
        if idx["is_index"] and idx not in shortlist:
            shortlist.append(idx)

    try:
        sess = nse_session()
    except Exception:
        sess = None

    for r in shortlist:
        r["oc"] = None
        r["final"] = r["tech_score"] * 10
        if sess:
            rec = fetch_chain(sess, r["name"], r["is_index"])
            if rec:
                oc = analyze_chain(rec)
                if oc:
                    r["oc"] = oc
                    if oc["oc_bias"] == r["direction"]:
                        r["final"] += 30
                    elif oc["oc_bias"] != "NEUTRAL":
                        r["final"] -= 20
            time.sleep(0.7)

    shortlist.sort(key=lambda x: x["final"], reverse=True)
    top = shortlist[:TOP_N_IN_REPORT]

    today = dt.datetime.now().strftime("%d %b %Y")
    if not top:
        html = "<p>आज कोणताही स्ट्रॉंग सेटअप सापडला नाही.</p>"
    else:
        html = build_html(top, today)
    send_mail(html, f"Pre-Market Options Screener — {today}")
    print("Report sent:", [t["name"] for t in top])


if __name__ == "__main__":
    main()
      
