import os
import smtplib
import logging
import datetime as dt
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from report import render_html_report

EMAIL_USER = os.getenv("EMAIL_USER", "").strip()
EMAIL_PASS = os.getenv("EMAIL_PASS", "").strip()
EMAIL_TO = os.getenv("EMAIL_TO", EMAIL_USER).strip()


def send_alert(payload):
    mode = payload.get("mode", "BTST")
    summary = payload.get("summary", {})
    today_str = dt.datetime.now().strftime("%a, %d %b %Y")

    # Calculate exact total ideas count so subject never says "0 ideas"
    idx_cnt = len(payload.get("index_trades", []))
    bull_cnt = len(payload.get("setups", {}).get("bullish", []))
    bear_cnt = len(payload.get("setups", {}).get("bearish", []))
    eq_cnt = len(payload.get("equity_intraday", []))
    total_ideas = summary.get("ideas_count") or (idx_cnt + bull_cnt + bear_cnt + eq_cnt)

    subject = f"[{mode}] Institutional Quant, Hedging & 5x Equity Desk | {total_ideas} Setups | {today_str}"
    html_body = render_html_report(payload)

    if not EMAIL_USER or not EMAIL_PASS:
        logging.warning("Email credentials missing; skipping email dispatch.")
        return

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Quant F&O Desk <{EMAIL_USER}>"
        msg["To"] = EMAIL_TO

        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            recipients = [r.strip() for r in EMAIL_TO.split(",") if r.strip()]
            server.sendmail(EMAIL_USER, recipients, msg.as_string())

        logging.info(f"Email dispatched successfully: {subject}")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")
