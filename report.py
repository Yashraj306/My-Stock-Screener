"""Professional, defensive HTML email report for My Stock Screener."""
import datetime as dt
from html import escape
from cards import build_stock_card, build_equity_intraday_table

NAVY="#101D35"; BLUE="#2563EB"; GREEN="#059669"; RED="#DC2626"
GOLD="#D69E36"; MUTED="#64748B"; BORDER="#E5EAF2"

def esc(v, default="—"):
    return escape(str(default if v is None or v == "" else v))

def money(v):
    try: return f"₹{float(v):,.2f}"
    except (TypeError, ValueError): return "Unavailable"

def section(title, subtitle, accent=BLUE):
    return f"""<div style="margin:22px 0 9px;border-left:4px solid {accent};padding:5px 10px">
    <b style="font-size:15px;color:{NAVY}">{esc(title)}</b><div style="font-size:10px;color:{MUTED}">{esc(subtitle)}</div></div>"""

def metric(label, value, color=BLUE):
    return f"""<td style="padding:5px;width:25%"><div style="border:1px solid {BORDER};border-top:3px solid {color};padding:10px;border-radius:7px"><div style="font-size:9px;color:{MUTED}">{esc(label)}</div><b style="font-size:17px;color:{NAVY}">{esc(value)}</b></div></td>"""

def render_global_cues(cues):
    if not cues: return '<p style="font-size:11px;color:#64748B">Global market data unavailable.</p>'
    cells=[]
    for cue in cues:
        chg=cue.get("chg")
        color=GREEN if str(chg).startswith("+") else RED if str(chg).startswith("-") else MUTED
        cells.append(f'<td style="padding:8px;border:1px solid {BORDER}"><small>{esc(cue.get("name"))}</small><br><b>{esc(cue.get("val"))}</b><br><span style="color:{color}">{esc(chg)}</span></td>')
    rows=[]
    for i in range(0,len(cells),3):
        row=cells[i:i+3]
        row += ['<td></td>']*(3-len(row))
        rows.append('<tr>'+''.join(row)+'</tr>')
    return '<table width="100%" style="border-collapse:collapse;font-size:10px">'+''.join(rows)+'</table>'

def render_sectors(sectors):
    if not sectors: return '<p style="font-size:11px;color:#64748B">Sector data unavailable.</p>'
    rows=[]
    for x in sectors:
        read=str(x.get("read","Unavailable"))
        color=GREEN if read.lower()=="strong" else RED if read.lower()=="weak" else GOLD
        rows.append(f'<tr><td style="padding:7px;border-bottom:1px solid {BORDER}">{esc(x.get("name"))}</td><td align="right">{esc(x.get("avg_move"))}</td><td align="center">{esc(x.get("up_down"))}</td><td align="right" style="color:{color}">{esc(read)}</td></tr>')
    return '<table width="100%" style="font-size:11px;border-collapse:collapse"><tr><th align="left">SECTOR</th><th>CHANGE</th><th>ADV/DEC</th><th>READ</th></tr>'+''.join(rows)+'</table>'

def _signal_section(title, subtitle, items, renderer, accent):
    body=''.join(renderer(x) for x in items[:5] if isinstance(x,dict)) if items else '<p style="padding:12px;color:#64748B;font-size:11px">No valid signals passed the filters.</p>'
    return section(title,subtitle,accent)+body

