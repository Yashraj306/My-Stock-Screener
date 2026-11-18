import datetime as dt
from html import escape

NAVY = "#101D35"
BLUE = "#2563EB"
GREEN = "#059669"
RED = "#DC2626"
GOLD = "#D69E36"
MUTED = "#64748B"
BORDER = "#E5EAF2"
BG = "#F3F6FB"


def esc(value, default="—"):
    if value is None or value == "":
        value = default
    return escape(str(value))


def rupee(value):
    try:
        return "₹" + f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def change_color(value):
    text = str(value or "")
    if text.startswith("+"): return GREEN
    if text.startswith("-"): return RED
    return MUTED


def pill(text, color=BLUE):
    return f"""
    <span style="display:inline-block;padding:5px 10px;border-radius:20px;background:{color}15;color:{color};font-size:10px;font-weight:800;letter-spacing:.5px;">
        {esc(text)}
    </span>
    """


def metric(label, value, sub="", accent=BLUE):
    return f"""
    <td width="25%" valign="top" style="padding:5px;">
      <div style="background:#FFFFFF;border:1px solid {BORDER};border-top:3px solid {accent};border-radius:9px;padding:13px 11px;min-height:65px;">
        <div style="color:{MUTED};font-size:9px;font-weight:800;letter-spacing:.8px;text-transform:uppercase;">{esc(label)}</div>
        <div style="color:{NAVY};font-size:19px;font-weight:800;margin-top:7px;">{esc(value)}</div>
        <div style="color:{MUTED};font-size:10px;margin-top:3px;">{esc(sub, "")}</div>
      </div>
    </td>
    """


def section_header(number_text, title, subtitle, accent=BLUE):
    num_box = f'<td width="38" valign="top"><div style="background:{accent};color:#FFFFFF;border-radius:8px;width:29px;padding:7px 0;text-align:center;font-size:12px;font-weight:800;">{esc(number_text)}</div></td>' if number_text else ""
    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="margin:25px 0 10px;">
      <tr>
        {num_box}
        <td valign="middle">
          <div style="color:{NAVY};font-size:16px;font-weight:800;">{esc(title)}</div>
          <div style="color:{MUTED};font-size:10px;margin-top:3px;">{esc(subtitle)}</div>
        </td>
      </tr>
    </table>
    """


def render_global_cues(cues):
    if not cues: return ""
    rows = []
    for i in range(0, len(cues), 3):
        chunk = cues[i:i + 3]
        row = "".join(f'<td width="33%" valign="top" style="padding:7px 9px;border:1px solid {BORDER};background:#FFFFFF;"><div style="font-size:10px;color:{MUTED};">{esc(x.get("name"))}</div><div style="font-size:14px;font-weight:800;color:{NAVY};margin-top:4px;">{esc(x.get("val"))}</div><div style="font-size:10px;font-weight:700;color:{change_color(x.get("chg"))};">{esc(x.get("chg"))}</div></td>' for x in chunk)
        if len(chunk) < 3: row += '<td width="33%"></td>' * (3 - len(chunk))
        rows.append(f"<tr>{row}</tr>")
    return f"""
    {section_header("01", "GLOBAL MARKET MONITOR", "External market indicators and volatility")}
    <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background:#FFFFFF;">{''.join(rows)}</table>
    """


def render_sector_table(sectors):
    if not sectors: return '<div style="color:#94A3B8;font-size:11px;">Sector data unavailable.</div>'
    rows = ""
    for item in sectors:
        read = str(item.get("read", "Mixed"))
        color = GREEN if read.lower() == "strong" else (RED if read.lower() == "weak" else GOLD)
        rows += f'<tr><td style="padding:9px 7px;border-bottom:1px solid {BORDER};font-weight:700;">{esc(item.get("name"))}</td><td align="right" style="padding:9px 7px;border-bottom:1px solid {BORDER};">{esc(item.get("avg_move"))}</td><td align="center" style="padding:9px 7px;border-bottom:1px solid {BORDER};">{esc(item.get("up_down"))}</td><td align="right" style="padding:9px 7px;border-bottom:1px solid {BORDER};color:{color};font-weight:800;">{esc(read)}</td></tr>'
    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background:#FFFFFF;font-size:11px;">
      <tr style="background:#F8FAFC;color:{MUTED};"><th align="left" style="padding:9px 7px;">SECTOR</th><th align="right" style="padding:9px 7px;">CHANGE</th><th align="center" style="padding:9px 7px;">ADV/DEC</th><th align="right" style="padding:9px 7px;">READ</th></tr>
      {rows}
    </table>
    """


