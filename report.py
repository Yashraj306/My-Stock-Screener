import datetime as dt
from html import escape

NAVY = "#101D35"
BLUE = "#2563EB"
GREEN = "#059669"
RED = "#DC2626"
GOLD = "#D69E36"
MUTED = "#64748B"
BORDER = "#E5EAF2"


def esc(value, default="—"):
    if value is None or value == "":
        value = default
    return escape(str(value))


def num(value, decimals=2):
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "—"


def money(value):
    try:
        return f"₹{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def pill(text, color=BLUE):
    return (
        f'<span style="display:inline-block;padding:5px 10px;'
        f'border-radius:20px;background:{color}15;color:{color};'
        f'font-size:10px;font-weight:800;">{esc(text)}</span>'
    )


def section(title, subtitle, accent=BLUE):
    return f"""
    <table width="100%" cellpadding="0" cellspacing="0"
           style="margin:24px 0 10px;">
      <tr>
        <td width="5" style="background:{accent};"></td>
        <td style="padding:5px 11px;">
          <div style="font-size:16px;font-weight:800;color:{NAVY};">
            {esc(title)}
          </div>
          <div style="font-size:10px;color:{MUTED};margin-top:3px;">
            {esc(subtitle)}
          </div>
        </td>
      </tr>
    </table>
    """


def metric(label, value, color=BLUE):
    return f"""
    <td width="25%" style="padding:5px;">
      <div style="border:1px solid {BORDER};border-top:3px solid {color};
                  border-radius:8px;padding:11px;background:#FFFFFF;">
        <div style="font-size:9px;color:{MUTED};font-weight:800;">
          {esc(label)}
        </div>
        <div style="font-size:18px;color:{NAVY};font-weight:900;
                    margin-top:6px;">
          {esc(value)}
        </div>
      </div>
    </td>
    """


def render_global_cues(cues):
    if not cues:
        return ""
    rows = []
    for start in range(0, len(cues), 3):
        cells = ""
        for cue in cues[start:start + 3]:
            change = cue.get("chg")
            color = GREEN if str(change).startswith("+") else (
                RED if str(change).startswith("-") else MUTED
            )
            cells += f"""
            <td width="33%" style="padding:9px;border:1px solid {BORDER};">
              <div style="font-size:10px;color:{MUTED};">
                {esc(cue.get("name"))}
              </div>
              <div style="font-size:14px;font-weight:800;color:{NAVY};">
                {esc(cue.get("val"))}
              </div>
              <div style="font-size:10px;color:{color};font-weight:700;">
                {esc(change)}
              </div>
            </td>
            """
        missing = 3 - len(cues[start:start + 3])
        cells += '<td width="33%"></td>' * missing
        rows.append(f"<tr>{cells}</tr>")
    return (
        section("GLOBAL MARKET MONITOR", "External market indicators")
        + '<table width="100%" cellpadding="0" cellspacing="0">'
        + "".join(rows) + "</table>"
    )


def render_sectors(sectors):
    if not sectors:
        return '<p style="color:#64748B;font-size:11px;">Sector data unavailable.</p>'
    rows = ""
    for item in sectors:
        read = str(item.get("read", "Mixed"))
        color = GREEN if read.lower() == "strong" else (
            RED if read.lower() == "weak" else GOLD
        )
        rows += f"""
        <tr>
          <td style="padding:8px;border-bottom:1px solid {BORDER};">
            {esc(item.get("name"))}</td>
          <td align="right" style="padding:8px;border-bottom:1px solid {BORDER};">
            {esc(item.get("avg_move"))}</td>
          <td align="center" style="padding:8px;border-bottom:1px solid {BORDER};">
            {esc(item.get("up_down"))}</td>
          <td align="right" style="padding:8px;border-bottom:1px solid {BORDER};
                                  color:{color};font-weight:800;">
            {esc(read)}</td>
        </tr>
        """
    return f"""
    <table width="100%" cellpadding="0" cellspacing="0"
           style="font-size:11px;border-collapse:collapse;">
      <tr style="background:#F1F5F9;color:{MUTED};">
        <th align="left" style="padding:8px;">SECTOR</th>
        <th align="right" style="padding:8px;">CHANGE</th>
        <th align="center" style="padding:8px;">ADV/DEC</th>
        <th align="right" style="padding:8px;">READ</th>
      </tr>{rows}
    </table>
    """


