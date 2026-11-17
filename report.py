
import datetime as dt
from html import escape


def safe(value, default="—"):
    if value is None:
        return default

    return escape(str(value))


def money(value):
    try:
        return f"₹{float(value):,.2f}"
    except (TypeError, ValueError):
        return "Unavailable"


def render_global_cues(cues):
    if not cues:
        return "<p>No global market data available.</p>"

    rows = ""

    for cue in cues:
        name = safe(cue.get("name"))
        value = safe(cue.get("val"))
        change = safe(cue.get("chg"))

        rows += f"""
        <tr>
            <td>{name}</td>
            <td>{value}</td>
            <td>{change}</td>
        </tr>
        """

    return f"""
    <h3>Global Market Cues</h3>
    <table>
        <tr><th>Name</th><th>Value</th><th>Change</th></tr>
        {rows}
    </table>
    """


def render_intraday_card(item):
    return f"""
    <div class="card">
        <h3>{safe(item.get("symbol"))}
            — {safe(item.get("action"))}</h3>
        <p><b>Entry:</b> {money(item.get("entry"))}</p>
        <p><b>Stop Loss:</b> {money(item.get("stop_loss"))}</p>
        <p><b>Target 1:</b> {money(item.get("target_1"))}</p>
        <p><b>Target 2:</b> {money(item.get("target_2"))}</p>
        <p><b>RSI:</b> {safe(item.get("rsi"))}</p>
        <p><b>Volume Ratio:</b> {safe(item.get("volume_ratio"))}</p>
        <p><b>VWAP:</b> {money(item.get("vwap"))}</p>
        <p><b>Leverage:</b> Not assumed</p>
        <p><b>Quantity:</b> Not calculated — capital undefined</p>
        <p class="note">Signal only. No automatic order.</p>
    </div>
    """


def render_option_card(item):
    plan = item.get("option_plan") or {}
    strategy = plan.get("strategy", "Unavailable")

    details = ""

    if strategy == "OPTION_BUYING":
        details = f"""
        <p><b>Option Type:</b> {safe(plan.get("option_type"))}</p>
        <p><b>Strike:</b> {safe(plan.get("strike"))}</p>
        <p><b>Expiry:</b> {safe(plan.get("expiry"))}</p>
        <p><b>Premium:</b> {money(plan.get("entry_premium"))}</p>
        <p><b>IV:</b> {safe(plan.get("iv"))}</p>
        <p><b>Delta:</b> {safe(plan.get("delta"))}</p>
        <p><b>Theta:</b> {safe(plan.get("theta"))}</p>
        <p><b>Maximum Loss per Unit:</b>
            {money(plan.get("maximum_loss_per_unit"))}</p>
        """

    elif strategy in ("BULL_CALL_SPREAD", "BEAR_PUT_SPREAD"):
        buy_leg = plan.get("buy_leg") or {}
        sell_leg = plan.get("sell_leg") or {}

        details = f"""
        <p><b>Buy Strike:</b> {safe(buy_leg.get("strike"))}</p>
        <p><b>Sell Strike:</b> {safe(sell_leg.get("strike"))}</p>
        <p><b>Expiry:</b> {safe(plan.get("expiry"))}</p>
        <p><b>Net Debit:</b>
            {money(plan.get("net_debit_per_unit"))}</p>
        <p><b>Maximum Loss per Unit:</b>
            {money(plan.get("maximum_loss_per_unit"))}</p>
        <p><b>Maximum Profit per Unit:</b>
            {money(plan.get("maximum_profit_per_unit"))}</p>
        <p><b>Breakeven:</b>
            {money(plan.get("breakeven"))}</p>
        """

    else:
        details = "<p>Option strategy details unavailable.</p>"

    return f"""
    <div class="card">
        <h3>{safe(item.get("symbol"))} — {safe(item.get("side"))}</h3>
        <p><b>Strategy:</b> {safe(strategy)}</p>
        <p><b>Underlying Price:</b> {money(item.get("close"))}</p>
        {details}
        <p><b>Quantity:</b> Not calculated — capital undefined</p>
        <p><b>Execution:</b> Signal only</p>
        <p class="note">
            Review live quotes, expiry and contract lot size
            before considering any trade.
        </p>
    </div>
    """


def render_section(title, items, renderer):
    if not items:
        content = '<p class="empty">No valid signals matched the filters.</p>'
    else:
        content = "".join(renderer(item) for item in items)

    return f"""
    <section>
        <h2>{title}</h2>
        {content}
    </section>
    """


def render_sector_table(sectors):
    if not sectors:
        return "<p>Sector data unavailable.</p>"

    rows = ""

    for sector in sectors:
        rows += f"""
        <tr>
            <td>{safe(sector.get("name"))}</td>
            <td>{safe(sector.get("avg_move"))}</td>
            <td>{safe(sector.get("up_down"))}</td>
            <td>{safe(sector.get("read"))}</td>
        </tr>
        """

    return f"""
    <table>
        <tr>
            <th>Sector</th>
            <th>Average Change</th>
            <th>Advances / Declines</th>
            <th>Market Read</th>
        </tr>
        {rows}
    </table>
    """