def render_html_report(data):
    data=data if isinstance(data,dict) else {}
    summary=data.get("summary") or {}
    setups=data.get("setups") or {}
    mode=str(data.get("mode","UNKNOWN")).upper()
    eq=data.get("equity_intraday") or []
    bull=data.get("intraday_bullish") or [x for x in eq if str(x.get("action","")).upper().startswith("BUY")]
    bear=data.get("intraday_bearish") or [x for x in eq if str(x.get("action","")).upper().startswith("SELL")]
    obull=data.get("options_bullish") or setups.get("bullish") or []
    obear=data.get("options_bearish") or setups.get("bearish") or []
    total=len(bull)+len(bear)+len(obull)+len(obear)
    now=dt.datetime.now(dt.timezone(dt.timedelta(hours=5,minutes=30))).strftime("%d %b %Y | %I:%M %p IST")
    regime=str(summary.get("breadth_tone") or "Unavailable").upper()
    regime_color=GREEN if regime=="BULLISH" else RED if regime=="BEARISH" else GOLD
    return f"""<!doctype html><html><head><meta charset="utf-8"></head>
    <body style="margin:0;background:#F3F6FB;font-family:Arial,sans-serif;color:{NAVY}">
    <table width="100%" cellpadding="0" cellspacing="0" style="padding:14px 4px"><tr><td align="center">
    <table width="100%" cellpadding="0" cellspacing="0" style="max-width:720px;background:#fff;border:1px solid {BORDER};border-radius:10px">
    <tr><td style="background:{NAVY};padding:22px;color:#fff">
      <div style="font-size:10px;color:#7DD3FC;font-weight:bold;letter-spacing:1.5px">QUANTITATIVE RESEARCH • MARKET INTELLIGENCE</div>
      <div style="font-size:23px;font-weight:900;margin:8px 0">MARKET STRATEGY REPORT</div>
      <div style="font-size:12px;color:#CBD5E1">{esc(mode)} | {esc(now)}</div>
      <div style="font-size:10px;color:#CBD5E1;margin-top:7px">Data source: {esc(summary.get('data_source','Unavailable'))}</div>
    </td></tr>
    <tr><td style="padding:15px">
      <b style="font-size:10px;letter-spacing:1px;color:{MUTED}">EXECUTIVE SNAPSHOT</b>
      <table width="100%" style="margin-top:8px"><tr>{metric("VALID SIGNALS",total)}{metric("INTRADAY",len(bull)+len(bear),GOLD)}{metric("OPTIONS",len(obull)+len(obear),GREEN)}{metric("REGIME",regime,regime_color)}</tr></table>
      <div style="margin-top:10px;padding:11px;background:#F8FAFC;border:1px solid {BORDER};font-size:11px;line-height:1.8">
        <b>NIFTY:</b> {esc(summary.get('nifty_spot'))} ({esc(summary.get('nifty_chg'))}) |
        <b>BANK NIFTY:</b> {esc(summary.get('bnf_spot'))} ({esc(summary.get('bnf_chg'))})<br>
        <b>FIN NIFTY:</b> {esc(summary.get('fin_spot'))} ({esc(summary.get('fin_chg'))}) |
        <b>BREADTH:</b> {esc(summary.get('breadth_ratio'))}<br>
        <b>ABOVE EMA20:</b> {esc(summary.get('above_ema20'))}% |
        <b>ABOVE EMA50:</b> {esc(summary.get('above_ema50'))}%
      </div>
      <div style="margin-top:9px;padding:10px;background:#FFFBEB;border:1px solid #FDE68A;font-size:10px;line-height:1.8;color:#92400E">
        <b>RISK & EXECUTION CONTROLS</b><br>
        Capital: NOT DEFINED | Quantity / lots: NOT CALCULATED<br>
        Execution: SIGNAL ONLY | Automatic orders: DISABLED<br>
        Broker leverage / margin is not assumed.
      </div>
      {section("GLOBAL MARKET MONITOR","External market indicators")}
      {render_global_cues(data.get("global_cues") or [])}
      {section("SECTOR ROTATION","Sector strength and participation",GOLD)}
      {render_sectors(data.get("sectors") or [])}
      {section("INTRADAY EQUITY","Five-minute candles • VWAP • Momentum",BLUE)}
      {_signal_section("BULLISH EQUITY SETUPS","Long candidates | Maximum 5",bull,lambda x: _intraday_card(x,True),GREEN)}
      {_signal_section("BEARISH EQUITY SETUPS","Short candidates | Maximum 5",bear,lambda x: _intraday_card(x,False),RED)}
      {section("BTST STOCK OPTIONS","Option buying or defined-risk debit spreads",GOLD)}
      {_signal_section("BULLISH OPTIONS","CE Buying / Bull Call Spread | Maximum 5",obull,build_stock_card,GREEN)}
      {_signal_section("BEARISH OPTIONS","PE Buying / Bear Put Spread | Maximum 5",obear,build_stock_card,RED)}
      {section("IMPORTANT DISCLAIMER","Research shortlist only")}
      <p style="font-size:10px;line-height:1.7;color:{MUTED}">Signals are not recommendations or guarantees. Verify live prices, liquidity, expiry, contract specifications, broker margin and risk before any decision. No orders are placed by this system. Missing data is shown as unavailable; a signal should be rejected when required data is missing.</p>
    </td></tr></table></td></tr></table></body></html>"""

def _intraday_card(item,bullish=True):
    accent=GREEN if bullish else RED
    side="BULLISH LONG" if bullish else "BEARISH SHORT"
    return f"""<div style="border:1px solid {BORDER};border-left:4px solid {accent};padding:12px;margin:8px 0;border-radius:8px">
    <b style="font-size:16px">{esc(item.get('symbol'))}</b> <span style="color:{accent};font-size:10px;font-weight:bold">{side}</span>
    <div style="font-size:11px;line-height:1.9;margin-top:6px">
    Entry: <b>{money(item.get('entry'))}</b> | Stop: <b>{money(item.get('stop_loss'))}</b> | Target 1: <b>{money(item.get('target_1'))}</b><br>
    Target 2: {money(item.get('target_2'))} | RSI: {esc(item.get('rsi'))} | Volume ratio: {esc(item.get('volume_ratio'))}<br>
    Quantity: Not calculated | Leverage: Not assumed
    </div></div>"""