def render_intraday_card(item, bullish=True):
    accent = GREEN if bullish else RED
    side = "BULLISH LONG" if bullish else "BEARISH SHORT"
    return f"""
    <div style="background:#FFFFFF;border:1px solid {BORDER};
                border-left:4px solid {accent};border-radius:9px;
                padding:13px;margin-bottom:10px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td>
            <div style="font-size:17px;font-weight:900;color:{NAVY};">
              {esc(item.get("symbol"))}
            </div>
            <div style="font-size:10px;color:{MUTED};">
              {esc(item.get("sector", "Equity"))}
            </div>
          </td>
          <td align="right">{pill(side, accent)}</td>
        </tr>
      </table>
      <table width="100%" cellpadding="0" cellspacing="4"
             style="margin-top:10px;">
        <tr>
          <td style="background:#F8FAFC;padding:8px;">
            <small>ENTRY</small><br><b>{money(item.get("entry"))}</b>
          </td>
          <td style="background:#FEF2F2;padding:8px;">
            <small style="color:{RED};">STOP LOSS</small><br>
            <b style="color:{RED};">{money(item.get("stop_loss"))}</b>
          </td>
          <td style="background:#ECFDF5;padding:8px;">
            <small style="color:{GREEN};">TARGET 1</small><br>
            <b style="color:{GREEN};">{money(item.get("target_1"))}</b>
          </td>
        </tr>
      </table>
      <div style="font-size:10px;color:{MUTED};line-height:1.9;margin-top:8px;">
        Target 2: <b>{money(item.get("target_2"))}</b> &nbsp;|&nbsp;
        RSI: <b>{esc(item.get("rsi"))}</b> &nbsp;|&nbsp;
        Volume: <b>{esc(item.get("volume_ratio"))}</b><br>
        VWAP: <b>{money(item.get("vwap"))}</b> &nbsp;|&nbsp;
        Quantity: <b>Not calculated</b>
      </div>
    </div>
    """


def render_option_card(item):
    plan = item.get("option_plan") or {}
    strategy = plan.get("strategy", "Unavailable")
    is_bull = "BULL" in strategy or str(item.get("side", "")).startswith("CALL")
    accent = GREEN if is_bull else RED

    if strategy == "OPTION_BUYING":
        rows = [
            ("Option", plan.get("option_type")),
            ("Strike", plan.get("strike")),
            ("Expiry", plan.get("expiry")),
            ("Entry premium", money(plan.get("entry_premium"))),
            ("IV", plan.get("iv")),
            ("Delta", plan.get("delta")),
            ("Theta", plan.get("theta")),
            ("Maximum loss / unit", money(plan.get("maximum_loss_per_unit")))
        ]
    elif strategy in ("BULL_CALL_SPREAD", "BEAR_PUT_SPREAD"):
        buy = plan.get("buy_leg") or {}
        sell = plan.get("sell_leg") or {}
        rows = [
            ("Buy strike", buy.get("strike")),
            ("Sell strike", sell.get("strike")),
            ("Expiry", plan.get("expiry")),
            ("Net debit / unit", money(plan.get("net_debit_per_unit"))),
            ("Maximum loss / unit", money(plan.get("maximum_loss_per_unit"))),
            ("Maximum profit / unit", money(plan.get("maximum_profit_per_unit"))),
            ("Breakeven", money(plan.get("breakeven")))
        ]
    else:
        rows = [("Details", "Option strategy data unavailable")]

    detail_rows = "".join(
        f'<tr><td style="padding:6px;color:{MUTED};border-bottom:1px solid {BORDER};">{esc(k)}</td>'
        f'<td align="right" style="padding:6px;font-weight:700;border-bottom:1px solid {BORDER};">{esc(v)}</td></tr>'
        for k, v in rows
    )

    return f"""
    <div style="background:#FFFFFF;border:1px solid {BORDER};
                border-left:4px solid {accent};border-radius:9px;
                padding:13px;margin-bottom:10px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td>
            <div style="font-size:17px;font-weight:900;color:{NAVY};">
              {esc(item.get("symbol"))}
            </div>
            <div style="font-size:10px;color:{MUTED};">
              Underlying: {money(item.get("close"))}
            </div>
          </td>
          <td align="right">{pill(strategy.replace("_", " "), accent)}</td>
        </tr>
      </table>
      <table width="100%" cellpadding="0" cellspacing="0"
             style="font-size:11px;margin-top:10px;border-collapse:collapse;">
        {detail_rows}
      </table>
      <div style="margin-top:10px;padding:9px;background:#F8FAFC;
                  border-radius:5px;font-size:10px;color:{MUTED};">
        Quantity / lots not calculated — capital undefined.<br>
        Signal only. Verify live quotes, expiry and lot size.
      </div>
    </div>
    """


