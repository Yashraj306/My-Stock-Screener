from html import escape

from config import ALLOW_MIS_LEVERAGE, CAPITAL, CAPITAL_CONFIGURED, MIS_LEVERAGE


def _esc(value, default="—"):
    if value is None or value == "":
        value = default
    return escape(str(value))


def _rupee(value):
    try:
        if value is None or value == "":
            return "—"
        return "₹" + f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _leverage_label(item: dict) -> str:
    enabled = item.get("leverage_enabled")
    if enabled is None:
        enabled = ALLOW_MIS_LEVERAGE
    mult = item.get("leverage_used")
    if mult is None:
        mult = MIS_LEVERAGE if enabled else 1.0
    try:
        mult_val = float(mult)
    except (TypeError, ValueError):
        mult_val = 1.0
    if enabled and mult_val > 1.0:
        return f"MIS {mult_val:g}x Leverage Assumed"
    return "1x Cash / No Leverage Assumed"


def _sizing_summary_line(item: dict) -> str:
    lev_text = _leverage_label(item)
    qty = item.get("qty")
    status = str(item.get("sizing_status") or "")
    try:
        qty_int = int(qty) if qty is not None else 0
    except (TypeError, ValueError):
        qty_int = 0

    if not CAPITAL_CONFIGURED or (CAPITAL is not None and float(CAPITAL) <= 0.0) or status == "NOT_CALCULATED":
        return f"{lev_text} &nbsp;|&nbsp; Qty: Not calculated (capital not configured)"

    if qty_int <= 0:
        max_risk = _rupee(item.get("max_risk_rs"))
        return f"{lev_text} &nbsp;|&nbsp; Qty: 0 under risk cap (Risk budget: {max_risk})"

    exposure = _rupee(item.get("notional_exposure"))
    margin = _rupee(item.get("margin_required"))
    risk_rs = _rupee(item.get("max_risk_rs"))
    return (
        f"{lev_text} &nbsp;|&nbsp; Qty: <b>{qty_int}</b> "
        f"&nbsp;|&nbsp; Exposure: <b>{exposure}</b> "
        f"&nbsp;|&nbsp; Est. Margin: <b>{margin}</b> "
        f"&nbsp;|&nbsp; Max Risk: <b>{risk_rs}</b>"
    )


def make_checklist_html(checks: dict | None) -> str:
    checks = checks or {}
    items = [
        ("Trend", bool(checks.get("trend", False))),
        ("VWAP / Level", bool(checks.get("level", False))),
        ("Volume", bool(checks.get("volume", False))),
        ("Options / OI", bool(checks.get("options", False))),
        ("Sector", bool(checks.get("sector", False))),
        ("Rel. Strength", bool(checks.get("rs", False))),
    ]
    parts = []
    for label, passed in items:
        mark = "&#10004;" if passed else "&#10008;"
        color = "#059669" if passed else "#94A3B8"
        parts.append(f'<span style="margin-right:10px;"><b style="color:{color};">{mark}</b> {_esc(label)}</span>')
    return f'<div style="margin:8px 0;font-size:11px;color:#334155;">{"".join(parts)}</div>'


