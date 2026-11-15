def make_checklist_html(checks):
    items = [
        ("Trend", checks.get("trend", False)),
        ("VWAP / Level", checks.get("level", False)),
        ("Volume", checks.get("volume", False)),
        ("Options / OI", checks.get("options", False)),
        ("Sector", checks.get("sector", False)),
        ("Rel. Strength", checks.get("rs", False)),
    ]
    html = '<div style="margin: 8px 0; font-size: 11px; color: #334155;">'
    for label, passed in items:
        sym = "&#10004;" if passed else "&#10008;"
        col = "#10b981" if passed else "#94a3b8"
        html += f'<span style="margin-right: 12px;"><b style="color: {col};">{sym}</b> {label}</span>'
    html += '</div>'
    return html


def build_stock_card(idea):
    sym = idea["symbol"]
    side = idea["side"]
    setup_kind = idea["setup_kind"]
    grade = idea.get("grade", "A")
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
    bullets = "".join([f"<li style='margin-bottom: 2px;'>{r}</li>" for r in idea.get("reasons", [])])

    opt = idea.get("opt_chain")
    strike = idea.get("strike")
    hedge = idea.get("hedge_plan")

    is_call = "CALL" in side
    badge_bg = "#065f46" if is_call else "#991b1b"
    border_accent = "#10b981" if is_call else "#ef4444"

    hedge_html = ""
    if hedge:
        hedge_html = f"""
        <div style="background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 6px; padding: 10px; margin-top: 10px; font-size: 11.5px;">
            <div style="font-weight: 700; color: #166534; font-size: 12px; margin-bottom: 6px;">
                🛡️ Pre-Built Option Hedging Strategy: {hedge['strategy_name']} (Lot Size: {hedge['lot_size']})
            </div>
            <table style="width: 100%; border-collapse: collapse; background: #ffffff; font-size: 11px; text-align: center; margin-bottom: 6px;">
                <tr style="background: #dcfce7; color: #14532d; font-weight: bold;">
                    <td style="padding: 5px; border: 1px solid #bbf7d0;">Action</td>
                    <td style="padding: 5px; border: 1px solid #bbf7d0;">Strike Leg</td>
                    <td style="padding: 5px; border: 1px solid #bbf7d0;">Est. Premium</td>
                    <td style="padding: 5px; border: 1px solid #bbf7d0;">Role</td>
                </tr>
                <tr>
                    <td style="padding: 5px; border: 1px solid #bbf7d0; color: #15803d; font-weight: bold;">BUY (Long)</td>
                    <td style="padding: 5px; border: 1px solid #bbf7d0; font-weight: bold;">{hedge['buy_leg']}</td>
                    <td style="padding: 5px; border: 1px solid #bbf7d0;">₹{hedge['buy_prem']}</td>
                    <td style="padding: 5px; border: 1px solid #bbf7d0;">Core Directional Delta</td>
                </tr>
                <tr>
                    <td style="padding: 5px; border: 1px solid #bbf7d0; color: #b91c1c; font-weight: bold;">SELL (Short)</td>
                    <td style="padding: 5px; border: 1px solid #bbf7d0; font-weight: bold;">{hedge['sell_leg']}</td>
                    <td style="padding: 5px; border: 1px solid #bbf7d0;">₹{hedge['sell_prem']}</td>
                    <td style="padding: 5px; border: 1px solid #bbf7d0;">Theta & IV Crush Hedge</td>
                </tr>
            </table>
            <div style="display: flex; justify-content: space-between; color: #1e293b; font-size: 11px; line-height: 1.4;">
                <span><b>Net Debit:</b> ₹{hedge['net_debit']} pts | <b>Breakeven:</b> {hedge['breakeven']} | <b>Max Risk/Lot:</b> <span style="color:#dc2626; font-weight:bold;">₹{hedge['max_loss']:,}</span> | <b>Max Profit/Lot:</b> <span style="color:#16a34a; font-weight:bold;">₹{hedge['max_profit']:,}</span></span>
            </div>
        </div>
        """

    opt_box = ""
    if opt and strike:
        data_tag = '<span style="color:#059669;">[● Upstox Live]</span>' if not strike.get("is_synthetic") else '<span style="color:#d97706;">[● BSM Quant Model]</span>'
        opt_box = f"""
        <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px; margin-top: 8px; font-size: 11.5px; line-height: 1.5; color: #334155;">
            <div style="font-weight: bold; color: #0f172a; margin-bottom: 3px;">
                Naked Option Contract {data_tag} | Expiry: {opt.get('expiry', 'Current')}
            </div>
            <div>
                PCR: <b>{opt.get('pcr', '1.0')}</b> (Intraday PCR: <b>{opt.get('intraday_pcr', '1.0')}</b>) | OI Support: {opt.get('support', '-')} | OI Resistance: {opt.get('resistance', '-')} | Max Pain: {opt.get('max_pain', '-')}<br>
                <b>Single Leg Strike: {strike.get('strike_name')}</b> @ ₹{strike.get('ltp', 0.0)} | Delta {strike.get('delta', 0.5)} | Theta {strike.get('theta', 0.0)}/day | IV {strike.get('iv', 20.0)}% | <b>SL: ₹{strike.get('sl')} | T1: ₹{strike.get('t1')}</b>
            </div>
        </div>
        """

    tv_url = f"https://in.tradingview.com/chart/?symbol=NSE%3A{sym}"

    return f"""
    <div style="border: 1px solid #cbd5e1; border-left: 5px solid {border_accent}; border-radius: 8px; padding: 14px; margin-bottom: 16px; background: #ffffff; box-shadow: 0 1px 3px rgba(0,0,0,0.04);">
        <div style="display: flex; justify-content: space-between; align-items: flex-start;">
            <div>
                <span style="font-size: 16px; font-weight: 800; color: #0f172a;">{sym}</span>
                <span style="background: {badge_bg}; color: #ffffff; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; margin-left: 6px;">{side}</span>
                <a href="{tv_url}" style="font-size: 11px; color: #2563eb; text-decoration: none; margin-left: 8px; font-weight: 600;">[📊 Chart ↗]</a><br>
                <span style="font-size: 11.5px; color: #64748b;">{setup_kind} | Sector: <b>{sec}</b> | Spot: <b>₹{close:,.2f} ({chg_sign}{chg:.2f}%)</b> | RSI {rsi:.0f} | Vol {vol:.1f}x | ATR {atr_p:.1f}%</span>
            </div>
            <div style="background: #1e293b; color: #f8fafc; padding: 3px 10px; border-radius: 4px; font-weight: bold; font-size: 12px;">
                Grade {grade}
            </div>
        </div>

        <table style="width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 11.5px; text-align: center;">
            <tr style="background: #f1f5f9; color: #475569; font-weight: 600;">
                <td style="padding: 6px; border: 1px solid #e2e8f0;">Spot Entry</td>
                <td style="padding: 6px; border: 1px solid #e2e8f0;">Spot Stop-Loss</td>
                <td style="padding: 6px; border: 1px solid #e2e8f0;">Spot Target 1</td>
                <td style="padding: 6px; border: 1px solid #e2e8f0;">Spot Target 2</td>
                <td style="padding: 6px; border: 1px solid #e2e8f0;">R:R</td>
            </tr>
            <tr style="font-weight: bold; color: #0f172a;">
                <td style="padding: 6px; border: 1px solid #e2e8f0;">₹{u_entry:,.2f}</td>
                <td style="padding: 6px; border: 1px solid #e2e8f0; color: #dc2626;">₹{u_sl:,.2f}</td>
                <td style="padding: 6px; border: 1px solid #e2e8f0; color: #16a34a;">₹{u_t1:,.2f}</td>
                <td style="padding: 6px; border: 1px solid #e2e8f0; color: #16a34a;">₹{u_t2:,.2f}</td>
                <td style="padding: 6px; border: 1px solid #e2e8f0;">1:{rr:.1f}</td>
            </tr>
        </table>

        {checklist_html}

        <div style="font-size: 11.5px; color: #334155; margin-top: 4px;">
            <ul style="margin: 4px 0 0 16px; padding: 0;">{bullets}</ul>
        </div>

        {hedge_html}
        {opt_box}
    </div>
    """


