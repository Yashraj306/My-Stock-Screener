from html import escape

NAVY = "#101D35"
GREEN = "#059669"
RED = "#DC2626"
GOLD = "#D69E36"
MUTED = "#64748B"
BORDER = "#E5EAF2"


def esc(value, default="—"):
    if value is None or value == "":
        value = default
    return escape(str(value))


def fmt_inr(value, decimals=2):
    if value is None or value == "":
        return "—"
    try:
        num = float(value)
        return f"₹{num:,.{decimals}f}"
    except (TypeError, ValueError):
        return esc(value)


def _render_rationale_and_entry_box(item: dict, is_bull: bool = True) -> str:
    why_trade = item.get("why_trade")
    ext_status = item.get("extension_status")
    entry_action = item.get("entry_action")
    be_low = item.get("best_entry_low")
    be_high = item.get("best_entry_high")

    if not any([why_trade, ext_status, entry_action, be_low is not None, be_high is not None]):
        return ""

    action_color = GOLD if "WAIT" in str(entry_action or "").upper() else (GREEN if is_bull else RED)
    zone_str = (
        f"{fmt_inr(be_low)} – {fmt_inr(be_high)}"
        if (be_low not in (None, "") and be_high not in (None, ""))
        else "—"
    )

    return f"""
    <div style="margin-top:8px;padding:8px 10px;background:#FFFDF7;border:1px solid #FDE68A;border-radius:6px;font-size:10.5px;line-height:1.65;color:{NAVY};">
      {f'<div><b>Why This Trade:</b> {esc(why_trade)}</div>' if why_trade else ''}
      <div style="margin-top:2px;">
        <b>Timing / Extension:</b> {esc(ext_status)} &nbsp;|&nbsp;
        <b>Entry Plan:</b> <span style="color:{action_color};font-weight:800;">{esc(entry_action)}</span>
      </div>
      <div style="margin-top:2px;">
        <b>Best Entry Zone (Pullback):</b> <b>{esc(zone_str)}</b>
      </div>
    </div>
    """


def _render_levels_and_sizing_box(item: dict, is_bull: bool = True) -> str:
    entry = item.get("entry") or item.get("close")
    sl = item.get("stop_loss")
    pa_t1 = item.get("pa_target_1") if item.get("pa_target_1") is not None else item.get("target_1")
    pa_t2 = item.get("pa_target_2") if item.get("pa_target_2") is not None else item.get("target_2")
    oi_target = item.get("oi_target")

    sizing_status = str(item.get("sizing_status") or "").strip()
    qty = item.get("qty")
    est_inv = item.get("est_margin") if item.get("est_margin") is not None else item.get("est_investment")
    exposure = item.get("exposure")
    max_risk = item.get("max_risk")

    if sizing_status and ("NOT CONFIGURED" in sizing_status.upper() or "UNAVAILABLE" in sizing_status.upper()):
        sizing_line = f"<b>Investment / Sizing:</b> {esc(sizing_status)}"
    else:
        qty_str = f"{int(float(qty))}" if qty not in (None, "") else "—"
        inv_str = fmt_inr(est_inv) if est_inv not in (None, "") else "—"
        exp_str = fmt_inr(exposure) if exposure not in (None, "") else "—"
        risk_str = fmt_inr(max_risk) if max_risk not in (None, "") else "—"
        sizing_line = (
            f"<b>Est. Investment / Margin:</b> {esc(inv_str)} &nbsp;|&nbsp; "
            f"<b>Qty:</b> {esc(qty_str)} &nbsp;|&nbsp; "
            f"<b>Exposure:</b> {esc(exp_str)} &nbsp;|&nbsp; "
            f"<b>Max Risk:</b> {esc(risk_str)}"
        )

    oi_label = fmt_inr(oi_target) if oi_target not in (None, "") else "Live chain unavailable"

    return f"""
    <div style="margin-top:8px;padding:8px 10px;background:#F8FAFC;border:1px solid {BORDER};border-radius:6px;font-size:10.5px;line-height:1.7;color:{NAVY};">
      <div>
        <b>Entry:</b> {esc(fmt_inr(entry))} &nbsp;|&nbsp;
        <b>Stop Loss (SL):</b> <span style="color:{RED};font-weight:700;">{esc(fmt_inr(sl))}</span>
      </div>
      <div>
        <b>PA Targets:</b> T1 {esc(fmt_inr(pa_t1))} &nbsp;|&nbsp; T2 {esc(fmt_inr(pa_t2))} &nbsp;|&nbsp;
        <b>OI Target:</b> <span style="color:{GREEN if is_bull else RED};font-weight:700;">{esc(oi_label)}</span>
      </div>
      <div style="margin-top:3px;color:#334155;">
        {sizing_line}
      </div>
    </div>
    """