def render_intraday_card(item: dict, bullish: bool = True) -> str:
    accent = "#059669" if bullish else "#DC2626"
    badge_bg = "#ECFDF5" if bullish else "#FEF2F2"
    side_text = _esc(item.get("action") or ("BUY (LONG)" if bullish else "SELL (SHORT)"))

    entry = item.get("entry")
    sl = item.get("stop_loss") if item.get("stop_loss") is not None else item.get("sl")
    t1 = item.get("target_1") if item.get("target_1") is not None else item.get("t1")
    t2 = item.get("target_2") if item.get("target_2") is not None else item.get("t2")
    rsi_val = item.get("rsi")
    vr_val = item.get("volume_ratio") if item.get("volume_ratio") is not None else item.get("vr")
    vwap_val = item.get("vwap")
    score_val = item.get("score")

    vr_display = f"{float(vr_val):.2f}x" if vr_val not in (None, "") else "—"
    score_display = f"Score {float(score_val):.1f}/100" if score_val not in (None, "") else ""

    return f"""
    <div style="background:#FFFFFF;border:1px solid #E5EAF2;border-left:4px solid {accent};border-radius:9px;padding:14px;margin-bottom:10px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td>
            <div style="font-size:16px;font-weight:900;color:#101D35;">{_esc(item.get("symbol"))}</div>
            <div style="font-size:10px;color:#64748B;margin-top:2px;">
              {_esc(item.get("sector", "Equity"))} &nbsp;|&nbsp; {_esc(item.get("index_tag", "NSE/BSE"))}
              {f' &nbsp;|&nbsp; <b>{_esc(score_display)}</b>' if score_display else ''}
            </div>
          </td>
          <td align="right" valign="top">
            <span style="display:inline-block;padding:4px 9px;border-radius:16px;background:{badge_bg};color:{accent};font-size:10px;font-weight:800;">
              {side_text}
            </span>
          </td>
        </tr>
      </table>

      <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:11px;">
        <tr>
          <td width="33%" style="padding:7px;background:#F8FAFC;border-radius:5px;">
            <div style="font-size:9px;color:#64748B;">ENTRY</div>
            <b style="font-size:13px;color:#101D35;">{_rupee(entry)}</b>
          </td>
          <td width="33%" style="padding:7px;background:#FEF2F2;border-radius:5px;">
            <div style="font-size:9px;color:#DC2626;">STOP LOSS</div>
            <b style="font-size:13px;color:#DC2626;">{_rupee(sl)}</b>
          </td>
          <td width="33%" style="padding:7px;background:#ECFDF5;border-radius:5px;">
            <div style="font-size:9px;color:#059669;">TARGET 1</div>
            <b style="font-size:13px;color:#059669;">{_rupee(t1)}</b>
          </td>
        </tr>
      </table>

      <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:8px;">
        <tr>
          <td style="font-size:10px;color:#64748B;">TARGET 2<br><b style="font-size:11px;color:#101D35;">{_rupee(t2)}</b></td>
          <td style="font-size:10px;color:#64748B;">RSI<br><b style="font-size:11px;color:#101D35;">{_esc(rsi_val)}</b></td>
          <td style="font-size:10px;color:#64748B;">VOL RATIO<br><b style="font-size:11px;color:#101D35;">{_esc(vr_display)}</b></td>
          <td style="font-size:10px;color:#64748B;">VWAP<br><b style="font-size:11px;color:#101D35;">{_rupee(vwap_val)}</b></td>
        </tr>
      </table>

      <div style="margin-top:10px;padding-top:8px;border-top:1px solid #E5EAF2;font-size:10px;color:#64748B;">
        {_sizing_summary_line(item)}
      </div>
    </div>
    """