def render_intraday_card(item, bullish=True):
    accent = GREEN if bullish else RED
    side = "BULLISH LONG" if bullish else "BEARISH SHORT"
    entry, sl, t1, t2 = item.get("entry"), item.get("stop_loss") or item.get("sl"), item.get("target_1") or item.get("t1"), item.get("target_2") or item.get("t2")
    return f"""
    <div style="background:#FFFFFF;border:1px solid {BORDER};border-left:4px solid {accent};border-radius:9px;padding:14px;margin-bottom:10px;">
      <table width="100%" cellpadding="0" cellspacing="0"><tr><td><div style="font-size:17px;font-weight:900;color:{NAVY};">{esc(item.get("symbol"))}</div><div style="font-size:10px;color:{MUTED};margin-top:3px;">{esc(item.get("sector"))}</div></td><td align="right" valign="top">{pill(side, accent)}</td></tr></table>
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:13px;">
        <tr>
          <td width="33%" style="padding:8px;background:#F8FAFC;border-radius:5px;"><div style="font-size:9px;color:{MUTED};">ENTRY</div><b style="font-size:14px;color:{NAVY};">{rupee(entry)}</b></td>
          <td width="33%" style="padding:8px;background:#FEF2F2;border-radius:5px;"><div style="font-size:9px;color:{RED};">STOP LOSS</div><b style="font-size:14px;color:{RED};">{rupee(sl)}</b></td>
          <td width="33%" style="padding:8px;background:#ECFDF5;border-radius:5px;"><div style="font-size:9px;color:{GREEN};">TARGET 1</div><b style="font-size:14px;color:{GREEN};">{rupee(t1)}</b></td>
        </tr>
      </table>
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:9px;">
        <tr>
          <td style="font-size:10px;color:{MUTED};">TARGET 2<br><b style="font-size:12px;color:{NAVY};">{rupee(t2)}</b></td>
          <td style="font-size:10px;color:{MUTED};">RSI<br><b style="font-size:12px;color:{NAVY};">{esc(item.get("rsi"))}</b></td>
          <td style="font-size:10px;color:{MUTED};">VOL RATIO<br><b style="font-size:12px;color:{NAVY};">{esc(item.get("volume_ratio") or item.get("vr"))}x</b></td>
          <td style="font-size:10px;color:{MUTED};">VWAP<br><b style="font-size:12px;color:{NAVY};">{rupee(item.get("vwap"))}</b></td>
        </tr>
      </table>
    </div>
    """


def render_option_card(item):
    hedge = item.get("hedge_plan") or item.get("option_plan") or {}
    side = item.get("side", "CALL")
    accent = GREEN if "CALL" in side or "CE" in side else RED
    
    hedge_box = ""
    if hedge and "buy_leg" in hedge:
        hedge_box = f"""
        <div style="background:#F0FDF4;border:1px solid #BBF7D0;border-radius:6px;padding:8px;margin-top:8px;font-size:11px;">
          <div style="font-weight:700;color:#166534;margin-bottom:4px;">🛡️ Spread: {esc(hedge.get('strategy_name'))}</div>
          <div><b>Buy:</b> {esc(hedge.get('buy_leg'))} (₹{hedge.get('buy_prem')}) &nbsp;|&nbsp; <b>Sell:</b> {esc(hedge.get('sell_leg'))} (₹{hedge.get('sell_prem')})</div>
          <div><b>Max Risk:</b> ₹{hedge.get('max_loss'):,} &nbsp;|&nbsp; <b>Max Profit:</b> ₹{hedge.get('max_profit'):,}</div>
        </div>
        """
    return f"""
    <div style="background:#FFFFFF;border:1px solid {BORDER};border-left:4px solid {accent};border-radius:9px;padding:14px;margin-bottom:11px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td><div style="font-size:17px;font-weight:900;color:{NAVY};">{esc(item.get("symbol"))}</div><div style="font-size:10px;color:{MUTED};">Spot: {rupee(item.get("close"))}</div></td>
          <td align="right" valign="top">{pill(item.get("setup_kind", "Option"), accent)}</td>
        </tr>
      </table>
      {hedge_box}
    </div>
    """


def render_signal_section(title, subtitle, items, renderer, bullish=True):
    accent = GREEN if bullish else RED
    content = "".join(renderer(item) for item in items[:5]) if items else '<div style="padding:15px;background:#FFFFFF;border:1px solid {BORDER};border-radius:8px;color:#64748B;font-size:11px;">No valid signals passed filters.</div>'
    return f"{section_header('', title, subtitle, accent)}{content}"


