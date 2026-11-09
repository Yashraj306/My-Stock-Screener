import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests
import logging

from config import (
    EMAIL_USER,
    EMAIL_PASS,
    EMAIL_TO,
    TELEGRAM_TOKEN,
    TELEGRAM_CHAT_ID,
    TITLE,
    MODE
)

def format_telegram_message(payload):
    lines = [f"📊 *{TITLE}* ({MODE})\n"]

    indices = payload.get("indices", [])
    if indices:
        lines.append("🏛️ *KEY INDICES TECH & F&O:*")
        for idx in indices:
            opt = idx.get("options")
            piv = idx.get("pivots", {})
            pcr_str = f"PCR: *{opt['oi_pcr']}* ({opt['sentiment']})" if opt else "PCR: N/A"
            lines.append(
                f"• *{idx['symbol']}*: ₹{idx['spot']} | RSI: {idx['rsi']}\n"
                f"  {pcr_str} | Pivot: ₹{piv.get('pivot')}\n"
                f"  Res: ₹{opt.get('resistance_strike') if opt else piv.get('r1')} | Sup: ₹{opt.get('support_strike') if opt else piv.get('s1')}"
            )
        lines.append("")

    trades = payload.get("trades", [])
    if trades:
        lines.append(f"🚀 *HIGH-PROBABILITY TRADE SUGGESTIONS:*")
        for t in trades[:8]:
            sym = t["symbol"]
            tr = t["trade"]
            opt = t.get("options")
            icon = "🟢" if "BUY" in tr["direction"] else "🔴"
            opt_note = f"PCR: {opt['oi_pcr']}" if opt else "Cash Action"
            lines.append(
                f"{icon} *{sym}* — *{tr['direction']}*\n"
                f"  • *Entry:* ₹{tr['entry']} | *SL:* ₹{tr['stop_loss']}\n"
                f"  • *TGT 1:* ₹{tr['target_1']} | *TGT 2:* ₹{tr['target_2']}\n"
                f"  • *R:R:* 1:{tr['rr_ratio']} | *Qty:* {tr['qty']} shs\n"
                f"  • *RSI:* {t['rsi']} | {opt_note} | Reason: {tr['reasons']}\n"
            )
    else:
        # Show top watchlist if no strict triggers
        stocks = payload.get("stocks", [])
        if stocks:
            lines.append("👀 *TOP ACTIVE CONSTITUENTS (WATCHLIST):*")
            top_active = sorted(stocks, key=lambda x: x.get("rsi", 50), reverse=True)[:6]
            for s in top_active:
                lines.append(f"• *{s['symbol']}*: ₹{s['price']} | RSI: {s['rsi']} | Vol: {s['volume_ratio']}x")
            lines.append("")

    return "\n".join(lines)


