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

logging.basicConfig(level=logging.INFO)


def format_telegram_message(results):
    """Creates a concise Markdown message for Telegram."""
    lines = [f"🚀 *{TITLE}* (Mode: {MODE})\n"]

    swing_setups = results.get("swing", [])
    if swing_setups:
        lines.append("📌 *SWING TRADING SETUPS:*")
        for s in swing_setups[:8]:
            sig_icon = "🟢" if s["signal"] == "BUY" else "🔴"
            lines.append(
                f"{sig_icon} *{s['symbol']}* ({s['signal']})\n"
                f"   • Entry: ₹{s['entry']} | SL: ₹{s['stop_loss']}\n"
                f"   • TGT1: ₹{s['target_1']} | TGT2: ₹{s['target_2']}\n"
                f"   • Qty: {s['qty']} (R:R {s['rr_ratio']}) | RSI: {s['rsi']}\n"
            )
    else:
        lines.append("ℹ️ *Swing Setups:* No qualified triggers today.\n")

    btst_list = results.get("btst", [])
    if btst_list:
        lines.append("⚡ *BTST CANDIDATES:*")
        for b in btst_list[:5]:
            lines.append(f"   • {b['symbol']} @ ₹{b['price']} (RSI: {round(b['rsi'], 1)})")
        lines.append("")

    options = results.get("options", [])
    if options:
        lines.append("📊 *OPTION CHAIN HIGHLIGHTS:*")
        for o in options[:4]:
            lines.append(
                f"   • *{o['symbol']}* (Exp: {o['expiry']})\n"
                f"     Spot: ₹{o['spot']} | PCR: {o['oi_pcr']} ({o['sentiment']})\n"
                f"     Max Pain: ₹{o['max_pain']} | Res: ₹{o['resistance_strike']} | Sup: ₹{o['support_strike']}\n"
            )

    return "\n".join(lines)


def format_html_email(results):
    """Formats a responsive HTML report for Email delivery."""
    swing_rows = ""
    for s in results.get("swing", []):
        color = "#28a745" if s["signal"] == "BUY" else "#dc3545"
        swing_rows += f"""
        <tr>
            <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">{s['symbol']}</td>
            <td style="padding: 8px; border: 1px solid #ddd; color: {color}; font-weight: bold;">{s['signal']}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">₹{s['entry']}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">₹{s['stop_loss']}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">₹{s['target_1']}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">{s['qty']}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">{s['rr_ratio']}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">{s['rsi']}</td>
        </tr>
        """

    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.5;">
        <h2 style="color: #0d6efd;">{TITLE}</h2>
        <p>Market Scan Mode: <b>{MODE}</b></p>
        
        <h3>Qualified Swing Trades</h3>
        <table style="border-collapse: collapse; width: 100%; text-align: left; font-size: 13px;">
            <thead>
                <tr style="background-color: #f2f2f2;">
                    <th style="padding: 8px; border: 1px solid #ddd;">Symbol</th>
                    <th style="padding: 8px; border: 1px solid #ddd;">Signal</th>
                    <th style="padding: 8px; border: 1px solid #ddd;">Entry</th>
                    <th style="padding: 8px; border: 1px solid #ddd;">Stop Loss</th>
                    <th style="padding: 8px; border: 1px solid #ddd;">Target 1</th>
                    <th style="padding: 8px; border: 1px solid #ddd;">Qty</th>
                    <th style="padding: 8px; border: 1px solid #ddd;">R:R</th>
                    <th style="padding: 8px; border: 1px solid #ddd;">RSI</th>
                </tr>
            </thead>
            <tbody>
                {swing_rows if swing_rows else "<tr><td colspan='8' style='padding: 10px; text-align: center;'>No setups triggered.</td></tr>"}
            </tbody>
        </table>
    </body>
    </html>
    """
    return html


def send_telegram(text):
    """Sends notification to Telegram chat via Bot API."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.info("Telegram credentials missing. Skipping Telegram notification.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code == 200:
            logging.info("Telegram notification sent successfully.")
            return True
        logging.warning(f"Telegram API failed with code {r.status_code}: {r.text}")
    except Exception as e:
        logging.error(f"Error sending Telegram alert: {e}")
    return False


def send_email(subject, html_content):
    """Sends HTML email report via SMTP."""
    if not EMAIL_USER or not EMAIL_PASS or not EMAIL_TO:
        logging.info("Email credentials missing. Skipping Email notification.")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, EMAIL_TO.split(","), msg.as_string())
        logging.info("Email alert dispatched successfully.")
        return True
    except Exception as e:
        logging.error(f"Failed to dispatch email: {e}")
    return False


def send_alert(results):
    """Dispatches notifications across all configured channels."""
    tg_msg = format_telegram_message(results)
    send_telegram(tg_msg)

    email_html = format_html_email(results)
    send_email(f"[{MODE}] {TITLE} - Daily Report", email_html)