def render_option_card(item: dict) -> str:
    plan = item.get("option_plan") or item.get("hedge_plan") or {}
    chain = item.get("opt_chain") or {}
    side = str(item.get("side") or "")
    is_bull = ("CALL" in side.upper()) or ("CE" in side.upper()) or ("BULL" in str(plan.get("strategy") or "").upper())
    accent = "#059669" if is_bull else "#DC2626"
    badge_bg = "#ECFDF5" if is_bull else "#FEF2F2"

    strategy = str(plan.get("strategy") or "UNAVAILABLE")
    lot_size = plan.get("lot_size")
    lot_text = f"Lot Size: {int(lot_size)}" if lot_size not in (None, "", 0) else "Lot Size: —"

    if plan.get("available") and strategy in ("BULL_CALL_SPREAD", "BEAR_PUT_SPREAD"):
        buy = plan.get("buy_leg") or {}
        sell = plan.get("sell_leg") or {}
        lot_loss = _rupee(plan.get("max_loss_lot")) if plan.get("max_loss_lot") is not None else "Not calculated"
        lot_profit = _rupee(plan.get("max_profit_lot")) if plan.get("max_profit_lot") is not None else "Not calculated"
        strategy_html = f"""
        <div style="background:#F8FAFC;border:1px solid #E5EAF2;border-radius:6px;padding:9px;margin-top:8px;font-size:11px;line-height:1.6;">
          <div><b>Strategy:</b> {_esc(strategy.replace("_", " "))} &nbsp;|&nbsp; <b>Expiry:</b> {_esc(plan.get("expiry"))} &nbsp;|&nbsp; {_esc(lot_text)}</div>
          <div><b>Buy Leg:</b> {_esc(buy.get("strike"))} ({_rupee(buy.get("ltp"))}) &nbsp;|&nbsp; <b>Sell Leg:</b> {_esc(sell.get("strike"))} ({_rupee(sell.get("ltp"))})</div>
          <div><b>Net Debit/Unit:</b> {_rupee(plan.get("net_debit_per_unit"))} &nbsp;|&nbsp; <b>Breakeven:</b> {_rupee(plan.get("breakeven"))}</div>
          <div><b>Max Risk/Unit:</b> <span style="color:#DC2626;font-weight:700;">{_rupee(plan.get("maximum_loss_per_unit"))}</span> (Lot: {lot_loss}) &nbsp;|&nbsp; <b>Max Profit/Unit:</b> <span style="color:#059669;font-weight:700;">{_rupee(plan.get("maximum_profit_per_unit"))}</span> (Lot: {lot_profit})</div>
        </div>
        """
    elif plan.get("available") and strategy == "OPTION_BUYING":
        lot_loss = _rupee(plan.get("max_loss_lot")) if plan.get("max_loss_lot") is not None else "Not calculated"
        strategy_html = f"""
        <div style="background:#F8FAFC;border:1px solid #E5EAF2;border-radius:6px;padding:9px;margin-top:8px;font-size:11px;line-height:1.6;">
          <div><b>Strategy:</b> OPTION BUYING ({_esc(plan.get("option_type"))}) &nbsp;|&nbsp; <b>Strike:</b> {_esc(plan.get("strike"))} &nbsp;|&nbsp; <b>Expiry:</b> {_esc(plan.get("expiry"))} &nbsp;|&nbsp; {_esc(lot_text)}</div>
          <div><b>Entry Premium:</b> {_rupee(plan.get("entry_premium"))} &nbsp;|&nbsp; <b>IV:</b> {_esc(plan.get("iv"))}% &nbsp;|&nbsp; <b>Delta:</b> {_esc(plan.get("delta"))} &nbsp;|&nbsp; <b>Theta:</b> {_esc(plan.get("theta"))}</div>
          <div><b>Max Loss/Unit:</b> <span style="color:#DC2626;font-weight:700;">{_rupee(plan.get("maximum_loss_per_unit"))}</span> (Lot: {lot_loss})</div>
        </div>
        """
    else:
        reason = plan.get("reason") or "Live option-chain contract data not available."
        strategy_html = f"""
        <div style="background:#FFFBEB;border:1px solid #FDE68A;border-radius:6px;padding:8px;margin-top:8px;font-size:11px;color:#92400E;">
          <b>Option Plan Unavailable:</b> {_esc(reason)}
        </div>
        """

    chain_html = ""
    if chain:
        chain_html = f"""
        <div style="font-size:10px;color:#64748B;margin-top:6px;">
          <b>Option Chain:</b> PCR {_esc(chain.get("oi_pcr"))} | Support {_esc(chain.get("support_strike"))} ({_esc(chain.get("support_oi_pct"))}% OI) | Resistance {_esc(chain.get("resistance_strike"))} ({_esc(chain.get("resistance_oi_pct"))}% OI) | Max Pain {_esc(chain.get("max_pain"))} | {_esc(chain.get("oi_quadrant"))}
        </div>
        """

    reasons = item.get("reasons") or []
    reasons_html = "".join(f"<li>{_esc(r)}</li>" for r in reasons)

    return f"""
    <div style="background:#FFFFFF;border:1px solid #E5EAF2;border-left:4px solid {accent};border-radius:9px;padding:14px;margin-bottom:11px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td>
            <div style="font-size:16px;font-weight:900;color:#101D35;">{_esc(item.get("symbol"))}</div>
            <div style="font-size:10px;color:#64748B;margin-top:2px;">
              Spot: <b>{_rupee(item.get("close"))}</b> ({_esc(item.get("change_pct"))}%) &nbsp;|&nbsp; Sector: {_esc(item.get("sector"))}
            </div>
          </td>
          <td align="right" valign="top">
            <span style="display:inline-block;padding:4px 9px;border-radius:16px;background:{badge_bg};color:{accent};font-size:10px;font-weight:800;">
              {_esc(item.get("side"))}
            </span>
          </td>
        </tr>
      </table>

      {make_checklist_html(item.get("checks"))}
      {strategy_html}
      {chain_html}

      {f'<ul style="margin:7px 0 0 16px;padding:0;font-size:10.5px;color:#475569;">{reasons_html}</ul>' if reasons_html else ''}
    </div>
    """


def build_stock_card(idea: dict) -> str:
    return render_option_card(idea)