def format_html_email(payload):
    trade_cards = ""
    trades = payload.get("trades", [])
    
    # If no strict trade triggered, populate with top active momentum stocks so email is never empty
    display_list = trades if trades else payload.get("stocks", [])[:10]

    for t in display_list:
        tr = t.get("trade", {})
        opt = t.get("options")
        direction = tr.get("direction", "WATCH")
        badge_color = "#28a745" if "BUY" in direction else ("#dc3545" if "SELL" in direction else "#6c757d")
        opt_detail = f"PCR: {opt['oi_pcr']} ({opt['sentiment']}) | Resistance: ₹{opt['resistance_strike']} | Support: ₹{opt['support_strike']}" if opt else "F&O Confirmation Pending"

        trade_cards += f"""
        <div style="border: 1px solid #e0e0e0; border-radius: 6px; padding: 12px; margin-bottom: 10px; background: #ffffff;">
            <div style="margin-bottom: 6px;">
                <span style="font-size: 15px; font-weight: bold; color: #111;">{t['symbol']}</span>
                <span style="background: {badge_color}; color: #fff; padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: bold; margin-left: 8px;">{direction}</span>
            </div>
            <table style="width: 100%; font-size: 12px; color: #333; margin-bottom: 6px;">
                <tr>
                    <td><b>Entry:</b> ₹{tr.get('entry', t['price'])}</td>
                    <td><b>Stop Loss:</b> ₹{tr.get('stop_loss', '-')}</td>
                    <td><b>Target 1:</b> ₹{tr.get('target_1', '-')}</td>
                    <td><b>Target 2:</b> ₹{tr.get('target_2', '-')}</td>
                </tr>
                <tr>
                    <td><b>R:R:</b> 1:{tr.get('rr_ratio', '-')}</td>
                    <td><b>Qty:</b> {tr.get('qty', '-')}</td>
                    <td><b>RSI:</b> {t['rsi']}</td>
                    <td><b>Vol Ratio:</b> {t['volume_ratio']}x</td>
                </tr>
            </table>
            <div style="font-size: 11px; color: #555; background: #f8f9fa; padding: 5px; border-radius: 4px;">
                <b>Analysis:</b> {tr.get('reasons', 'Range-bound near support/resistance')} <br>
                <b>F&O Options:</b> {opt_detail}
            </div>
        </div>
        """

    indices_rows = ""
    for idx in payload.get("indices", []):
        opt = idx.get("options") or {}
        piv = idx.get("pivots") or {}
        indices_rows += f"""
        <tr>
            <td style="padding: 6px; border: 1px solid #ddd; font-weight: bold;">{idx['symbol']}</td>
            <td style="padding: 6px; border: 1px solid #ddd;">₹{idx['spot']}</td>
            <td style="padding: 6px; border: 1px solid #ddd;">{idx['rsi']}</td>
            <td style="padding: 6px; border: 1px solid #ddd;">{opt.get('oi_pcr', 'N/A')}</td>
            <td style="padding: 6px; border: 1px solid #ddd;">₹{opt.get('resistance_strike', piv.get('r1', 'N/A'))}</td>
            <td style="padding: 6px; border: 1px solid #ddd;">₹{opt.get('support_strike', piv.get('s1', 'N/A'))}</td>
        </tr>
        """

    return f"""
    <html>
    <body style="font-family: Arial, sans-serif; background: #f4f6f9; padding: 12px;">
        <div style="max-width: 680px; margin: 0 auto; background: #fff; padding: 16px; border-radius: 8px;">
            <h2 style="color: #0d6efd; margin-top: 0;">{TITLE}</h2>
            <p style="font-size: 13px; color: #666;">Mode: <b>{MODE}</b> | Scanned: <b>{payload.get('total_scanned', 0)} Constituents</b></p>
            
            <h3 style="color: #333; border-bottom: 2px solid #0d6efd; padding-bottom: 4px; font-size: 15px;">Index Tech Levels & Options</h3>
            <table style="width: 100%; border-collapse: collapse; font-size: 12px; margin-bottom: 16px;">
                <tr style="background: #e9ecef;">
                    <th style="padding: 6px; border: 1px solid #ddd;">Index</th>
                    <th style="padding: 6px; border: 1px solid #ddd;">Spot</th>
                    <th style="padding: 6px; border: 1px solid #ddd;">RSI</th>
                    <th style="padding: 6px; border: 1px solid #ddd;">PCR</th>
                    <th style="padding: 6px; border: 1px solid #ddd;">Resistance</th>
                    <th style="padding: 6px; border: 1px solid #ddd;">Support</th>
                </tr>
                {indices_rows}
            </table>

            <h3 style="color: #333; border-bottom: 2px solid #28a745; padding-bottom: 4px; font-size: 15px;">Trade Setups & High-Momentum Stocks</h3>
            {trade_cards}
        </div>
    </body>
    </html>
    """


def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=15)
    except Exception:
        pass


def send_email(subject, html_content):
    if not EMAIL_USER or not EMAIL_PASS or not EMAIL_TO:
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html_content, "html"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(EMAIL_USER, EMAIL_PASS)
            s.sendmail(EMAIL_USER, EMAIL_TO.split(","), msg.as_string())
    except Exception:
        pass


def send_alert(payload):
    tg_text = format_telegram_message(payload)
    send_telegram(tg_text)
    send_email(f"[{MODE}] F&O Indices & Stocks Analysis Report", format_html_email(payload))
