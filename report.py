import datetime as dt
from html import escape

from cards import render_intraday_card, render_option_card
from config import ALLOW_MIS_LEVERAGE, CAPITAL, CAPITAL_CONFIGURED, MAX_ALLOCATION_PCT, MIS_LEVERAGE, RISK_PCT

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


def change_color(value):
    text = str(value or "")
    if text.startswith("+"):
        return GREEN
    if text.startswith("-"):
        return RED
    return MUTED


def metric(label, value, sub="", accent=BLUE):
    return f"""
    <td width="25%" valign="top" style="padding:5px;">
      <div style="background:#FFFFFF;border:1px solid {BORDER};border-top:3px solid {accent};border-radius:9px;padding:12px 10px;min-height:64px;">
        <div style="color:{MUTED};font-size:9px;font-weight:800;letter-spacing:.7px;text-transform:uppercase;">{esc(label)}</div>
        <div style="color:{NAVY};font-size:18px;font-weight:800;margin-top:6px;">{esc(value)}</div>
        <div style="color:{MUTED};font-size:10px;margin-top:3px;">{esc(sub, "")}</div>
      </div>
    </td>
    """


def section_header(number_text, title, subtitle, accent=BLUE):
    num_box = (
        f'<td width="38" valign="top"><div style="background:{accent};color:#FFFFFF;border-radius:8px;width:29px;padding:7px 0;text-align:center;font-size:12px;font-weight:800;">{esc(number_text)}</div></td>'
        if number_text
        else ""
    )
    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="margin:22px 0 9px;">
      <tr>
        {num_box}
        <td valign="middle">
          <div style="color:{NAVY};font-size:15px;font-weight:800;">{esc(title)}</div>
          <div style="color:{MUTED};font-size:10px;margin-top:2px;">{esc(subtitle)}</div>
        </td>
      </tr>
    </table>
    """


def render_global_cues(cues):
    if not cues:
        return ""
    rows = []
    for i in range(0, len(cues), 3):
        chunk = cues[i : i + 3]
        row = "".join(
            f'<td width="33%" valign="top" style="padding:7px 9px;border:1px solid {BORDER};background:#FFFFFF;">'
            f'<div style="font-size:10px;color:{MUTED};">{esc(x.get("name"))}</div>'
            f'<div style="font-size:13px;font-weight:800;color:{NAVY};margin-top:3px;">{esc(x.get("val"))}</div>'
            f'<div style="font-size:10px;font-weight:700;color:{change_color(x.get("chg"))};">{esc(x.get("chg"))}</div>'
            f"</td>"
            for x in chunk
        )
        if len(chunk) < 3:
            row += '<td width="33%"></td>' * (3 - len(chunk))
        rows.append(f"<tr>{row}</tr>")
    return f"""
    {section_header("01", "GLOBAL MARKET MONITOR", "External market indicators and volatility")}
    <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background:#FFFFFF;">{"".join(rows)}</table>
    """


def render_sector_table(sectors):
    if not sectors:
        return '<div style="color:#94A3B8;font-size:11px;">Sector data unavailable.</div>'
    rows = ""
    for item in sectors:
        read = str(item.get("read", "Mixed"))
        color = GREEN if read.lower() == "strong" else (RED if read.lower() == "weak" else GOLD)
        rows += (
            f'<tr>'
            f'<td style="padding:8px 7px;border-bottom:1px solid {BORDER};font-weight:700;">{esc(item.get("name"))}</td>'
            f'<td align="right" style="padding:8px 7px;border-bottom:1px solid {BORDER};">{esc(item.get("avg_move"))}</td>'
            f'<td align="center" style="padding:8px 7px;border-bottom:1px solid {BORDER};">{esc(item.get("up_down"))}</td>'
            f'<td align="right" style="padding:8px 7px;border-bottom:1px solid {BORDER};color:{color};font-weight:800;">{esc(read)}</td>'
            f"</tr>"
        )
    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background:#FFFFFF;font-size:11px;">
      <tr style="background:#F8FAFC;color:{MUTED};">
        <th align="left" style="padding:8px 7px;">SECTOR</th>
        <th align="right" style="padding:8px 7px;">CHANGE</th>
        <th align="center" style="padding:8px 7px;">ADV/DEC</th>
        <th align="right" style="padding:8px 7px;">READ</th>
      </tr>
      {rows}
    </table>
    """


