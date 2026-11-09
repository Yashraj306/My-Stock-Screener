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
from report import render_html_report


def send_telegram_summary(data):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    s = data.get("summary", {})
    text = (
        f"📊 *{TITLE}* ({MODE})\n"
        f"Nifty: *{s.get('nifty_spot')}* ({s.get('nifty_chg')}) | RSI {s.get('nifty_rsi')}\n"
        f"Breadth: {s.get('breadth_ratio')} | {s.get('breadth_tone')}\n"
        f"Ideas Generated: *{s.get('ideas_count')}* | Best: *{s.get('best_rated')}*\n\n"
        f"Full report with strike Greeks & checklist has been dispatched to your Email! 📬"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=12)
    except Exception:
        pass


def send_email_report(data):
    if not EMAIL_USER or not EMAIL_PASS or not EMAIL_TO:
        return
    html_body = render_html_report(data)
    subject = f"[{MODE}] {TITLE} | {data.get('summary', {}).get('ideas_count', 0)} ideas | {data.get('summary', {}).get('date_str', '')}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, EMAIL_TO.split(","), msg.as_string())
    except Exception as e:
        logging.error(f"Failed to dispatch email: {e}")


def send_alert(data):
    send_telegram_summary(data)
    send_email_report(data)
