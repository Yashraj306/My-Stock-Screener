from config import *
from setups import premium_plan, position_size

GC = {"A": "#0a7d2c", "B": "#b9770e", "C": "#6b7280"}
FONT = "font-family:Arial,Helvetica,sans-serif;"

def n(x):
    return f"{x:,.2f}" if abs(x) < 1000 else f"{x:,.0f}"

def cell(label, val, color="#111"):
    return (f'<td style="padding:8px 6px;text-align:center;border:1px solid #e5e7eb">'
            f'<div style="font-size:11px;color:#6b7280">{label}</div>'
            f'<div style="font-size:15px;font-weight:bold;color:{color}">{val}</div></td>')

def card(r):
    bull = r["direction"] == "BULLISH"
    dc = "#0a7d2c" if bull else "#c0392b"
    lv, oc = r["lv"], r.get("oc")
    b = oc["best"] if oc else None
    act = "BUY CALL (CE)" if bull else "BUY PUT (PE)"
    kind = "Pre-rally: coiling near a key level" if r["setup"] == "PRE" else "Continuation: move already started"
    entry_lbl = "Entry (trigger)" if r["setup"] == "PRE" else "Entry (ref.)"
    h = [f'<div style="{FONT}border:1px solid #e5e7eb;border-left:6px solid {dc};border-radius:6px;padding:12px;margin:0 0 14px">']
    h.append(f'<table width="100%" cellpadding="0" cellspacing="0"><tr>'
             f'<td style="font-size:17px;font-weight:bold">{r["name"]}'
             f'<span style="color:{dc};font-size:14px"> &nbsp;{act}</span></td>'
             f'<td align="right"><span style="background:{GC[r["grade"]]};color:#fff;padding:3px 10px;border-radius:12px;'
             f'font-size:13px;font-weight:bold">Grade {r["grade"]}</span></td></tr></table>')
    h.append(f'<div style="font-size:12px;color:#4b5563;margin:4px 0 8px">{kind} | Sector: {r["sector"]} | '
             f'Close {n(r["price"])} ({r["chg"]:+.2f}%) | RSI {r["rsi"]:.0f} | Vol {r["vr"]:.1f}x | ATR {r["atr_pct"]:.1f}%</div>')
    h.append('<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse"><tr>'
             + cell(entry_lbl, n(lv["entry"])) + cell("Stop-loss", n(lv["stop"]), "#c0392b")
             + cell("Target 1", n(lv["t1"]), "#0a7d2c") + cell("Target 2", n(lv["t2"]), "#0a7d2c")
             + cell("R:R (T1)", f'{lv["rr"]:.2f}') + '</tr></table>')
    h.append('<div style="font-size:11px;color:#6b7280;margin:3px 0 8px">Levels are on the underlying and ATR-based. They are a plan, not a prediction.</div>')
    chips = "".join(f'<span style="display:inline-block;margin:0 6px 4px 0;padding:2px 8px;border-radius:10px;font-size:11px;'
                    f'background:{"#e7f6ec" if ok else "#fdecea"};color:{"#0a7d2c" if ok else "#c0392b"}">{"&#10003;" if ok else "&#10007;"} {k}</span>'
                    for k, ok in r["checks"].items())
    h.append(f'<div style="margin-bottom:6px">{chips}</div>')
    h.append('<div style="font-size:13px;margin-bottom:6px"><b>Why this setup</b><ul style="margin:4px 0 0 18px;padding:0">'
             + "".join(f"<li>{x}</li>" for x in r["why"][:7]) + '</ul></div>')
    if oc:
        h.append(f'<div style="font-size:12px;background:#f3f4f6;padding:8px;border-radius:4px;margin-bottom:6px">'
                 f'<b>Option chain</b> ({oc["src"]}, expiry {oc["expiry"]}, {oc["dte"]} days)<br>'
                 f'PCR {oc["pcr"]:.2f} | OI support {oc["support"]:.0f} | OI resistance {oc["resistance"]:.0f} | '
                 f'Max pain {oc["max_pain"]:.0f} | ATM IV {oc["iv"]:.1f}<br>'
                 f'OI action: <b>{oc["buildup"]}</b> ({oc["oi_chg_pct"]:+.1f}% OI) | OI bias: {oc["bias"]}</div>')
        if b:
            g = ""
            plan = premium_plan(b, lv)
            if b["delta"]:
                g = (f'<br>Delta {b["delta"]:.2f} | Theta {b["theta"]:.2f}/day ({b["decay"]:.1f}% of premium) | '
                     f'Gamma {b["gamma"]:.4f} | Vega {b["vega"]:.2f}')
            if plan:
                g += (f'<br>Premium plan (approx.): SL ~{plan["sl"]:.1f} | T1 ~{plan["t1"]:.1f} | R:R {plan["rr"]:.2f} '
                      f'<span style="color:#6b7280">(delta-based, ignores theta and IV change)</span>')
            h.append(f'<div style="font-size:12px;background:#fff8e6;padding:8px;border-radius:4px">'
                     f'<b>Best strike: {b["strike"]:.0f} {b["side"]}</b> | Premium {b["ltp"]:.1f} | OI {b["oi"]:,.0f} ({b["oi_up"]:+.1f}%) | '
                     f'Volume {b["vol"]:,.0f} | IV {b["iv"]:.1f}{g}</div>')
            z = position_size(b, plan, r.get("lot"))
            r["size"] = z
            if z:
                if z["lots"] > 0:
                    msg = (f'<b>{z["lots"]} lot(s)</b> of {z["lot"]} | outlay about Rs {z["outlay"]:,.0f} | '
                           f'loss at premium SL about Rs {z["lots"] * z["per_lot"]:,.0f}')
                else:
                    msg = (f'1 lot (Rs {z["one_lot"]:,.0f}) risks about Rs {z["per_lot"]:,.0f} at the premium SL, above your limit of '
                           f'Rs {z["max_loss"]:,.0f}. Skip it or use a spread.')
                h.append(f'<div style="font-size:12px;background:#eef6ff;padding:8px;border-radius:4px;margin-top:6px">'
                         f'<b>Position size</b> (risk {RISK_PCT:g}% of Rs {CAPITAL:,.0f}): {msg}</div>')
    else:
        h.append('<div style="font-size:12px;color:#6b7280">No option-chain data for this stock today.</div>')
    for f in r["flags"]:
        h.append(f'<div style="font-size:12px;color:#b45309;margin-top:6px">&#9888; {f}</div>')
    h.append('</div>')
    return "".join(h)