def render_intraday_card(item: dict, bullish: bool = True) -> str:
    accent = GREEN if bullish else RED
    sym = esc(item.get("symbol"))
    sector = esc(item.get("sector", "EQUITY"))
    action = esc(item.get("action") or ("BUY (LONG)" if bullish else "SELL (SHORT)"))
    score = item.get("score")
    score_str = f"Score {float(score):.1f}/100" if score is not None else ""
    rsi = item.get("rsi")
    vol_ratio = item.get("vol_ratio")
    vwap = item.get("vwap")
    rs_score = item.get("rs_score")

    meta_parts = [p for p in [
        f"RSI {float(rsi):.1f}" if rsi not in (None, "") else None,
        f"Vol {float(vol_ratio):.2f}x" if vol_ratio not in (None, "") else None,
        f"VWAP {fmt_inr(vwap)}" if vwap not in (None, "") else None,
        f"RS {float(rs_score):+.2f}%" if rs_score not in (None, "") else None,
    ] if p]

    return f"""
    <div style="background:#FFFFFF;border:1px solid {BORDER};border-left:4px solid {accent};border-radius:8px;padding:11px 12px;margin-bottom:9px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td>
            <span style="font-size:13px;font-weight:800;color:{NAVY};">{sym}</span>
            <span style="font-size:10px;color:{MUTED};margin-left:6px;">{sector}</span>
          </td>
          <td align="right">
            <span style="font-size:10px;font-weight:800;color:{accent};">{action}</span>
            {f'<span style="font-size:10px;color:{MUTED};margin-left:8px;">{esc(score_str)}</span>' if score_str else ''}
          </td>
        </tr>
      </table>
      {_render_rationale_and_entry_box(item, is_bull=bullish)}
      {_render_levels_and_sizing_box(item, is_bull=bullish)}
      {f'<div style="margin-top:6px;font-size:10px;color:{MUTED};">{" &nbsp;|&nbsp; ".join(esc(x) for x in meta_parts)}</div>' if meta_parts else ''}
    </div>
    """


