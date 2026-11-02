import ssl, smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from config import *

SECT_WHEN = "today" if MODE == "BTST" else "yesterday"
BTST_NOTE = ("BTST plan: these ideas use live prices from the afternoon. Buy only if the stock still holds its strength "
             "around 3:00 to 3:20 PM, check US futures and news before holding overnight, keep a stop-loss, "
             "and plan to exit in the first hour next morning. Options lose value overnight, so avoid near-expiry strikes.")

# ---------- HTML ----------
def col(x): return "#0a7d2c" if x >= 0 else "#c0392b"

def build_html(today, gl, gscore, gtone, gnote, secs, nc, bc, top, src_note, nifty_row, bank_row):
    css = ("font-family:Arial,Helvetica,sans-serif;color:#222;")
    h = [f'<html><body style="{css}max-width:760px;margin:auto">']
    h.append(f'<div style="background:#12263f;color:#fff;padding:14px 18px;border-radius:6px">'
             f'<div style="font-size:18px;font-weight:bold">{TITLE}</div>'
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

    h.append(f'<h3 style="margin:18px 0 6px">3. Sector strength ({SECT_WHEN})</h3>')
    h.append('<table cellpadding="5" style="border-collapse:collapse;font-size:12px;width:100%">'
             '<tr style="background:#eee"><th align="left">Sector</th><th>Avg move</th><th>Stocks up</th><th>Read</th></tr>')
    for sec, avg, up, n in secs:
        read = "Strong" if avg > 0.7 and up / n > 0.6 else "Weak" if avg < -0.7 and up / n < 0.4 else "Mixed"
        h.append(f'<tr><td style="border:1px solid #ddd">{sec}</td>'
                 f'<td style="border:1px solid #ddd;color:{col(avg)};text-align:center">{avg:+.2f}%</td>'
                 f'<td style="border:1px solid #ddd;text-align:center">{up}/{n}</td>'
                 f'<td style="border:1px solid #ddd;text-align:center">{read}</td></tr>')
    h.append('</table>')

    bulls = [x for x in top if x["direction"] == "BULLISH"]
    bears = [x for x in top if x["direction"] == "BEARISH"]
    seq = []
    for title, grp in (("4. Bullish ideas: BUY CALL", bulls), ("5. Bearish ideas: BUY PUT", bears)):
        seq.append((title, None, 0, len(grp)))
        seq += [(None, x, k, 0) for k, x in enumerate(grp, 1)]
    for title, r, i, n in seq:
        if title:
            h.append(f'<h3 style="margin:18px 0 6px">{title}</h3>')
            if n == 0:
                h.append('<p style="font-size:13px">No strong setup today.</p>')
            continue
        dc = "#0a7d2c" if r["direction"] == "BULLISH" else "#c0392b"
        cc = {"HIGH": "#0a7d2c", "MEDIUM": "#b9770e", "LOW": "#777"}[r["conf"]]
        h.append(f'<div style="border:1px solid #ccc;border-left:5px solid {dc};padding:10px;margin-bottom:10px;border-radius:4px">')
        h.append(f'<div style="font-size:15px"><b>{i}. {r["name"]}</b> '
                 f'<span style="color:{dc}"><b>{r["action"]}</b></span> '
                 f'<span style="float:right;color:{cc}">Confidence: <b>{r["conf"]}</b> ({r["score"]:.0f})</span></div>')
        h.append(f'<div style="font-size:12px;margin:4px 0">Prev close <b>{r["price"]:.2f}</b> ({r["chg"]:+.2f}%) | '
                 f'RSI {r["rsi"]:.0f} | Volume {r["vr"]:.1f}x | ATR {r["atr_pct"]:.1f}% | '
                 f'52w range {r["lo52"]:.0f} - {r["hi52"]:.0f} | Pressure: {r["pressure"]} | Sector: {r.get("sector","-")}</div>')
        if r.get("lvl_res") and r.get("lvl_sup"):
            h.append(f'<div style="font-size:12px;margin:2px 0">Key levels (swing): Resistance <b>{r["lvl_res"]:.0f}</b> | Support <b>{r["lvl_sup"]:.0f}</b></div>')
        h.append('<div style="font-size:12px"><b>Why:</b><ul style="margin:3px 0 3px 18px;padding:0">'
                 + "".join(f"<li>{x}</li>" for x in r["reasons"]) + '</ul></div>')
        oc = r.get("oc")
        if oc:
            h.append(f'<div style="font-size:12px;background:#f6f8fa;padding:6px;border-radius:4px">'
                     f'<b>Option chain ({oc["src"]}, expiry {oc["expiry"]})</b><br>'
                     f'PCR {oc["pcr"]:.2f} | Support {oc["support"]:.0f} | Resistance {oc["resistance"]:.0f} | '
                     f'Max pain {oc["max_pain"]:.0f} | ATM IV {oc["iv"]:.1f}<br>'
                     f'OI buildup: <b>{oc["buildup"]}</b> ({oc["oi_chg_pct"]:+.1f}% OI) | OI bias: {oc["bias"]}</div>')
            b = oc.get("best")
            if b:
                g = ""
                if b["delta"]:
                    g = (f'<br>Delta {b["delta"]:.2f} | Theta {b["theta"]:.2f}/day '
                         f'({b["decay"]:.1f}% of premium) | Gamma {b["gamma"]:.4f} | Vega {b["vega"]:.2f}')
                h.append(f'<div style="font-size:12px;background:#fff8e6;padding:6px;margin-top:4px;border-radius:4px">'
                         f'<b>Best strike: {b["strike"]:.0f} {b["side"]}</b> | Premium {b["ltp"]:.1f} | '
                         f'OI {b["oi"]:,.0f} ({b["oi_up"]:+.1f}%) | Volume {b["vol"]:,.0f} | IV {b["iv"]:.1f}{g}</div>')
        else:
            h.append('<div style="font-size:12px;color:#888">Option chain unavailable for this stock today.</div>')
        h.append('</div>')

    if MODE == "BTST":
        h.append('<div style="font-size:12px;background:#eef6ff;padding:8px;margin-top:10px;border-radius:4px">' + BTST_NOTE + '</div>')
    h.append('<div style="font-size:11px;color:#777;border-top:1px solid #ddd;padding-top:8px">'
             'Based on previous-day data and the latest option chain snapshot. Ideas for your watchlist only, not advice. '
             'Confirm with live price action after 9:15, avoid chasing gaps, size small, always use a stop-loss. '
             'Index weights in this script are approximate and need updating every March and September.</div>')
    h.append('</body></html>')
    return "".join(h)

def send_mail(html, subject):
    m = MIMEMultipart("alternative")
    m["Subject"], m["From"], m["To"] = subject, EMAIL_USER, EMAIL_TO
    m.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(EMAIL_USER, EMAIL_PASS)
        s.sendmail(EMAIL_USER, EMAIL_TO.split(","), m.as_string())