def render_signal_section(title, subtitle, items, renderer):
    content = (
        "".join(renderer(x) for x in items[:5])
        if items else
        '<div style="padding:13px;background:#FFFFFF;border:1px solid '
        + BORDER + ';border-radius:8px;color:' + MUTED
        + ';font-size:11px;">No valid signals passed the filters.</div>'
    )
    return section(title, subtitle) + content


def render_html_report(data):
    """Create professional HTML email report for the screener."""
    data = data or {}
    summary = data.get("summary") or {}
    setups = data.get("setups") or {}

    intraday_bull = data.get("intraday_bullish") or []
    intraday_bear = data.get("intraday_bearish") or []

    if not intraday_bull and not intraday_bear:
        equity = data.get("equity_intraday") or []
        intraday_bull = [
            x for x in equity
            if str(x.get("action", "")).startswith("BUY")
        ]
        intraday_bear = [
            x for x in equity
            if str(x.get("action", "")).startswith("SELL")
        ]

    option_bull = data.get("options_bullish") or setups.get("bullish") or []
    option_bear = data.get("options_bearish") or setups.get("bearish") or []

    total = len(intraday_bull) + len(intraday_bear) + len(option_bull) + len(option_bear)

    now = dt.datetime.now(
        dt.timezone(dt.timedelta(hours=5, minutes=30))
    )
    timestamp = now.strftime("%d %b %Y | %I:%M %p IST")

    regime = str(summary.get("breadth_tone", "UNKNOWN")).upper()
    regime_color = GREEN if regime == "BULLISH" else (
        RED if regime == "BEARISH" else GOLD
    )

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width,initial-scale=1">
    </head>
    <body style="margin:0;padding:0;background:#F3F6FB;
                 font-family:Arial,Helvetica,sans-serif;color:{NAVY};">
      <table width="100%" cellpadding="0" cellspacing="0"
             style="background:#F3F6FB;padding:16px 5px;">
        <tr><td align="center">
          <table width="100%" cellpadding="0" cellspacing="0"
                 style="max-width:720px;background:#FFFFFF;
                        border:1px solid {BORDER};border-radius:12px;
                        overflow:hidden;">

            <tr><td style="background:{NAVY};padding:24px;color:#FFFFFF;">
              <div style="color:#7DD3FC;font-size:10px;font-weight:800;
                          letter-spacing:2px;">
                QUANTITATIVE RESEARCH • MARKET INTELLIGENCE
              </div>
              <div style="font-size:24px;font-weight:900;margin-top:9px;">
                MARKET STRATEGY REPORT
              </div>
              <div style="color:#CBD5E1;font-size:12px;margin-top:6px;">
                INTRADAY EQUITY &amp; BTST STOCK OPTIONS
              </div>
              <div style="border-top:1px solid #334155;margin-top:16px;
                          padding-top:11px;color:#CBD5E1;font-size:10px;">
                {timestamp} &nbsp;|&nbsp;
                DATA: {esc(summary.get("data_source", "UPSTOX"))}
              </div>
            </td></tr>

            <tr><td style="padding:17px 16px 5px;">
              <div style="font-size:10px;color:{MUTED};font-weight:800;
                          letter-spacing:1px;">
                EXECUTIVE MARKET SNAPSHOT
              </div>
              <table width="100%" cellpadding="0" cellspacing="0"
                     style="margin-top:8px;">
                <tr>
                  {metric("VALID SIGNALS", total, BLUE)}
                  {metric("INTRADAY", len(intraday_bull)+len(intraday_bear), GOLD)}
                  {metric("OPTIONS", len(option_bull)+len(option_bear), GREEN)}
                  {metric("REGIME", regime, regime_color)}
                </tr>
              </table>
              <div style="margin-top:9px;padding:12px;background:#F8FAFC;
                          border:1px solid {BORDER};border-radius:8px;
                          font-size:11px;line-height:1.9;">
                <b>NIFTY:</b> {esc(summary.get("nifty_spot"))}
                ({esc(summary.get("nifty_chg"))}) &nbsp;|
                <b>BANK NIFTY:</b> {esc(summary.get("bnf_spot"))}
                ({esc(summary.get("bnf_chg"))})<br>
                <b>FIN NIFTY:</b> {esc(summary.get("fin_spot"))}
                ({esc(summary.get("fin_chg"))}) &nbsp;|
                <b>BREADTH:</b> {esc(summary.get("breadth_ratio"))}<br>
                <b>ABOVE EMA20:</b> {esc(summary.get("above_ema20"))}% &nbsp;|
                <b>ABOVE EMA50:</b> {esc(summary.get("above_ema50"))}%
              </div>
              <div style="margin-top:9px;padding:11px;background:#FFFBEB;
                          border:1px solid #FDE68A;border-radius:7px;
                          color:#92400E;font-size:10px;line-height:1.8;">
                <b>CONSERVATIVE RISK PROTOCOL</b><br>
                Capital: NOT DEFINED &nbsp;|&nbsp; Quantity: NOT CALCULATED<br>
                Execution: SIGNAL ONLY &nbsp;|&nbsp; Automatic orders: DISABLED
              </div>
            </td></tr>

            <tr><td style="padding:0 16px 5px;">
              {render_global_cues(data.get("global_cues") or [])}
            </td></tr>

            <tr><td style="padding:0 16px 5px;">
              {section("SECTOR ROTATION", "Sector strength and participation", GOLD)}
              {render_sectors(data.get("sectors") or [])}
            </td></tr>

            <tr><td style="padding:0 16px 5px;">
              {section("INTRADAY EQUITY", "Five-minute candles • VWAP • Momentum", BLUE)}
              {render_signal_section("BULLISH EQUITY SETUPS",
                                     "Long candidates | Maximum 5",
                                     intraday_bull,
                                     lambda x: render_intraday_card(x, True))}
              {render_signal_section("BEARISH EQUITY SETUPS",
                                     "Short candidates | Maximum 5",
                                     intraday_bear,
                                     lambda x: render_intraday_card(x, False))}
            </td></tr>

            <tr><td style="padding:0 16px 5px;">
              {section("BTST STOCK OPTIONS",
                       "Option buying or defined-risk debit spreads", GOLD)}
              {render_signal_section("BULLISH OPTIONS",
                                     "CE Buying / Bull Call Spread | Maximum 5",
                                     option_bull, render_option_card)}
              {render_signal_section("BEARISH OPTIONS",
                                     "PE Buying / Bear Put Spread | Maximum 5",
                                     option_bear, render_option_card)}
            </td></tr>

            <tr><td style="padding:13px 16px;">
              <div style="background:#F8FAFC;border:1px solid {BORDER};
                          border-radius:8px;padding:12px;font-size:10px;
                          line-height:1.9;">
                <b>DATA &amp; EXECUTION STATUS</b><br>
                Intraday: {esc(data.get("intraday_status"))}<br>
                Options: {esc(data.get("option_strategy"))}<br>
                Automatic orders: Disabled<br>
                Capital configuration: Not defined
              </div>
            </td></tr>

            <tr><td style="background:{NAVY};padding:18px;color:#CBD5E1;">
              <div style="font-size:11px;color:#FFFFFF;font-weight:800;
                          letter-spacing:1px;">
                RISK &amp; EXECUTION PROTOCOL
              </div>
              <div style="font-size:10px;line-height:1.9;margin-top:9px;">
                01 — Research and signal generation only; no orders are placed.<br>
                02 — Capital is undefined; quantity and lots are not calculated.<br>
                03 — No fixed leverage is assumed. Verify broker margin independently.<br>
                04 — Verify option premium, liquidity, expiry and lot size.<br>
                05 — Per-unit maximum loss is not total contract loss.<br>
                06 — Signals do not guarantee returns. Apply independent risk checks.
              </div>
              <div style="border-top:1px solid #334155;margin-top:13px;
                          padding-top:10px;color:#94A3B8;font-size:9px;">
                AUTOMATED MARKET RESEARCH DESK<br>
                Generated {timestamp}
              </div>
            </td></tr>

          </table>
        </td></tr>
      </table>
    </body>
    </html>
    """
