from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from config import *
from cards import card, track_html, n, FONT

def col(x): return "#0a7d2c" if x >= 0 else "#c0392b"

def h2(t, sub=""):
    s = f'<div style="font-size:12px;color:#6b7280;margin-top:2px">{sub}</div>' if sub else ""
    return (f'<div style="{FONT}margin:22px 0 10px;padding-bottom:6px;border-bottom:2px solid #12263f">'
            f'<span style="font-size:16px;font-weight:bold;color:#12263f">{t}</span>{s}</div>')

def build_html(c):
    sect_when = "today" if MODE == "BTST" else "yesterday"
    h = [f'<html><body style="{FONT}color:#111;background:#f3f4f6;margin:0;padding:12px"><div style="max-width:680px;margin:auto;background:#fff;padding:16px;border-radius:8px">']
    h.append(f'<div style="background:#12263f;color:#fff;padding:16px 18px;border-radius:6px">'
             f'<div style="font-size:20px;font-weight:bold">{TITLE}</div>'
             f'<div style="font-size:12px;opacity:.85;margin-top:3px">{c["today"]}</div></div>')
    # summary
    nf = c["nifty"]
    br = c["breadth"]
    tcol = {"POSITIVE": "#0a7d2c", "NEGATIVE": "#c0392b", "MIXED": "#b45309"}[c["gtone"]]
    trend = "above" if nf["price"] > nf["ema20"] else "below"
    top = [r for r in c["all_ideas"] if r["grade"] == "A"][:3]
    top_txt = ", ".join(f'{r["name"]} ({"CE" if r["direction"] == "BULLISH" else "PE"})' for r in top) or "none rated A today"
    warn = f'<div style="margin-top:6px;color:#b45309">&#9888; {c["fresh_txt"]}</div>' if c["fresh_level"] != "ok" else ""
    h.append(f'<div style="{FONT}background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;padding:12px;margin-top:12px;font-size:13px;line-height:1.6">'
             f'<b>Summary</b><br>'
             f'Nifty {n(nf["price"])} ({nf["chg"]:+.2f}%), {trend} its 20-day average, RSI {nf["rsi"]:.0f}<br>'
             f'Breadth: {br["adv"]} up / {br["dec"]} down | {br["a20"]:.0f}% above EMA20 | {br["a50"]:.0f}% above EMA50 | <b>{br["tone"]}</b><br>'
             + (f'Global tone: <b style="color:{tcol}">{c["gtone"]}</b> ({c["gnote"]})<br>' if MODE != "BTST" else '')
             + f'Scanned {c["n_scanned"]} stocks and indices | Ideas: {len(c["all_ideas"])} '
               f'(pre-rally {c["n_pre"]}, continuation {c["n_post"]})<br>'
               f'Best rated (Grade A): {top_txt}<br>'
               f'<span style="font-size:11px;color:#6b7280">{c["fresh_txt_ok"]} | {c["src_note"]} | {c["uni_note"]}</span>{warn}</div>')
    # market context
    if MODE != "BTST":
        h.append(h2("Global cues", "US markets close after India, so their move hints at today's open"))
        cells = "".join(f'<td style="border:1px solid #e5e7eb;text-align:center;padding:6px;font-size:11px"><b>{a}</b><br>{b:,.1f}<br>'
                        f'<span style="color:{col(d)}">{d:+.2f}%</span></td>' for a, b, d in c["gl"])
        h.append(f'<div style="overflow-x:auto"><table cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:100%"><tr>{cells}</tr></table></div>')
    h.append(h2("Index view"))
    for nm, row, ct in (("NIFTY", c["nifty"], c["nc"]), ("BANKNIFTY", c["bank"], c["bc"])):
        if not row: continue
        up = ", ".join(f"{s} ({p:+.0f})" for s, _, _, p in ct[:3])
        dn = ", ".join(f"{s} ({p:+.0f})" for s, _, _, p in ct[-3:][::-1])
        h.append(f'<div style="font-size:13px;border:1px solid #e5e7eb;border-radius:6px;padding:10px;margin-bottom:8px;line-height:1.6">'
                 f'<b>{nm}</b> {n(row["price"])} ({row["chg"]:+.2f}%) | RSI {row["rsi"]:.0f}<br>'
                 f'<span style="color:#0a7d2c">Lifting: {up}</span><br><span style="color:#c0392b">Dragging: {dn}</span> '
                 f'<span style="font-size:11px;color:#6b7280">(index points)</span></div>')
    h.append(h2(f"Sector strength ({sect_when})"))
    rows = ""
    for sec, avg, up, tot in c["secs"][:12]:
        read = "Strong" if avg > 0.7 and up / tot > 0.6 else "Weak" if avg < -0.7 and up / tot < 0.4 else "Mixed"
        rows += (f'<tr><td style="padding:5px 8px;border:1px solid #e5e7eb">{sec}</td>'
                 f'<td align="center" style="border:1px solid #e5e7eb;color:{col(avg)}">{avg:+.2f}%</td>'
                 f'<td align="center" style="border:1px solid #e5e7eb">{up}/{tot}</td>'
                 f'<td align="center" style="border:1px solid #e5e7eb">{read}</td></tr>')
    h.append(f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-size:12px">'
             f'<tr style="background:#f3f4f6"><th align="left" style="padding:5px 8px">Sector</th><th>Avg move</th><th>Up</th><th>Read</th></tr>{rows}</table>')
    # ideas
    blocks = (("PRE", "BULLISH", "Pre-rally setups: bullish (BUY CALL)", "Coiling under resistance. Best entries come on the break."),
              ("PRE", "BEARISH", "Pre-rally setups: bearish (BUY PUT)", "Pressing on support. Best entries come on the break."),
              ("POST", "BULLISH", "Continuation: bullish (BUY CALL)", "Move has started. Prefer pullbacks, avoid chasing."),
              ("POST", "BEARISH", "Continuation: bearish (BUY PUT)", "Move has started. Prefer bounces that fail, avoid chasing."))
    for kind, side, title, sub in blocks:
        items = c["ideas"][kind][side]
        h.append(h2(title, sub))
        h.append("".join(card(r) for r in items) if items else
                 '<div style="font-size:13px;color:#6b7280">Nothing passed the quality filters today. Fewer, better ideas is intended.</div>')
    h.append(h2("Track record", "How past ideas actually performed"))
    h.append(track_html(c["stats"]))
    if MODE == "BTST":
        h.append('<div style="font-size:12px;background:#eef6ff;padding:10px;margin-top:14px;border-radius:4px;line-height:1.5">'
                 '<b>BTST plan:</b> buy only if the stock still holds strength around 3:00 to 3:20 PM. Check US futures and news before holding overnight, '
                 'keep a stop-loss, and plan to exit in the first hour next morning. Options lose value overnight.</div>')
    h.append('<div style="font-size:11px;color:#6b7280;border-top:1px solid #e5e7eb;margin-top:16px;padding-top:8px;line-height:1.5">'
             'Grades count independent confirmations (trend, level, volume, options, sector, relative strength). They are not probabilities. '
             'Ideas only, not advice. Confirm with live price action, size small, always use a stop-loss. '
             'Index weights are approximate and need updating every March and September.</div>')
    h.append('</div></body></html>')
    return "".join(h)

def build_stale_html(txt):
    return (f'<html><body style="{FONT}"><div style="max-width:560px;margin:auto;padding:16px">'
            f'<h3>{TITLE}: no ideas generated</h3><p>{txt}</p>'
            '<p>Reason for stopping: ideas built on old prices would be misleading. If today is a market holiday, ignore this mail. '
            'Otherwise re-run the workflow later.</p></div></body></html>')

def send_mail(html, subject):
    m = MIMEMultipart("alternative")
    m["Subject"], m["From"], m["To"] = subject, EMAIL_USER, EMAIL_TO
    m.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(EMAIL_USER, EMAIL_PASS)
        s.sendmail(EMAIL_USER, EMAIL_TO.split(","), m.as_string())
