"""
Email delivery for My Stock Screener.

Expected report interface:
    report.render_html_report(payload) -> HTML string

Required environment variables:
    EMAIL_USER
    EMAIL_PASS
    EMAIL_TO

This module sends reports only. It never places or modifies orders.
"""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from report import render_html_report


logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_TIMEOUT = int(os.getenv("SMTP_TIMEOUT", "30"))


def _as_list(value: Any) -> list:
    """Return a list only when the payload field is actually a list/tuple."""
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _recipient_list(raw: str) -> list:
    """Support one or more comma/semicolon separated recipients."""
    if not raw:
        return []
    return [
        address.strip()
        for address in raw.replace(";", ",").split(",")
        if address.strip()
    ]


def _mode_label(payload: dict) -> str:
    """Choose a clear email subject from the actual scan mode."""
    mode = str(payload.get("mode", "SWING")).strip().upper()

    if mode in {"MORNING", "INTRADAY"}:
        return "MORNING INTRADAY EQUITY SCAN"
    if mode == "BTST":
        return "BTST STOCK OPTIONS SCAN"
    if mode == "SWING":
        return "SWING + INTRADAY + BTST SCAN"

    # Backward compatibility for older payloads.
    if "MORNING" in mode or "INTRADAY" in mode:
        return "MORNING INTRADAY EQUITY SCAN"
    if "BTST" in mode:
        return "BTST STOCK OPTIONS SCAN"
    return "STOCK MARKET SCREENER REPORT"


def _signal_counts(payload: dict) -> dict:
    """Count signal lists without assuming that all fields are present."""
    intraday_bull = _as_list(payload.get("intraday_bullish"))
    intraday_bear = _as_list(payload.get("intraday_bearish"))
    options_bull = _as_list(payload.get("options_bullish"))

    options_bear = _as_list(payload.get("options_bearish"))

    # Compatibility with payloads that only populate `setups`.
    setups = payload.get("setups")
    if isinstance(setups, dict):
        if not options_bull:
            options_bull = _as_list(setups.get("bullish"))
        if not options_bear:
            options_bear = _as_list(setups.get("bearish"))

    # Compatibility with older payloads that only have equity_intraday.
    if not intraday_bull and not intraday_bear:
        equity = _as_list(payload.get("equity_intraday"))
        for item in equity:
            if not isinstance(item, dict):
                continue
            action = str(item.get("action", "")).upper()
            if "BUY" in action or "LONG" in action:
                intraday_bull.append(item)
            elif "SELL" in action or "SHORT" in action:
                intraday_bear.append(item)

    return {
        "intraday_bullish": len(intraday_bull),
        "intraday_bearish": len(intraday_bear),
        "options_bullish": len(options_bull),
        "options_bearish": len(options_bear),
        "total": (
            len(intraday_bull) + len(intraday_bear)
            + len(options_bull) + len(options_bear)
        ),
    }


def _build_subject(payload: dict, counts: dict) -> str:
    label = _mode_label(payload)
    date_text = str(payload.get("scan_date", "")).strip()
    if not date_text:
        date_text = "Latest"

    return (
        f"{label} | {date_text} | "
        f"{counts['total']} Signals"
    )


def send_alert(payload: dict) -> bool:
    """
    Render and email the screener report.

    Returns True when SMTP accepts the message; otherwise raises an
    exception so GitHub Actions can show the delivery failure.
    """
    if not isinstance(payload, dict):
        raise TypeError("send_alert expects a dictionary payload")

    email_user = os.getenv("EMAIL_USER", "").strip()
    email_pass = os.getenv("EMAIL_PASS", "").strip()
    recipients = _recipient_list(os.getenv("EMAIL_TO", ""))

    missing = []
    if not email_user:
        missing.append("EMAIL_USER")
    if not email_pass:
        missing.append("EMAIL_PASS")
    if not recipients:
        missing.append("EMAIL_TO")

    if missing:
        raise RuntimeError(
            "Email configuration missing: " + ", ".join(missing)
        )

    html = render_html_report(payload)
    if not isinstance(html, str) or not html.strip():
        raise ValueError("render_html_report returned empty or invalid HTML")

    counts = _signal_counts(payload)
    subject = _build_subject(payload, counts)

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = email_user
    message["To"] = ", ".join(recipients)

    mode = str(payload.get("mode", "UNKNOWN")).upper()
    plain_text = (
        "My Stock Screener Report\n"
        f"Mode: {mode}\n"
        f"Intraday bullish: {counts['intraday_bullish']}\n"
        f"Intraday bearish: {counts['intraday_bearish']}\n"
        f"Options bullish: {counts['options_bullish']}\n"
        f"Options bearish: {counts['options_bearish']}\n"
        f"Total signals: {counts['total']}\n\n"
        "Execution: SIGNAL_ONLY\n"
        "Automatic order placement: DISABLED\n"
        "Please review the attached HTML report content in your email client."
    )

    message.attach(MIMEText(plain_text, "plain", "utf-8"))
    message.attach(MIMEText(html, "html", "utf-8"))

    logger.info(
        "Sending %s email to %d recipient(s); signals=%d",
        mode, len(recipients), counts["total"],
    )

    # Gmail App Password is expected in EMAIL_PASS when using Gmail.
    with smtplib.SMTP_SSL(
        SMTP_HOST,
        SMTP_PORT,
        timeout=SMTP_TIMEOUT,
    ) as server:
        server.login(email_user, email_pass)
        server.sendmail(email_user, recipients, message.as_string())

    logger.info("Email report accepted by SMTP server.")
    return True
