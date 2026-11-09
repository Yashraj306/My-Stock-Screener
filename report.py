import datetime as dt
from cards import build_stock_card


def render_html_report(data):
    mode = data.get("mode", "BTST")
    title = f"{mode} Quant & F&O Market Screener"
    today_str = dt.datetime.now().strftime("%A, %d %b %Y")

    summary = data.get("summary", {})
    global_cues = data.get("global_cues", [])
    index_view = data.get("index_view", {})
    index_trades = data.get("index_trades", [])
    sectors = data.get("sectors", [])
    setups = data.get("setups", {})
    track_record = data.get("track_record", {})

    cues_th = "".join([f'<th style="padding: 6px; border: 1px solid #ddd; font-size: 11px;">{c["name"]}</th>' for c in global_cues])
    cues_td = "".join([f'<td style="padding: 6px; border: 1px solid #ddd; font-size: 11px; text-align: center;"><b>{c["val"]}</b><br><span style="color: {"#00873c" if "+" in c["chg"] else "#d9534f"};">{c["chg"]}</span></td>' for c in global_cues])
    cues_html = f"""
    <div style="margin-bottom: 18px;">
        <h4 style="margin: 0 0 4px 0; color: #2c3e50;">Global cues & GIFT Nifty</h4>
        <div style="font-size: 11px; color: #7f8c8d; margin-bottom: 6px;">Live indicators hinting at domestic market opening and volatility</div>
        <table style="width: 100%; border-collapse: collapse; background: #fff;">
            <tr style="background: #f8f9fa;">{cues_th}</tr>
            <tr>{cues_td}</tr>
        </table>
    </div>
    """ if global_cues else ""

    idx_blocks = ""
    for idx_name, info in index_view.items():
        lifting_str = ", ".join([f"{x[0]} (+{x[1]})" for x in info.get("lifting", [])])
        dragging_str = ", ".join([f"{x[0]} ({x[1]})" for x in info.get("dragging", [])])
        idx_blocks += f"""
        <div style="font-size: 13px; margin-bottom: 6px;">
            <b>{idx_name} {info.get('spot', '')} ({info.get('chg', '')}) | RSI {info.get('rsi', '')}</b><br>
            <span style="color: #27ae60;">Lifting:</span> {lifting_str} (index points)<br>
            <span style="color: #e74c3c;">Dragging:</span> {dragging_str} (index points)
        </div>
        """

    idx_trades_html = ""
    if index_trades:
        idx_trades_html = """
        <div style="margin-top: 18px; border-left: 4px solid #10ac84; padding-left: 10px;">
            <div style="font-size: 15px; font-weight: bold; color: #10ac84; margin-bottom: 8px;">🏛️ Indices Trade Plans (NIFTY, BANKNIFTY, FINNIFTY, SENSEX)</div>
        """
        for it in index_trades:
            idx_trades_html += build_stock_card(it)
        idx_trades_html += "</div>"

    sec_rows = ""
    for s in sectors:
        col = "#27ae60" if s.get("read") == "Strong" else ("#e74c3c" if s.get("read") == "Weak" else "#7f8c8d")
        sec_rows += f"""
        <tr>
            <td style="padding: 5px; border: 1px solid #ddd; font-weight: bold;">{s['name']}</td>
            <td style="padding: 5px; border: 1px solid #ddd; text-align: center;">{s['avg_move']}</td>
            <td style="padding: 5px; border: 1px solid #ddd; text-align: center;">{s['up_down']}</td>
            <td style="padding: 5px; border: 1px solid #ddd; text-align: center; color: {col}; font-weight: bold;">{s['read']}</td>
        </tr>
        """

    def render_section(heading, sub_desc, cards_list):
        content = "".join([build_stock_card(c) for c in cards_list]) if cards_list else '<div style="font-size: 12px; color: #888; font-style: italic; margin-bottom: 12px;">No setups passed criteria.</div>'
        return f"""
        <div style="margin-top: 18px;">
            <div style="font-size: 15px; font-weight: bold; color: #2c3e50;">{heading}</div>
            <div style="font-size: 11px; color: #7f8c8d; margin-bottom: 8px;">{sub_desc}</div>
            {content}
        </div>
        """

    bull_cards = render_section("🟢 Top 5 Bullish Stock Ideas (BUY CALL / CE)", "High relative strength and volume momentum candidates.", setups.get("bullish", []))
    bear_cards = render_section("🔴 Top 5 Bearish Stock Ideas (BUY PUT / PE)", "Breakdown and institutional weakness candidates.", setups.get("bearish", []))

    tr_rows = ""
    for row in track_record.get("rows", []):
        tr_rows += f"""
        <tr>
            <td style="padding: 5px; border: 1px solid #ddd;">{row['name']}</td>
            <td style="padding: 5px; border: 1px solid #ddd; text-align: center;">{row['count']}</td>
            <td style="padding: 5px; border: 1px solid #ddd; text-align: center;">{row['profitable']}</td>
            <td style="padding: 5px; border: 1px solid #ddd; text-align: center;">{row['hit_t1']}</td>
            <td style="padding: 5px; border: 1px solid #ddd; text-align: center;">{row['hit_stop']}</td>
            <td style="padding: 5px; border: 1px solid #ddd; text-align: center;">{row['avg_move']}</td>
        </tr>
        """

    vix_alert_box = ""
    if summary.get("vix_alert"):
        vix_alert_box = f"""
        <div style="background: #fff3cd; border-left: 4px solid #ffbb33; padding: 8px 12px; margin-bottom: 14px; font-size: 12px; color: #856404;">
            <b>Volatility Insight:</b> {summary.get("vix_alert")}
        </div>
        """

    return f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: #f4f6f8; margin: 0; padding: 12px; color: #2f3542;">
        <div style="max-width: 680px; margin: 0 auto; background: #ffffff; padding: 20px; border-radius: 8px; border: 1px solid #e1e8ed;">
            
            <!-- Guaranteed Clean Inline Logo Header -->
            <div style="border-bottom: 2px solid #2c3e50; padding-bottom: 12px; margin-bottom: 14px; display: flex; align-items: center; justify-content: space-between;">
                <div>
                    <h2 style="margin: 0; color: #2c3e50; font-size: 20px;">{title}</h2>
                    <div style="font-size: 13px; color: #7f8c8d;">{today_str}</div>
                </div>
                <div>
                    <div style="background: linear-gradient(135deg, #0d1b2a, #1b263b); border: 2px solid #00b894; border-radius: 10px; padding: 6px 14px; text-align: center;">
                        <div style="font-size: 18px; line-height: 1.1;">🐂 📈 🐻</div>
                        <div style="color: #00cec9; font-weight: 900; font-size: 10px; letter-spacing: 1px; margin-top: 2px;">QUANT F&O</div>
                    </div>
                </div>
            </div>

            {vix_alert_box}

            <!-- Summary Box -->
            <div style="background: #f8f9fa; border-left: 4px solid #3498db; padding: 10px 14px; margin-bottom: 16px; font-size: 13px; line-height: 1.5;">
                <div style="font-weight: bold; font-size: 14px; margin-bottom: 4px;">Summary</div>
                <div><b>Nifty {summary.get('nifty_spot')} ({summary.get('nifty_chg')})</b>, above 20-day EMA, RSI {summary.get('nifty_rsi')}</div>
                <div>Breadth: {summary.get('breadth_ratio')} | {summary.get('above_ema20')}% above EMA20 | {summary.get('above_ema50')}% above EMA50 | <b>{summary.get('breadth_tone')}</b></div>
                {f"<div>Opening Bias: <b>{summary.get('global_tone')}</b></div>" if summary.get('global_tone') else ""}
                <div>Coverage: <b>4 Core Indices + 10 Selected Stocks (Top 5 Bullish & 5 Bearish)</b></div>
                <div>Top Pick: <b>{summary.get('best_rated', 'None')}</b></div>
            </div>

            {cues_html}

            <!-- Index View -->
            <div style="margin-bottom: 18px;">
                <h4 style="margin: 0 0 6px 0; color: #2c3e50;">Index view & point contributors</h4>
                {idx_blocks}
            </div>

            <!-- Sector Strength -->
            <div style="margin-bottom: 18px;">
                <h4 style="margin: 0 0 6px 0; color: #2c3e50;">Sector strength</h4>
                <table style="width: 100%; border-collapse: collapse; font-size: 11px;">
                    <tr style="background: #f1f2f6; font-weight: bold;">
                        <td style="padding: 5px; border: 1px solid #ddd;">Sector</td>
                        <td style="padding: 5px; border: 1px solid #ddd; text-align: center;">Avg move</td>
                        <td style="padding: 5px; border: 1px solid #ddd; text-align: center;">Up</td>
                        <td style="padding: 5px; border: 1px solid #ddd; text-align: center;">Read</td>
                    </tr>
                    {sec_rows}
                </table>
            </div>

            <!-- Indices Trades -->
            {idx_trades_html}

            <!-- Stock Trades -->
            {bull_cards}
            {bear_cards}

            <!-- Track Record -->
            <div style="margin-top: 24px; padding-top: 14px; border-top: 1px solid #e1e8ed;">
                <h4 style="margin: 0 0 4px 0; color: #2c3e50;">Track record</h4>
                <div style="font-size: 11px; color: #7f8c8d; margin-bottom: 6px;">Evaluated next-session performance</div>
                <table style="width: 100%; border-collapse: collapse; font-size: 11px;">
                    <tr style="background: #f1f2f6; font-weight: bold;">
                        <td style="padding: 5px; border: 1px solid #ddd;">Last signals</td>
                        <td style="padding: 5px; border: 1px solid #ddd; text-align: center;">Count</td>
                        <td style="padding: 5px; border: 1px solid #ddd; text-align: center;">Profitable</td>
                        <td style="padding: 5px; border: 1px solid #ddd; text-align: center;">Hit T1</td>
                        <td style="padding: 5px; border: 1px solid #ddd; text-align: center;">Hit stop</td>
                        <td style="padding: 5px; border: 1px solid #ddd; text-align: center;">Avg move</td>
                    </tr>
                    {tr_rows if tr_rows else "<tr><td colspan='6' style='padding: 6px; text-align: center; color: #888;'>Evaluating performance history.</td></tr>"}
                </table>
            </div>

            <div style="margin-top: 16px; font-size: 10px; color: #95a5a6; line-height: 1.4;">
                Rule: BTST entries should be validated near 3:15 PM if price sustains relative to VWAP and day limits. Morning entries should be taken on the break of opening 15-minute range. Options lose value overnight; always maintain strict stop loss.
            </div>

        </div>
    </body>
    </html>
    """