def render_html_report(data):
    """
    Generate a conservative signal-only HTML email report.

    Supports:
        Intraday equity bullish/bearish
        BTST stock option bullish/bearish
        Option buying and debit spreads

    No automatic order instructions.
    """

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

    option_bull = (
        data.get("options_bullish")
        or setups.get("bullish")
        or []
    )

    option_bear = (
        data.get("options_bearish")
        or setups.get("bearish")
        or []
    )

    mode = safe(data.get("mode"), "INTRADAY + BTST")

    timestamp = dt.datetime.now(
        dt.timezone(dt.timedelta(hours=5, minutes=30))
    ).strftime("%d %b %Y, %I:%M %p IST")

    total = (
        len(intraday_bull)
        + len(intraday_bear)
        + len(option_bull)
        + len(option_bear)
    )

    global_html = render_global_cues(
        data.get("global_cues") or []
    )

    sector_html = render_sector_table(
        data.get("sectors") or []
    )

    intraday_status = safe(
        data.get("intraday_status"),
        "Not available"
    )

    option_status = safe(
        data.get("option_strategy"),
        "Live option data required"
    )

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width">
    <style>
        body {{
            font-family: Arial, sans-serif;
            background: #f1f5f9;
            color: #1e293b;
            margin: 0;
            padding: 12px;
        }}

        .container {{
            max-width: 720px;
            margin: auto;
            background: white;
            padding: 18px;
            border-radius: 10px;
        }}

        .header {{
            background: #0f172a;
            color: white;
            padding: 18px;
            border-radius: 8px;
        }}

        .header h1 {{
            font-size: 21px;
            margin: 5px 0;
        }}

        .summary {{
            background: #f8fafc;
            padding: 12px;
            margin-top: 14px;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            line-height: 1.8;
        }}

        section {{
            margin-top: 24px;
        }}

        section h2 {{
            font-size: 17px;
            border-bottom: 2px solid #cbd5e1;
            padding-bottom: 8px;
        }}

        .card {{
            border: 1px solid #cbd5e1;
            border-radius: 8px;
            padding: 12px;
            margin: 10px 0;
            background: #ffffff;
            line-height: 1.55;
        }}

        .card h3 {{
            margin-top: 0;
            color: #0f172a;
        }}

        .note {{
            color: #92400e;
            background: #fffbeb;
            padding: 8px;
            border-radius: 5px;
        }}

        .empty {{
            color: #64748b;
            padding: 10px;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
            font-size: 12px;
        }}

        th, td {{
            border: 1px solid #cbd5e1;
            padding: 7px;
            text-align: left;
        }}

        th {{
            background: #f1f5f9;
        }}

        .footer {{
            border-top: 1px solid #cbd5e1;
            margin-top: 25px;
            padding-top: 12px;
            font-size: 12px;
            color: #64748b;
            line-height: 1.7;
        }}
    </style>
    </head>

    <body>
    <div class="container">

        <div class="header">
            <div>UPSTOX MARKET SCREENER</div>
            <h1>{mode} Trading Signals</h1>
            <div>{timestamp}</div>
        </div>

        <div class="summary">
            <h3>Market Summary</h3>
            <p><b>Data Source:</b>
                {safe(summary.get("data_source"))}</p>
            <p><b>Nifty:</b>
                {safe(summary.get("nifty_spot"))}
                ({safe(summary.get("nifty_chg"))})</p>
            <p><b>Bank Nifty:</b>
                {safe(summary.get("bnf_spot"))}
                ({safe(summary.get("bnf_chg"))})</p>
            <p><b>Market Breadth:</b>
                {safe(summary.get("breadth_ratio"))}</p>
            <p><b>Market Regime:</b>
                {safe(summary.get("breadth_tone"))}</p>
            <p><b>Total Valid Signals:</b> {total}</p>
            <p><b>Capital:</b> NOT DEFINED</p>
            <p><b>Quantity:</b> NOT CALCULATED</p>
        </div>

        {global_html}

        <section>
            <h2>Sector Overview</h2>
            {sector_html}
        </section>

        {render_section(
            "Intraday Equity — Bullish BUY",
            intraday_bull,
            render_intraday_card
        )}

        {render_section(
            "Intraday Equity — Bearish SELL",
            intraday_bear,
            render_intraday_card
        )}

        {render_section(
            "BTST Stock Options — Bullish",
            option_bull,
            render_option_card
        )}

        {render_section(
            "BTST Stock Options — Bearish",
            option_bear,
            render_option_card
        )}

        <section>
            <h2>Data Status</h2>
            <p><b>Intraday:</b> {intraday_status}</p>
            <p><b>Options:</b> {option_status}</p>
        </section>

        <div class="footer">
            <b>Risk and Execution Notice</b><br>
            1. This report contains signals only. No orders are placed.<br>
            2. Capital is not configured. Quantity and lot count are not calculated.<br>
            3. No fixed leverage is assumed. Check actual broker margin separately.<br>
            4. Option premiums and Greeks require live quote verification.<br>
            5. Maximum loss figures are per unit, not total contract loss.<br>
            6. Signals are not guaranteed profitable. Verify prices, liquidity,
            expiry and risk before making any trading decision.
        </div>

    </div>
    </body>
    </html>
    """

    return html