def build_equity_intraday_table(eq_signals, margin_used=20000):
    """Renders 5x Leverage Intraday Equity (MIS) Buy & Sell signals."""
    if not eq_signals:
        return "<div style='font-size:12px; color:#64748b;'>No high-conviction intraday equity signals found.</div>"

    rows_html = ""
    for s in eq_signals:
        is_buy = s["action"] == "BUY (LONG)"
        act_col = "#15803d" if is_buy else "#b91c1c"
        act_bg = "#dcfce7" if is_buy else "#fee2e2"
        rows_html += f"""
        <tr style="border-bottom: 1px solid #e2e8f0;">
            <td style="padding: 7px 6px; font-weight: bold; color: #0f172a;">{s['symbol']}<br><span style="font-size:9.5px; color:#64748b; font-weight:normal;">{s['sector']} | {s['index_tag']}</span></td>
            <td style="padding: 7px 6px; text-align: center;"><span style="background:{act_bg}; color:{act_col}; padding: 2px 6px; border-radius: 4px; font-weight: bold; font-size: 10.5px;">{s['action']}</span></td>
            <td style="padding: 7px 6px; text-align: right; font-weight: bold;">₹{s['entry']:,.2f}<br><span style="font-size:9.5px; color:{'#16a34a' if s['chg']>=0 else '#dc2626'};">{s['chg']:+.2f}%</span></td>
            <td style="padding: 7px 6px; text-align: right; color: #dc2626; font-weight: bold;">₹{s['sl']:,.2f}</td>
            <td style="padding: 7px 6px; text-align: right; color: #16a34a; font-weight: bold;">₹{s['t1']:,.2f}<br><span style="font-size:9.5px; color:#059669;">T2: ₹{s['t2']:,.2f}</span></td>
            <td style="padding: 7px 6px; text-align: center; font-weight: bold; color: #1e293b;">{s['qty']} Qty<br><span style="font-size:9.5px; color:#64748b; font-weight:normal;">Exp: ₹{s['exposure']:,}</span></td>
            <td style="padding: 7px 6px; font-size: 10px; color: #475569;">VWAP: ₹{s['vwap']:,.1f}<br>RSI: {s['rsi']:.0f} | Vol: {s['vr']:.1f}x</td>
        </tr>
        """

    return f"""
    <div style="background: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; overflow: hidden; margin-top: 8px;">
        <div style="background: #0f172a; color: #f8fafc; padding: 8px 12px; font-size: 11px; display: flex; justify-content: space-between;">
            <span><b>⚡ 5x MIS Intraday Leverage Desk</b> (Nifty 50 • BankNifty • FinNifty • Sensex)</span>
            <span>Margin/Trade: <b>₹{margin_used:,}</b> ➔ 5x Exposure: <b>₹{margin_used*5:,}</b></span>
        </div>
        <table style="width: 100%; border-collapse: collapse; font-size: 11px;">
            <tr style="background: #f1f5f9; color: #334155; font-weight: bold; text-align: left;">
                <th style="padding: 7px 6px; border-bottom: 1px solid #cbd5e1;">Stock</th>
                <th style="padding: 7px 6px; border-bottom: 1px solid #cbd5e1; text-align: center;">Intraday Signal</th>
                <th style="padding: 7px 6px; border-bottom: 1px solid #cbd5e1; text-align: right;">Live LTP</th>
                <th style="padding: 7px 6px; border-bottom: 1px solid #cbd5e1; text-align: right;">Stop-Loss</th>
                <th style="padding: 7px 6px; border-bottom: 1px solid #cbd5e1; text-align: right;">Targets</th>
                <th style="padding: 7px 6px; border-bottom: 1px solid #cbd5e1; text-align: center;">5x Qty</th>
                <th style="padding: 7px 6px; border-bottom: 1px solid #cbd5e1;">Key Metrics</th>
            </tr>
            {rows_html}
        </table>
    </div>
    """
