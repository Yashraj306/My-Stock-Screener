
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

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def current_ist():
    return dt.datetime.now(IST)


def get_recipients():
    return [
        address.strip()
        for address in EMAIL_TO.split(",")
        if address.strip()
    ]


def get_scan_label(payload):
    """
    Determine report label using configured mode.
    """

    mode = str(payload.get("mode", "")).upper()

    if "MORNING" in mode or "INTRADAY" in mode:
        return "MORNING INTRADAY SCAN"

    if "BTST" in mode:
        return "BTST & STOCK OPTIONS SCAN"

    # Fallback based on Indian market session time.
    current_time = current_ist().time()

    if current_time < dt.time(12, 0):
        return "MORNING INTRADAY SCAN"

    return "BTST & STOCK OPTIONS SCAN"


def count_signals(payload):
    """
    Count actual signals without inventing setup counts.
    """

    intraday_bull = payload.get("intraday_bullish") or []
    intraday_bear = payload.get("intraday_bearish") or []

    equity = payload.get("equity_intraday") or []

    if not intraday_bull and not intraday_bear and equity:
        intraday_bull = [
            item for item in equity
            if str(item.get("action", "")).startswith("BUY")
        ]

        intraday_bear = [
            item for item in equity
            if str(item.get("action", "")).startswith("SELL")
        ]

    setups = payload.get("setups") or {}

    option_bull = (
        payload.get("options_bullish")
        or setups.get("bullish")
        or []
    )

    option_bear = (
        payload.get("options_bearish")
        or setups.get("bearish")
        or []
    )

    return {
        "intraday_bullish": len(intraday_bull),
        "intraday_bearish": len(intraday_bear),
        "options_bullish": len(option_bull),
        "options_bearish": len(option_bear)
    }


def make_subject(payload):
    now = current_ist()
    label = get_scan_label(payload)
    counts = count_signals(payload)

    total = sum(counts.values())

    return (
        f"[{label}] "
        f"{total} Valid Signals | "
        f"{now.strftime('%d %b %Y')}"
    )


def send_alert(payload):
    """
    Send professional HTML screener report by Gmail SMTP.

    No trading orders are placed by this module.
    """

    payload = payload or {}

    if not isinstance(payload, dict):
        logging.error("Email payload must be a dictionary.")
        return False

    if not EMAIL_USER or not EMAIL_PASS:
        logging.error(
            "Email credentials missing. "
            "Check EMAIL_USER and EMAIL_PASS GitHub Secrets."
        )
        return False

    recipients = get_recipients()

    if not recipients:
        logging.error(
            "EMAIL_TO is empty. No email recipient configured."
        )
        return False

    try:
        html_body = render_html_report(payload)

        if not html_body:
            logging.error("HTML report generation returned empty content.")
            return False

        subject = make_subject(payload)

        message = MIMEMultipart("alternative")

        message["Subject"] = subject
        message["From"] = f"Market Research Desk <{EMAIL_USER}>"
        message["To"] = ", ".join(recipients)

        plain_text = (
            "Market Screener Report\n\n"
            "Please open this email in an HTML-compatible email client.\n"
            "This report contains trading research signals only.\n"
            "No automatic orders are placed."
        )

        message.attach(
            MIMEText(plain_text, "plain", "utf-8")
        )

        message.attach(
            MIMEText(html_body, "html", "utf-8")
        )

        with smtplib.SMTP_SSL(
            "smtp.gmail.com",
            465,
            timeout=30
        ) as server:

            server.login(
                EMAIL_USER,
                EMAIL_PASS
            )

            server.sendmail(
                EMAIL_USER,
                recipients,
                message.as_string()
            )

        logging.info(
            "EMAIL SENT SUCCESSFULLY | Subject: %s | Recipients: %s",
            subject,
            len(recipients)
        )

        logging.info(
            "Signal counts: %s",
            count_signals(payload)
        )

        return True

    except smtplib.SMTPAuthenticationError:
        logging.exception(
            "Gmail authentication failed. "
            "Check EMAIL_USER and Gmail App Password."
        )

    except smtplib.SMTPException:
        logging.exception(
            "Gmail SMTP delivery failed."
        )

    except Exception:
        logging.exception(
            "Report generation or email delivery failed."
        )

    return False
