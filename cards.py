"""HTML card helpers compatible with report.py; signals only."""
from html import escape

NAVY = "#101D35"
GREEN = "#059669"
RED = "#DC2626"
MUTED = "#64748B"
BORDER = "#E5EAF2"

def _e(v, default="—"):
    return escape(str(default if v is None or v == "" else v))

def _money(v):
    try:
        return f"₹{float(v):,.2f}"
    except (TypeError, ValueError):
        return "Unavailable"

def build_stock_card(item):
    """Render one stock-option or index-option setup."""
    item = item if isinstance(item, dict) else {}
    plan = item.get("option_plan") or {}
    if not isinstance(plan, dict):
        plan = {}
    strategy = str(plan.get("strategy") or item.get("strategy") or "OPTION DATA UNAVAILABLE")
    bullish = ("BULL" in strategy.upper()
               or str(item.get("side", "")).upper().startswith("CALL")
               or str(item.get("signal", "")).upper() == "BUY")
    accent = GREEN if bullish else RED
    if strategy == "OPTION_BUYING":
        rows = [
            ("Option", plan.get("option_type")),
            ("Strike", plan.get("strike")),
            ("Expiry", plan.get("expiry")),
            ("Entry premium", _money(plan.get("entry_premium"))),
            ("IV", plan.get("iv")), ("Delta", plan.get("delta")),
            ("Theta", plan.get("theta")),
            ("Maximum loss / unit", _money(plan.get("maximum_loss_per_unit"))),
        ]
    elif strategy in ("BULL_CALL_SPREAD", "BEAR_PUT_SPREAD"):
        buy, sell = plan.get("buy_leg") or {}, plan.get("sell_leg") or {}
        rows = [
            ("Buy strike", buy.get("strike")), ("Sell strike", sell.get("strike")),
            ("Expiry", plan.get("expiry")),
            ("Net debit / unit", _money(plan.get("net_debit_per_unit"))),
            ("Maximum loss / unit", _money(plan.get("maximum_loss_per_unit"))),
            ("Maximum profit / unit", _money(plan.get("maximum_profit_per_unit"))),
            ("Breakeven", _money(plan.get("breakeven"))),
        ]
    else:
        rows = [("Option details", "Unavailable — setup rejected unless verified")]
    detail = "".join(
        f'<tr><td style="padding:6px;border-bottom:1px solid {BORDER};color:{MUTED}">{_e(k)}</td>'
        f'<td align="right" style="padding:6px;border-bottom:1px solid {BORDER};font-weight:700">{_e(v)}</td></tr>'
        for k, v in rows
    )
    return f"""
    <div style="border:1px solid {BORDER};border-left:4px solid {accent};border-radius:9px;padding:13px;margin:9px 0;background:#fff">
      <div style="font-size:17px;font-weight:900;color:{NAVY}">{_e(item.get('symbol'))}</div>
      <div style="font-size:10px;color:{MUTED}">Underlying: {_money(item.get('close'))} | Strategy: {_e(strategy)}</div>
      <table width="100%" style="font-size:11px;border-collapse:collapse;margin-top:8px">{detail}</table>
      <div style="font-size:10px;color:{MUTED};padding-top:8px">Quantity / lots not calculated. Signal only; automatic orders disabled.</div>
    </div>"""

def build_equity_intraday_table(items):
    """Render intraday equity signals without assuming broker leverage."""
    if not items:
        return '<p style="font-size:11px;color:#64748B">No valid intraday signals passed the filters.</p>'
    rows = []
    for item in items[:10]:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).upper()
        bullish = "BUY" in action or "LONG" in action
        color = GREEN if bullish else RED
        rows.append(f"""
        <tr>
          <td style="padding:7px;border-bottom:1px solid {BORDER}"><b>{_e(item.get('symbol'))}</b><br><small>{_e(item.get('sector','Equity'))}</small></td>
          <td style="padding:7px;color:{color};font-weight:800">{_e(action)}</td>
          <td style="padding:7px;text-align:right">{_money(item.get('entry'))}</td>
          <td style="padding:7px;text-align:right">{_money(item.get('stop_loss'))}</td>
          <td style="padding:7px;text-align:right">{_money(item.get('target_1'))}</td>
          <td style="padding:7px;text-align:right">{_e(item.get('volume_ratio','Unavailable'))}</td>
        </tr>""")
    return f"""<div style="overflow-x:auto"><table width="100%" style="font-size:10px;border-collapse:collapse">
      <tr style="background:#F1F5F9"><th align="left">SYMBOL</th><th align="left">SIDE</th><th align="right">ENTRY</th><th align="right">STOP</th><th align="right">TARGET</th><th align="right">VOL RATIO</th></tr>
      {''.join(rows)}
    </table></div>
    <p style="font-size:10px;color:{MUTED}">Broker leverage is not assumed. Quantity is not calculated. Signals only.</p>"""