def render_signal_section(title, subtitle, items, renderer, bullish=True):
    accent = GREEN if bullish else RED
    if not items:
        body = f'<div style="padding:13px;background:#FFFFFF;border:1px solid {BORDER};border-radius:8px;color:{MUTED};font-size:11px;">No signals matched current criteria.</div>'
    else:
        body = "".join(renderer(item) for item in items[:5])
    return f"""
    {section_header("", title, subtitle, accent)}
    {body}
    """


def _risk_banner_html(summary: dict) -> str:
    cap = summary.get("capital", CAPITAL)
    cap_ok = bool(summary.get("capital_configured", CAPITAL_CONFIGURED))
    risk_pct = float(summary.get("risk_pct", RISK_PCT)) * 100.0
    alloc_pct = float(summary.get("max_allocation_pct", MAX_ALLOCATION_PCT)) * 100.0
    lev_enabled = bool(summary.get("leverage_enabled", ALLOW_MIS_LEVERAGE))
    lev_mult = float(summary.get("leverage_multiplier", MIS_LEVERAGE if lev_enabled else 1.0))

    if cap_ok and float(cap) > 0:
        cap_str = f"₹{float(cap):,.2f}"
        risk_budget_str = f"₹{float(cap) * (risk_pct / 100.0):,.2f} ({risk_pct:.2f}%)"
    else:
        cap_str = "NOT CONFIGURED"
        risk_budget_str = "Not calculated"

    lev_str = f"MIS {lev_mult:g}x Enabled" if (lev_enabled and lev_mult > 1.0) else "1x Cash Only (No Leverage)"

    return f"""
    <div style="margin-top:10px;padding:11px 13px;background:#FFFBEB;border:1px solid #FDE68A;border-radius:7px;font-size:10.5px;line-height:1.7;color:#92400E;">
      <b>SIZING & LEVERAGE POLICY</b><br>
      Capital: <b>{esc(cap_str)}</b> &nbsp;|&nbsp; Per-Trade Risk Cap: <b>{esc(risk_budget_str)}</b> &nbsp;|&nbsp; Max Allocation Cap: <b>{alloc_pct:.0f}%</b><br>
      Leverage Assumption: <b>{esc(lev_str)}</b> &nbsp;|&nbsp; Order Execution: <b>SIGNAL ONLY (Manual Verification Required)</b>
    </div>
    """


