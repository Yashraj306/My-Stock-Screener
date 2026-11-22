import logging
import os
import requests


def _format_whatsapp_message(payload: dict) -> str:
    data = payload or {}
    summary = data.get("summary") or {}
    title = str(data.get("title") or "Quant & Hedging Desk").strip()
    mode = str(data.get("mode") or summary.get("mode") or "ALERT").upper()
    source = str(summary.get("data_source") or "N/A")

    nifty = summary.get("nifty_spot", "—")
    nifty_chg = summary.get("nifty_chg", "—")
    bnf = summary.get("bnf_spot", "—")
    bnf_chg = summary.get("bnf_chg", "—")
    breadth = summary.get("breadth_ratio", "—")
    ideas_count = summary.get("ideas_count", 0)

    lines = [
        f"*{title}* [{mode}]",
        f"Source: `{source}` | Ideas: *{ideas_count}*",
        f"NIFTY: *{nifty}* ({nifty_chg}) | BNF: *{bnf}* ({bnf_chg})",
        f"Breadth: {breadth}",
    ]

    index_trades = data.get("index_trades") or []
    if index_trades:
        lines.append("\n*Index Ideas:*")
        for t in index_trades[:4]:
            sym = t.get("symbol", "")
            side = t.get("side", "")
            entry = t.get("entry", "—")
            sl = t.get("stop_loss", "—")
            t1 = t.get("target_1", "—")
            lines.append(f"• *{sym}* ({side}) | E: {entry} | SL: {sl} | T1: {t1}")

    bull_setups = (data.get("options_bullish") or (data.get("setups") or {}).get("bullish") or [])[:5]
    bear_setups = (data.get("options_bearish") or (data.get("setups") or {}).get("bearish") or [])[:5]

    if bull_setups:
        lines.append("\n*Top Bullish Setups:*")
        for s in bull_setups:
            lines.append(
                f"• *{s.get('symbol')}* | E: {s.get('entry')} | SL: {s.get('stop_loss')} | "
                f"T1: {s.get('target_1')} | Zone: {s.get('best_entry_low')}–{s.get('best_entry_high')}"
            )

    if bear_setups:
        lines.append("\n*Top Bearish Setups:*")
        for s in bear_setups:
            lines.append(
                f"• *{s.get('symbol')}* | E: {s.get('entry')} | SL: {s.get('stop_loss')} | "
                f"T1: {s.get('target_1')} | Zone: {s.get('best_entry_low')}–{s.get('best_entry_high')}"
            )

    msg = "\n".join(lines)
    # WhatsApp text message length safety cap
    return msg[:3900]


def send_whatsapp_alert(payload: dict) -> bool:
    token = (os.getenv("WHATSAPP_TOKEN") or "").strip()
    phone_number_id = (os.getenv("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
    recipients_raw = (os.getenv("WHATSAPP_TO") or "").strip()

    if not token or not phone_number_id or not recipients_raw:
        logging.info("WhatsApp alert skipped: WHATSAPP_TOKEN / WHATSAPP_PHONE_NUMBER_ID / WHATSAPP_TO not set.")
        return False

    recipients = [r.strip().lstrip("+") for r in recipients_raw.split(",") if r.strip()]
    if not recipients:
        logging.warning("WhatsApp alert skipped: no valid recipient numbers in WHATSAPP_TO.")
        return False

    message_body = _format_whatsapp_message(payload)
    url = f"https://graph.facebook.com/v21.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    sent_any = False
    for to_num in recipients:
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_num,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": message_body,
            },
        }
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=15)
            if resp.status_code in (200, 201):
                logging.info("WhatsApp alert dispatched to %s", to_num)
                sent_any = True
            else:
                logging.warning(
                    "WhatsApp API failed for %s (HTTP %s): %s",
                    to_num,
                    resp.status_code,
                    (resp.text or "")[:200],
                )
        except Exception as exc:
            logging.warning("WhatsApp request exception for %s: %s", to_num, exc)

    return sent_any
