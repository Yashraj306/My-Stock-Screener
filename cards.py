def make_checklist_html(checks):
    items = [
        ("Trend", checks.get("trend", False)),
        ("VWAP / Level", checks.get("level", False)),
        ("Compression", checks.get("compression", False)) if "compression" in checks else ("Volume", checks.get("volume", False)),
        ("Options / OI", checks.get("options", False)),
        ("Sector", checks.get("sector", False)),
        ("Rel. strength", checks.get("rs", False)),
    ]
    html = '<div style="margin: 8px 0; font-size: 12px; color: #333;">'
    for label, passed in items:
        sym = "&#10004;" if passed else "&#10008;"
        col = "#00873c" if passed else "#888888"
        html += f'<span style="margin-right: 12px;"><b style="color: {col};">{sym}</b> {label}</span>'
    html += '</div>'
    return html


def build_stock_card(idea):
    sym = idea["symbol"]
    side = idea["side"]
    setup_kind = idea["setup_kind"]
    grade = idea.get("grade", "B")
    sec = idea.get("sector", "OTHER")
    close = idea["close"]
    chg = idea.get("change_pct", 0.0)
    chg_sign = "+" if chg >= 0 else ""
    rsi = idea.get("rsi", 50)
    vol = idea.get("vol_ratio", 1.0)
    atr_p = idea.get("atr_pct", 2.0)

    u_entry = idea["entry"]
    u_sl = idea["stop_loss"]
    u_t1 = idea["target_1"]
    u_t2 = idea["target_2"]
    rr = idea.get("rr", 1.5)

    checklist_html = make_checklist_html(idea.get("checks", {}))
    bullets = "".join([f"<li>{r}</li>" for r in idea.get("reasons", [])])

    opt = idea.get("opt_chain")
    strike = idea.get("strike")

    opt_box = ""
    if opt and strike:
        opt_box = f"""
        <div style="background: #f7f9fa; border: 1px solid #e1e8ed; border-radius: 6px; padding: 10px; margin-top: 10px; font-size: 12px; line-height: 1.5;">
            <div style="font-weight: bold; color: #1c2833; margin-bottom: 4px;">
                Option chain ({opt.get('source', 'Upstox')}, expiry {opt.get('expiry', '-')}, {opt.get('days_to_exp', '-')} days)
            </div>
            <div>
                PCR {opt.get('pcr', '0.0')} (Intraday PCR: <b>{opt.get('intraday_pcr', '1.0')}</b>) | OI support {opt.get('support', '-')} | OI resistance {opt.get('resistance', '-')} | Max pain {opt.get('max_pain', '-')} | ATM IV {opt.get('atm_iv', 0.0)}<br>
                OI action: <b>{opt.get('oi_action', 'Neutral')}</b> | OI bias: <b>{opt.get('oi_bias', 'NEUTRAL')}</b>
            </div>
            <div style="margin-top: 6px; padding-top: 6px; border-top: 1px dashed #d5dbdb;">
                <b>Best strike: {strike.get('strike_name')}</b> | Premium {strike.get('ltp', 0.0)} | OI {strike.get('oi', 0):,} ({strike.get('oi_chg', '+0.0%')}) | Volume {strike.get('volume', 0):,} | IV {strike.get('iv', 0.0)}<br>
                Delta {strike.get('delta', 0.0)} | Theta {strike.get('theta', 0.0)}/day ({strike.get('theta_pct', '0.0%')} of premium) | Gamma {strike.get('gamma', 0.0)} | Vega {strike.get('vega', 0.0)}<br>
                <b>Premium plan (approx.):</b> SL ~{strike.get('sl', 0.0)} | T1 ~{strike.get('t1', 0.0)} | R:R {rr} (delta-based)<br>
                <i>Position execution advice:</i> {strike.get('position_text')}
            </div>
        </div>
        """

    card_html = f"""
    <div style="border: 1px solid #dcdde1; border-radius: 8px; padding: 14px; margin-bottom: 16px; background: #ffffff;">
        <div style="display: flex; justify-content: space-between; align-items: flex-start;">
            <div>
                <span style="font-size: 17px; font-weight: bold; color: #2c3e50;">{sym} {side}</span><br>
                <span style="font-size: 12px; color: #7f8c8d;">{setup_kind} | Sector: {sec} | Close {close:,.2f} ({chg_sign}{chg:.2f}%) | RSI {rsi:.0f} | Vol {vol:.1f}x | ATR {atr_p:.1f}%</span>
            </div>
            <div style="background: #2c3e50; color: #ffffff; padding: 3px 10px; border-radius: 4px; font-weight: bold; font-size: 13px;">
                Grade {grade}
            </div>
        </div>

        <table style="width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 12px; text-align: center;">
            <tr style="background: #f1f2f6; color: #57606f; font-weight: 600;">
                <td style="padding: 6px; border: 1px solid #e4e7ea;">Entry (ref.)</td>
                <td style="padding: 6px; border: 1px solid #e4e7ea;">Stop-loss</td>
                <td style="padding: 6px; border: 1px solid #e4e7ea;">Target 1</td>
                <td style="padding: 6px; border: 1px solid #e4e7ea;">Target 2</td>
                <td style="padding: 6px; border: 1px solid #e4e7ea;">R:R (T1)</td>
            </tr>
            <tr style="font-weight: bold; color: #2f3542;">
                <td style="padding: 6px; border: 1px solid #e4e7ea;">{u_entry:,.2f}</td>
                <td style="padding: 6px; border: 1px solid #e4e7ea; color: #eb4d4b;">{u_sl:,.2f}</td>
                <td style="padding: 6px; border: 1px solid #e4e7ea; color: #20bf6b;">{u_t1:,.2f}</td>
                <td style="padding: 6px; border: 1px solid #e4e7ea; color: #20bf6b;">{u_t2:,.2f}</td>
                <td style="padding: 6px; border: 1px solid #e4e7ea;">{rr:.2f}</td>
            </tr>
        </table>
        <div style="font-size: 10px; color: #888; margin-top: 3px;">Levels are on the underlying and ATR-based. They are a plan, not a prediction.</div>

        {checklist_html}

        <div style="font-size: 12px; color: #333; margin-top: 6px;">
            <b>Why this setup</b>
            <ul style="margin: 4px 0 0 16px; padding: 0;">
                {bullets}
            </ul>
        </div>

        {opt_box}
    </div>
    """
    return card_html
