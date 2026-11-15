import datetime as dt
from cards import build_stock_card, build_equity_intraday_table


def render_html_report(data):
    mode = data.get("mode", "BTST")
    title = f"{mode} Institutional Quant & Hedging Desk"
    today_str = dt.datetime.now().strftime("%A, %d %b %Y | %I:%M %p IST")

    summary = data.get("summary", {})
    global_cues = data.get("global_cues", [])
    index_view = data.get("index_view", {})
    index_trades = data.get("index_trades", [])
    sectors = data.get("sectors", [])
    setups = data.get("setups", {})
    equity_intraday = data.get("equity_intraday", [])
    track_record = data.get("track_record", {})

    cues_th = "".join([f'<th style="padding: 6px; border: 1px solid #e2e8f0; font-size: 10.5px; color:#475569;">{c["name"]}</th>' for c in global_cues])
    cues_td = "".join([f'<td style="padding: 6px; border: 1px solid #e2e8f0; font-size: 11px; text-align: center;"><b>{c["val"]}</b><br><span style="color: {"#16a34a" if "+" in c["chg"] else "#dc2626"}; font-weight:bold;">{c["chg"]}</span></td>' for c in global_cues])
    cues_html = f"""
    <div style="margin-bottom: 18px;">
        <h4 style="margin: 0 0 6px 0; color: #0f172a; font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px;">🌍 Global Macro & GIFT Nifty</h4>
        <table style="width: 100%; border-collapse: collapse; background: #fff;">
            <tr style="background: #f8fafc;">{cues_th}</tr>
            <tr>{cues_td}</tr>
        </table>
    </div>
    """ if global_cues else ""

    idx_blocks = ""
    for idx_name, info in index_view.items():
        lifting_str = ", ".join([f"{x[0]} (+{x[1]})" for x in info.get("lifting", [])])
        dragging_str = ", ".join([f"{x[0]} ({x[1]})" for x in info.get("dragging", [])])
        idx_blocks += f"""
        <div style="font-size: 12px; margin-bottom: 6px; padding: 6px 10px; background: #f8fafc; border-radius: 6px; border: 1px solid #e2e8f0;">
            <b>{idx_name} {info.get('spot', '')} ({info.get('chg', '')}) | RSI {info.get('rsi', '')}</b><br>
            <span style="color: #16a34a; font-weight:bold;">▲ Lifting:</span> {lifting_str} pts &nbsp;|&nbsp;
            <span style="color: #dc2626; font-weight:bold;">▼ Dragging:</span> {dragging_str} pts
        </div>
        """

    idx_trades_html = ""
    if index_trades:
        idx_trades_html = """
        <div style="margin-top: 22px;">
            <div style="font-size: 15px; font-weight: 800; color: #0f172a; margin-bottom: 4px;">🏛️ Index Options & Spread Hedging (NIFTY • BANKNIFTY • FINNIFTY • SENSEX)</div>
            <div style="font-size: 11px; color: #64748b; margin-bottom: 10px;">Direct index option strategies with built-in OTM hedge legs to protect against overnight Theta/Vega decay.</div>
        """
        for it in index_trades:
            idx_trades_html += build_stock_card(it)
        idx_trades_html += "</div>"

    sec_rows = ""
    for s in sectors:
        col = "#16a34a" if s.get("read") == "Strong" else ("#dc2626" if s.get("read") == "Weak" else "#64748b")
        sec_rows += f"""
        <tr>
            <td style="padding: 5px 8px; border: 1px solid #e2e8f0; font-weight: bold;">{s['name']}</td>
            <td style="padding: 5px 8px; border: 1px solid #e2e8f0; text-align: center;">{s['avg_move']}</td>
            <td style="padding: 5px 8px; border: 1px solid #e2e8f0; text-align: center;">{s['up_down']}</td>
            <td style="padding: 5px 8px; border: 1px solid #e2e8f0; text-align: center; color: {col}; font-weight: bold;">{s['read']}</td>
        </tr>
        """

    def render_section(heading, sub_desc, cards_list):
        content = "".join([build_stock_card(c) for c in cards_list]) if cards_list else '<div style="font-size: 12px; color: #888;">No setups matched criteria.</div>'
        return f"""
        <div style="margin-top: 22px;">
            <div style="font-size: 15px; font-weight: 800; color: #0f172a;">{heading}</div>
            <div style="font-size: 11px; color: #64748b; margin-bottom: 10px;">{sub_desc}</div>
            {content}
        </div>
        """

    bull_cards = render_section("🟢 Top 5 Bullish Stock Options (with Pre-Built Bull Call Spreads)", "High relative strength F&O stocks with ready-to-execute hedged option legs.", setups.get("bullish", []))
    bear_cards = render_section("🔴 Top 5 Bearish Stock Options (with Pre-Built Bear Put Spreads)", "Institutional breakdown F&O stocks with hedged downside put spreads.", setups.get("bearish", []))

    eq_section_html = f"""
    <div style="margin-top: 24px;">
        <div style="font-size: 15px; font-weight: 800; color: #0f172a;">⚡ Intraday Equity Cash Signals (5x MIS Leverage)</div>
        <div style="font-size: 11px; color: #64748b; margin-bottom: 8px;">High-momentum cash stocks from Nifty 50, BankNifty, FinNifty & Sensex universe with 5x leveraged quantity sizing.</div>
        {build_equity_intraday_table(equity_intraday)}
    </div>
    """

    vix_alert_box = ""
    if summary.get("vix_alert"):
        vix_alert_box = f"""
        <div style="background: #fffbeb; border-left: 4px solid #f59e0b; padding: 9px 12px; margin-bottom: 14px; font-size: 12px; color: #92400e;">
            <b>⚡ Volatility Regime:</b> {summary.get("vix_alert")}
        </div>
        """

    return f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: #f1f5f9; margin: 0; padding: 12px; color: #1e293b;">
        <div style="max-width: 720px; margin: 0 auto; background: #ffffff; padding: 20px; border-radius: 10px; border: 1px solid #cbd5e1; box-shadow: 0 4px 12px rgba(0,0,0,0.05);">
            
            <!-- Institutional Dark Header -->
            <div style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); color: #ffffff; padding: 16px 18px; border-radius: 8px; margin-bottom: 16px; display: flex; align-items: center; justify-content: space-between;">
                <div>
                    <div style="font-size: 10px; color: #38bdf8; font-weight: 700; letter-spacing: 1.2px; text-transform: uppercase;">LIVE UPSTOX FEED • OPTIONS HEDGING & 5X EQUITY DESK</div>
                    <h2 style="margin: 4px 0 2px 0; color: #ffffff; font-size: 19px;">{title}</h2>
                    <div style="font-size: 11.5px; color: #94a3b8;">{today_str} | Feed: <b>{summary.get('data_source', 'Upstox Live API')}</b></div>
                </div>
                <div style="background: #020617; border: 1.5px solid #10b981; border-radius: 8px; padding: 6px 12px; text-align: center;">
                    <div style="font-size: 16px;">🐂 📊 🐻</div>
                    <div style="color: #10b981; font-weight: 900; font-size: 9.5px; letter-spacing: 1px;">QUANT PRO</div>
                </div>
            </div>

            {vix_alert_box}

            <!-- Executive Summary -->
            <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-left: 4px solid #2563eb; border-radius: 6px; padding: 12px 14px; margin-bottom: 16px; font-size: 12.5px; line-height: 1.6;">
                <div style="font-weight: 800; font-size: 13.5px; color: #0f172a; margin-bottom: 4px;">📌 Market Pulse & Desk Summary</div>
                <div><b>Nifty 50:</b> {summary.get('nifty_spot')} ({summary.get('nifty_chg')}) | <b>BankNifty:</b> {summary.get('bnf_spot')} ({summary.get('bnf_chg')}) | <b>FinNifty:</b> {summary.get('fin_spot')} ({summary.get('fin_chg')})</div>
                <div><b>Market Breadth:</b> {summary.get('breadth_ratio')} | {summary.get('above_ema20')}% > EMA20 | {summary.get('above_ema50')}% > EMA50 | Regime: <b>{summary.get('breadth_tone')}</b></div>
                <div><b>Actionable Ideas:</b> {summary.get('ideas_count', 24)} Total Setups (4 Index Spreads + 10 Stock Option Spreads + 10 Intraday 5x Equity Trades)</div>
                <div><b>Top Conviction Pick:</b> <span style="color:#059669; font-weight:bold;">{summary.get('best_rated', 'None')}</span></div>
            </div>

            {cues_html}

            <!-- Index Movers -->
            <div style="margin-bottom: 18px;">
                <h4 style="margin: 0 0 6px 0; color: #0f172a; font-size: 13px; text-transform: uppercase;">📊 Heavyweight Point Contributors</h4>
                {idx_blocks}
            </div>

            <!-- Sector Matrix -->
            <div style="margin-bottom: 18px;">
                <h4 style="margin: 0 0 6px 0; color: #0f172a; font-size: 13px; text-transform: uppercase;">🧭 Sector Rotation Matrix</h4>
                <table style="width: 100%; border-collapse: collapse; font-size: 11px;">
                    <tr style="background: #f1f5f9; font-weight: bold; color: #334155;">
                        <td style="padding: 6px 8px; border: 1px solid #cbd5e1;">Sector</td>
                        <td style="padding: 6px 8px; border: 1px solid #cbd5e1; text-align: center;">Avg Change</td>
                        <td style="padding: 6px 8px; border: 1px solid #cbd5e1; text-align: center;">Adv/Dec</td>
                        <td style="padding: 6px 8px; border: 1px solid #cbd5e1; text-align: center;">Institutional Read</td>
                    </tr>
                    {sec_rows}
                </table>
            </div>

            <!-- 5x Intraday Equity Section -->
            {eq_section_html}

            <!-- 4 Indices Options & Hedging -->
            {idx_trades_html}

            <!-- Top 5 Bullish & Top 5 Bearish Stock Options with Hedging -->
            {bull_cards}
            {bear_cards}

            <div style="margin-top: 20px; padding-top: 12px; border-top: 1px solid #e2e8f0; font-size: 10.5px; color: #64748b; line-height: 1.5;">
                <b>Execution Protocol:</b><br>
                1. <b>Option Hedging:</b> Always execute both BUY and SELL legs simultaneously (Basket Order) to lock in low margin and protect against overnight Theta/IV crush.<br>
                2. <b>5x Equity Intraday:</b> Execute in MIS/Intraday product after 9:25 AM opening range settle; square off mandatory before 3:15 PM.<br>
                3. Data powered by live Upstox V2 market quotes & quantitative BSM Greek engine.
            </div>

        </div>
    </body>
    </html>
    """
