import datetime as dt
from cards import build_stock_card


def render_html_report(data):
    mode = data.get("mode", "BTST")
    title = f"{mode} Options Screener"
    today_str = dt.datetime.now().strftime("%A, %d %b %Y")

    summary = data.get("summary", {})
    global_cues = data.get("global_cues", [])
    index_view = data.get("index_view", {})
    sectors = data.get("sectors", [])
    setups = data.get("setups", {})
    track_record = data.get("track_record", {})

    # 1. Global Cues Table
    cues_th = "".join([f'<th style="padding: 6px; border: 1px solid #ddd; font-size: 11px;">{c["name"]}</th>' for c in global_cues])
    cues_td = "".join([f'<td style="padding: 6px; border: 1px solid #ddd; font-size: 11px; text-align: center;"><b>{c["val"]}</b><br><span style="color: {"#00873c" if "+" in c["chg"] else "#d9534f"};">{c["chg"]}</span></td>' for c in global_cues])
    cues_html = f"""
    <div style="margin-bottom: 18px;">
        <h4 style="margin: 0 0 4px 0; color: #2c3e50;">Global cues</h4>
        <div style="font-size: 11px; color: #7f8c8d; margin-bottom: 6px;">US markets close after India, so their move hints at today's open</div>
        <table style="width: 100%; border-collapse: collapse; background: #fff;">
            <tr style="background: #f8f9fa;">{cues_th}</tr>
            <tr>{cues_td}</tr>
        </table>
    </div>
    """ if global_cues else ""

    # 2. Index Points Contributors
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

    # 3. Sector Table
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

    # 4. Setup Sections Renderer
    def render_section(heading, sub_desc, cards_list):
        content = "".join([build_stock_card(c) for c in cards_list]) if cards_list else '<div style="font-size: 12px; color: #888; font-style: italic; margin-bottom: 12px;">Nothing passed the quality filters today. Fewer, better ideas is intended.</div>'
        return f"""
        <div style="margin-top: 18px;">
            <div style="font-size: 15px; font-weight: bold; color: #2c3e50;">{heading}</div>
            <div style="font-size: 11px; color: #7f8c8d; margin-bottom: 8px;">{sub_desc}</div>
            {content}
        </div>
        """

    pre_bull = render_section("Pre-rally setups: bullish (BUY CALL)", "Coiling under resistance. Best entries come on the break.", setups.get("pre_bull", []))
    pre_bear = render_section("Pre-rally setups: bearish (BUY PUT)", "Pressing on support. Best entries come on the break.", setups.get("pre_bear", []))
    cont_bull = render_section("Continuation: bullish (BUY CALL)", "Move has started. Prefer pullbacks, avoid chasing.", setups.get("cont_bull", []))
    cont_bear = render_section("Continuation: bearish (BUY PUT)", "Move has started. Prefer bounces that fail, avoid chasing.", setups.get("cont_bear", []))

    # 5. Track Record Table
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

    html = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: #f4f6f8; margin: 0; padding: 12px; color: #2f3542;">
        <div style="max-width: 680px; margin: 0 auto; background: #ffffff; padding: 20px; border-radius: 8px; border: 1px solid #e1e8ed;">
            
            <div style="border-bottom: 2px solid #2c3e50; padding-bottom: 8px; margin-bottom: 14px;">
                <h2 style="margin: 0; color: #2c3e50;">{title}</h2>
                <div style="font-size: 13px; color: #7f8c8d;">{today_str}</div>
            </div>

            <!-- Summary Box -->
            <div style="background: #f8f9fa; border-left: 4px solid #3498db; padding: 10px 14px; margin-bottom: 16px; font-size: 13px; line-height: 1.5;">
                <div style="font-weight: bold; font-size: 14px; margin-bottom: 4px;">Summary</div>
                <div><b>Nifty {summary.get('nifty_spot')} ({summary.get('nifty_chg')})</b>, above its 20-day average, RSI {summary.get('nifty_rsi')}</div>
                <div>Breadth: {summary.get('breadth_ratio')} | {summary.get('above_ema20')}% above EMA20 | {summary.get('above_ema50')}% above EMA50 | <b>{summary.get('breadth_tone')}</b></div>
                {f"<div>Global tone: <b>{summary.get('global_tone')}</b></div>" if 'global_tone' in summary else ""}
                <div>Scanned {summary.get('scanned_count', 220)} stocks and indices | Ideas: <b>{summary.get('ideas_count', 0)}</b> (pre-rally {summary.get('pre_count', 0)}, continuation {summary.get('cont_count', 0)})</div>
                <div>Best rated: <b>{summary.get('best_rated', 'None')}</b></div>
            </div>

            {cues_html}

            <!-- Index View -->
            <div style="margin-bottom: 18px;">
                <h4 style="margin: 0 0 6px 0; color: #2c3e50;">Index view</h4>
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

            <!-- Setups -->
            {pre_bull}
            {pre_bear}
            {cont_bull}
            {cont_bear}

            <!-- Track Record -->
            <div style="margin-top: 24px; padding-top: 14px; border-top: 1px solid #e1e8ed;">
                <h4 style="margin: 0 0 4px 0; color: #2c3e50;">Track record</h4>
                <div style="font-size: 11px; color: #7f8c8d; margin-bottom: 6px;">How past ideas actually performed</div>
                <table style="width: 100%; border-collapse: collapse; font-size: 11px;">
                    <tr style="background: #f1f2f6; font-weight: bold;">
                        <td style="padding: 5px; border: 1px solid #ddd;">Last signals</td>
                        <td style="padding: 5px; border: 1px solid #ddd; text-align: center;">Count</td>
                        <td style="padding: 5px; border: 1px solid #ddd; text-align: center;">Profitable</td>
                        <td style="padding: 5px; border: 1px solid #ddd; text-align: center;">Hit T1</td>
                        <td style="padding: 5px; border: 1px solid #ddd; text-align: center;">Hit stop</td>
                        <td style="padding: 5px; border: 1px solid #ddd; text-align: center;">Avg move</td>
                    </tr>
                    {tr_rows if tr_rows else "<tr><td colspan='6' style='padding: 6px; text-align: center; color: #888;'>Track record: accumulating signals for first evaluation cycle.</td></tr>"}
                </table>
            </div>

            <div style="margin-top: 16px; font-size: 10px; color: #95a5a6; line-height: 1.4;">
                BTST plan: buy only if the stock still holds strength around 3:00 to 3:20 PM. Check US futures and news before holding overnight, keep a stop-loss, and plan to exit in the first hour next morning. Options lose value overnight.<br>
                Grades count independent confirmations (trend, level, volume, options, sector, relative strength). They are not probabilities. Ideas only, not advice.
            </div>

        </div>
    </body>
    </html>
    """
    return html