def render_html_report(data: dict | None) -> str:
    data = data or {}
    summary = data.get("summary") or {}
    setups = data.get("setups") or {}
    index_trades = data.get("index_trades") or []

    intraday_bull = data.get("intraday_bullish") or [
        x for x in (data.get("equity_intraday") or []) if str(x.get("action", "")).startswith("BUY")
    ]
    intraday_bear = data.get("intraday_bearish") or [
        x for x in (data.get("equity_intraday") or []) if str(x.get("action", "")).startswith("SELL")
    ]

    option_bull = data.get("options_bullish") or setups.get("bullish") or []
    option_bear = data.get("options_bearish") or setups.get("bearish") or []

    total_ideas = summary.get("ideas_count")
    if total_ideas is None:
        total_ideas = len(index_trades) + len(intraday_bull) + len(intraday_bear) + len(option_bull) + len(option_bear)

    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30)))
    timestamp = now.strftime("%d %b %Y | %I:%M %p IST")

    regime = str(summary.get("breadth_tone", "MIXED")).upper()
    regime_color = GREEN if regime == "BULLISH" else (RED if regime == "BEARISH" else GOLD)

    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
    <body style="margin:0;padding:0;background:{BG};font-family:Arial,Helvetica,sans-serif;color:{NAVY};">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:{BG};padding:16px 6px;">
        <tr>
          <td align="center">
            <table width="100%" cellpadding="0" cellspacing="0" style="max-width:720px;background:#FFFFFF;border-radius:12px;overflow:hidden;border:1px solid {BORDER};">
              <tr>
                <td style="background:{NAVY};padding:22px;color:#FFFFFF;">
                  <div style="color:#7DD3FC;font-size:10px;font-weight:800;letter-spacing:1.8px;">NSE / BSE QUANTITATIVE RESEARCH DESK</div>
                  <div style="font-size:22px;font-weight:900;margin-top:8px;">{esc(data.get("title") or "MARKET STRATEGY & HEDGING REPORT")}</div>
                  <div style="margin-top:14px;padding-top:10px;border-top:1px solid #334155;color:#CBD5E1;font-size:10px;">
                    {timestamp} &nbsp;•&nbsp; DATA SOURCE: {esc(summary.get("data_source", "UNAVAILABLE"))}
                  </div>
                </td>
              </tr>

              <tr>
                <td style="padding:18px 18px 6px;">
                  <table width="100%" cellpadding="0" cellspacing="0">
                    <tr>
                      {metric("TOTAL SIGNALS", total_ideas, "Current scan", BLUE)}
                      {metric("EQUITY", len(intraday_bull) + len(intraday_bear), "Intraday setups", GOLD)}
                      {metric("OPTIONS", len(option_bull) + len(option_bear) + len(index_trades), "Index & stock plans", GREEN)}
                      {metric("BREADTH REGIME", regime, "Advance / decline", regime_color)}
                    </tr>
                  </table>

                  <div style="margin-top:9px;padding:12px;background:#F8FAFC;border:1px solid {BORDER};border-radius:8px;font-size:11px;line-height:1.85;">
                    <b>NIFTY:</b> {esc(summary.get("nifty_spot"))} ({esc(summary.get("nifty_chg"))}) &nbsp;|&nbsp;
                    <b>BANKNIFTY:</b> {esc(summary.get("bnf_spot"))} ({esc(summary.get("bnf_chg"))}) &nbsp;|&nbsp;
                    <b>FINNIFTY:</b> {esc(summary.get("fin_spot"))} ({esc(summary.get("fin_chg"))})<br>
                    <b>BREADTH:</b> {esc(summary.get("breadth_ratio"))} &nbsp;|&nbsp;
                    <b>ABOVE EMA20:</b> {esc(summary.get("above_ema20"))}% &nbsp;|&nbsp;
                    <b>ABOVE EMA50:</b> {esc(summary.get("above_ema50"))}%
                  </div>

                  {_risk_banner_html(summary)}
                </td>
              </tr>

              <tr><td style="padding:0 18px 4px;">{render_global_cues(data.get("global_cues") or [])}</td></tr>
              <tr><td style="padding:0 18px 4px;">{section_header("02", "SECTOR ROTATION", "Sector-level price change and breadth")}{render_sector_table(data.get("sectors") or [])}</td></tr>
              <tr><td style="padding:0 18px 4px;">{section_header("03", "INDEX OPTIONS & SPREADS", "Directional index plans filtered against live option-chain data", BLUE)}{"".join(render_option_card(x) for x in index_trades) if index_trades else '<div style="padding:13px;background:#FFFFFF;border:1px solid #E5EAF2;border-radius:8px;color:#64748B;font-size:11px;">No index setups generated.</div>'}</td></tr>
              <tr><td style="padding:0 18px 4px;">{section_header("04", "STOCK OPTION PLANS", "Bullish and bearish option/spread candidates", GOLD)}{render_signal_section("BULLISH OPTION SETUPS", "Up to 5 bullish candidates", option_bull, render_option_card, True)}{render_signal_section("BEARISH OPTION SETUPS", "Up to 5 bearish candidates", option_bear, render_option_card, False)}</td></tr>
              <tr><td style="padding:0 18px 4px;">{section_header("05", "INTRADAY EQUITY SETUPS", "Risk-capped sizing per configured capital and leverage policy", GREEN)}{render_signal_section("BULLISH EQUITY SETUPS", "Long candidates", intraday_bull, lambda x: render_intraday_card(x, True), True)}{render_signal_section("BEARISH EQUITY SETUPS", "Short candidates", intraday_bear, lambda x: render_intraday_card(x, False), False)}</td></tr>

              <tr>
                <td style="background:{NAVY};padding:18px 20px;color:#CBD5E1;margin-top:16px;">
                  <div style="font-size:11px;font-weight:800;color:#FFFFFF;letter-spacing:1px;">RISK & STATISTICAL DISCLOSURE</div>
                  <div style="font-size:10px;line-height:1.85;margin-top:8px;">
                    01 — Signals are analytical outputs only; verify live quotes, liquidity, bid-ask spreads, margin, and expiry before trading.<br>
                    02 — No win rate or accuracy guarantee is claimed unless backed by a logged, post-cost out-of-sample record of sufficient sample size.
                  </div>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </body>
    </html>
    """