def track_html(s):
    if not s: return ('<div style="font-size:12px;color:#6b7280">Track record: no evaluated signals yet. '
                      'Every idea is logged and scored against the next session, so this box will fill in over the coming days.</div>')
    def row(label, x):
        if not x: return ""
        return (f'<tr><td style="padding:4px 8px;border:1px solid #e5e7eb">{label}</td>'
                f'<td align="center" style="border:1px solid #e5e7eb">{x["n"]}</td>'
                f'<td align="center" style="border:1px solid #e5e7eb">{x["win"]:.0f}%</td>'
                f'<td align="center" style="border:1px solid #e5e7eb">{x["t1"]:.0f}%</td>'
                f'<td align="center" style="border:1px solid #e5e7eb">{x["stop"]:.0f}%</td>'
                f'<td align="center" style="border:1px solid #e5e7eb">{x["avg"]:+.2f}%</td></tr>')
    return ('<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-size:12px">'
            '<tr style="background:#f3f4f6"><th align="left" style="padding:4px 8px">Last signals</th><th>Count</th><th>Profitable</th>'
            '<th>Hit T1</th><th>Hit stop</th><th>Avg move</th></tr>'
            + row("All", s["all"]) + row("Bullish", s["bull"]) + row("Bearish", s["bear"])
            + row("Morning mode", s["morning"]) + row("BTST mode", s["btst"])
            + '</table><div style="font-size:11px;color:#6b7280;margin-top:3px">'
              'Measured on the underlying: entry to the first full session after the signal. Small samples mean little, so wait for 30+ signals before trusting any number.</div>')