def render_html_report(data):
    data = data or {}
    summary = data.get("summary") or {}
    setups = data.get("setups") or {}
    index_trades = data.get("index_trades") or []

    intraday_bull = [x for x in data.get("equity_intraday", []) if str(x.get("action", "")).startswith("BUY")]
    intraday_bear = [x for x in data.get("equity_intraday", []) if str(x.get("action", "")).startswith("SELL")]

    option_bull = setups.get("bullish") or []
    option_bear = setups.get("bearish") or []
    total_ideas = summary.get("ideas_count") or (len(index_trades) + len(intraday_bull) + len(intraday_bear) + len(option_bull) + len(option_bear))

    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30)))
    timestamp = now.strftime("%d %b %Y | %I:%M %p IST")
    regime = str(summary.get("breadth_tone", "UNKNOWN")).upper()
    regime_color = GREEN if regime == "BULLISH" else (RED if regime == "BEARISH" else GOLD)

    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
    <body style="margin:0;padding:0;background:{BG};font-family:Arial,Helvetica,sans-serif;color:{NAVY};">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:{BG};padding:18px 6px;">
        <tr>
          <td align="center">
            <table width="100%" cellpadding="0" cellspacing="0" style="max-width:720px;background:#FFFFFF;border-radius:13px;overflow:hidden;border:1px solid {BORDER};">
              <tr>
                <td style="background:{NAVY};padding:25px 22px;color:#FFFFFF;">
                  <div style="color:#7DD3FC;font-size:10px;font-weight:800;letter-spacing:2px;">QUANT DESK • UPSTOX LIVE FEED</div>
                  <div style="font-size:24px;font-weight:900;margin-top:9px;">MARKET STRATEGY & HEDGING DESK</div>
                  <div style="color:#CBD5E1;font-size:12px;margin-top:6px;">5x MIS LEVERAGE EQUITY & OPTION SPREADS</div>
                  <div style="margin-top:17px;padding-top:12px;border-top:1px solid #334155;color:#CBD5E1;font-size:10px;">{timestamp} &nbsp; • &nbsp; DATA: {esc(summary.get("data_source", "Upstox Live API v2"))}</div>
                </td>
              </tr>
              <tr>
                <td style="padding:19px 18px 7px;">
                  <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:9px;">
                    <tr>
                      {metric("TOTAL SETUPS", total_ideas, "Active Signals", BLUE)}
                      {metric("EQUITY (5x)", len(intraday_bull) + len(intraday_bear), "MIS Trades", GOLD)}
                      {metric("OPTIONS", len(option_bull) + len(option_bear) + len(index_trades), "Spreads", GREEN)}
                      {metric("REGIME", regime, "Breadth", regime_color)}
                    </tr>
                  </table>
                  <div style="margin-top:9px;padding:13px;background:#F8FAFC;border:1px solid {BORDER};border-radius:8px;font-size:11px;line-height:1.9;">
                    <b>NIFTY:</b> {esc(summary.get("nifty_spot"))} ({esc(summary.get("nifty_chg"))}) &nbsp;&nbsp; <b>BANKNIFTY:</b> {esc(summary.get("bnf_spot"))} ({esc(summary.get("bnf_chg"))})<br>
                    <b>BREADTH:</b> {esc(summary.get("breadth_ratio"))} &nbsp;&nbsp; <b>ABOVE EMA20:</b> {esc(summary.get("above_ema20"))}%
                  </div>
                </td>
              </tr>
              <tr><td style="padding:0 18px 5px;">{render_global_cues(data.get("global_cues") or [])}</td></tr>
              <tr><td style="padding:0 18px 5px;">{section_header("02", "SECTOR ROTATION", "Relative sector strength")}{render_sector_table(data.get("sectors") or [])}</td></tr>
              <tr><td style="padding:0 18px 5px;">{section_header("03", "INDEX OPTIONS & SPREADS", "Nifty, BankNifty, FinNifty & Sensex", BLUE)}{"".join(render_option_card(x) for x in index_trades)}</td></tr>
              <tr><td style="padding:0 18px 5px;">{section_header("04", "STOCK OPTION SPREADS", "Pre-built Call & Put Spreads", GOLD)}{render_signal_section("BULLISH SPREADS", "Top 5 Call Debit Spreads", option_bull, render_option_card, True)}{render_signal_section("BEARISH SPREADS", "Top 5 Put Debit Spreads", option_bear, render_option_card, False)}</td></tr>
              <tr><td style="padding:0 18px 5px;">{section_header("05", "5x MIS INTRADAY EQUITY", "Momentum cash stocks with 5x leverage", GREEN)}{render_signal_section("BULLISH EQUITY (BUY)", "Long 5x", intraday_bull, lambda x: render_intraday_card(x, True), True)}{render_signal_section("BEARISH EQUITY (SELL)", "Short 5x", intraday_bear, lambda x: render_intraday_card(x, False), False)}</td></tr>
              <tr><td style="background:{NAVY};padding:19px 20px;color:#CBD5E1;"><div style="font-size:11px;font-weight:800;color:#FFFFFF;">RISK & EXECUTION PROTOCOL</div><div style="font-size:10px;line-height:1.9;margin-top:9px;">01 — Research and signal generation desk only.<br>02 — Verify broker MIS margin eligibility before order placement.</div></td></tr>
            </table>
          </td>
        </tr>
      </table>
    </body>
    </html>
    """