def render_option_card(item: dict) -> str:
    side_raw = str(item.get("side") or "").upper()
    is_bull = ("PUT" not in side_raw) and ("PE" not in side_raw) and ("BEAR" not in side_raw)
    accent = GREEN if is_bull else RED
    sym = esc(item.get("symbol"))
    spot = item.get("spot") or item.get("close")
    chg = item.get("change_pct")
    chg_str = f" ({float(chg):+.2f}%)" if chg not in (None, "") else ""

    plan = item.get("option_plan") or item.get("hedge_plan") or {}
    strategy = plan.get("strategy") or "UNAVAILABLE"
    expiry = plan.get("expiry") or "—"
    lot_size = int(plan.get("lot_size") or 0)

    if plan.get("available"):
        if "SPREAD" in str(strategy).upper():
            b_leg = plan.get("buy_leg") or {}
            s_leg = plan.get("sell_leg") or {}
            debit = plan.get("net_debit_per_unit")
            max_loss_lot = plan.get("max_loss_lot")
            max_prof_lot = plan.get("max_profit_lot")
            be = plan.get("breakeven")
            plan_html = (
                f"<div style='font-size:10.5px;margin-top:6px;line-height:1.65;'>"
                f"<b>Strategy:</b> {esc(strategy)} &nbsp;|&nbsp; <b>Expiry:</b> {esc(expiry)} &nbsp;|&nbsp; <b>Lot:</b> {lot_size}<br>"
                f"<b>Buy:</b> {esc(b_leg.get('strike'))} ({esc(fmt_inr(b_leg.get('ltp')))}) &nbsp;|&nbsp; "
                f"<b>Sell:</b> {esc(s_leg.get('strike'))} ({esc(fmt_inr(s_leg.get('ltp')))}) &nbsp;|&nbsp; "
                f"<b>Net Debit:</b> {esc(fmt_inr(debit))}<br>"
                f"<b>Breakeven:</b> {esc(fmt_inr(be))} &nbsp;|&nbsp; "
                f"<b>Max Risk (1 Lot):</b> {esc(fmt_inr(max_loss_lot))} &nbsp;|&nbsp; "
                f"<b>Max Profit (1 Lot):</b> {esc(fmt_inr(max_prof_lot))}"
                f"</div>"
            )
        else:
            strike = plan.get("strike")
            prem = plan.get("entry_premium")
            max_loss_lot = plan.get("max_loss_lot")
            plan_html = (
                f"<div style='font-size:10.5px;margin-top:6px;line-height:1.65;'>"
                f"<b>Strategy:</b> {esc(strategy)} &nbsp;|&nbsp; <b>Strike:</b> {esc(strike)} &nbsp;|&nbsp; "
                f"<b>Expiry:</b> {esc(expiry)} &nbsp;|&nbsp; <b>Lot:</b> {lot_size}<br>"
                f"<b>Premium:</b> {esc(fmt_inr(prem))} &nbsp;|&nbsp; <b>Max Loss (1 Lot):</b> {esc(fmt_inr(max_loss_lot))}"
                f"</div>"
            )
    else:
        reason = plan.get("reason") or "Option chain data unavailable."
        plan_html = (
            f"<div style='font-size:10.5px;margin-top:6px;color:{GOLD};'>"
            f"<b>Option Plan:</b> UNAVAILABLE ({esc(reason)})"
            f"</div>"
        )

    chain = item.get("chain_summary") or {}
    chain_line = ""
    if chain:
        chain_line = (
            f"<div style='margin-top:5px;font-size:10px;color:{MUTED};'>"
            f"PCR {esc(chain.get('pcr'))} &nbsp;|&nbsp; "
            f"Sup {esc(chain.get('support_strike'))} &nbsp;|&nbsp; "
            f"Res {esc(chain.get('resistance_strike'))} &nbsp;|&nbsp; "
            f"MaxPain {esc(chain.get('max_pain'))} &nbsp;|&nbsp; "
            f"{esc(chain.get('oi_build_up'))}"
            f"</div>"
        )

    return f"""
    <div style="background:#FFFFFF;border:1px solid {BORDER};border-left:4px solid {accent};border-radius:8px;padding:11px 12px;margin-bottom:9px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td>
            <span style="font-size:13px;font-weight:800;color:{NAVY};">{sym}</span>
            <span style="font-size:10.5px;color:{MUTED};margin-left:6px;">Spot {esc(fmt_inr(spot))}{esc(chg_str, "")}</span>
          </td>
          <td align="right">
            <span style="font-size:10px;font-weight:800;color:{accent};">{'BUY CALL (CE)' if is_bull else 'BUY PUT (PE)'}</span>
          </td>
        </tr>
      </table>
      {_render_rationale_and_entry_box(item, is_bull=is_bull)}
      {plan_html}
      {_render_levels_and_sizing_box(item, is_bull=is_bull)}
      {chain_line}
    </div>
    """
 