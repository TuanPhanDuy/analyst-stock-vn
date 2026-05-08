"""Rich HTML email with market context, entry/stop/target, sector heatmap."""
import os
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from datetime import datetime, timezone, timedelta
from src.market_rules import valid_lo_price


def _market_status_banner() -> str:
    """Show whether HOSE is currently open or closed, and when it next opens."""
    ict = timezone(timedelta(hours=7))
    now = datetime.now(ict)
    weekday = now.weekday()   # 0=Mon … 4=Fri, 5=Sat, 6=Sun
    h, m = now.hour, now.minute
    t = h * 60 + m           # minutes since midnight

    sessions = [
        (9*60,       9*60+15,  "Pre-open (ATO only)"),
        (9*60+15,    11*60+30, "Morning session — place LO orders NOW"),
        (11*60+30,   13*60,    "Lunch break"),
        (13*60,      14*60+30, "Afternoon session — place LO orders NOW"),
        (14*60+30,   14*60+45, "ATC closing — LO no longer accepted"),
    ]

    if weekday >= 5:
        status, color, msg = "CLOSED", "#c0392b", "Market reopens Monday 9:15 AM"
    else:
        status, color, msg = "CLOSED", "#c0392b", "Market reopens tomorrow 9:15 AM"
        for start, end, label in sessions:
            if start <= t < end:
                if "NOW" in label:
                    status, color = "OPEN", "#1a7f4b"
                else:
                    status, color = "OPEN (limited)", "#e67e22"
                msg = label
                break
        else:
            if t < 9*60:
                msg = f"Market opens in {9*60+15 - t} min (9:15 AM)"
            # after 2:45 PM on a weekday — already handled by default msg above

    return f"""
    <div style="padding:10px 18px;background:{color};color:white;border-radius:6px;
                margin-bottom:14px;font-family:sans-serif;font-size:14px;font-weight:bold">
      HOSE Market: {status} &nbsp;·&nbsp;
      <span style="font-weight:normal">{msg}</span>
      <span style="float:right;font-size:11px;opacity:0.85">
        {now.strftime('%H:%M')} HCM time
      </span>
    </div>"""


def _app(vnd: float) -> str:
    """
    Convert VND price to VCBS Mobies input format (nghìn đồng).
    26500 → '26.5'   26550 → '26.55'   150000 → '150'   16800 → '16.8'
    """
    v = vnd / 1000
    # Show up to 2 decimal places, strip trailing zeros
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s


def _sector_heatmap_html(sectors: dict) -> str:
    if not sectors:
        return ""
    rows = ""
    for sector, perf in sorted(sectors.items(), key=lambda x: x[1]["return_1m"], reverse=True):
        r1m = perf["return_1m"]
        r3m = perf["return_3m"]
        c1m = "#1a7f4b" if r1m > 0 else "#c0392b"
        c3m = "#1a7f4b" if r3m > 0 else "#c0392b"
        rows += f"""<tr>
          <td style="padding:5px 10px">{sector}</td>
          <td style="padding:5px 10px;text-align:right;color:{c1m};font-weight:bold">{r1m:+.1f}%</td>
          <td style="padding:5px 10px;text-align:right;color:{c3m}">{r3m:+.1f}%</td>
        </tr>"""
    return f"""
    <h3 style="color:#333;margin-top:28px">📊 Sector Performance</h3>
    <table style="border-collapse:collapse;font-family:sans-serif;font-size:13px;width:360px">
      <thead>
        <tr style="background:#f0f0f0">
          <th style="padding:5px 10px;text-align:left">Sector</th>
          <th style="padding:5px 10px;text-align:right">1 Month</th>
          <th style="padding:5px 10px;text-align:right">3 Months</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>"""


def _breadth_html(breadth: dict) -> str:
    if not breadth:
        return ""
    color = {"STRONG": "#1a7f4b", "WEAK": "#c0392b", "NEUTRAL": "#888"}.get(
        breadth.get("breadth", "NEUTRAL"), "#888"
    )
    return f"""
    <div style="margin:12px 0;padding:10px 16px;background:#f7f9fc;border-radius:6px;
                font-family:sans-serif;font-size:13px;display:inline-block">
      <b>Market Breadth:</b>
      <span style="color:{color};font-weight:bold">{breadth.get('breadth','?')}</span>
      &nbsp;·&nbsp; Above MA20: <b>{breadth.get('pct_above_ma20','?')}%</b>
      &nbsp;·&nbsp; Above MA60: <b>{breadth.get('pct_above_ma60','?')}%</b>
      &nbsp;·&nbsp; Advancing: <b>{breadth.get('pct_advancing','?')}%</b>
    </div>"""


def _vnindex_html(vnindex: dict) -> str:
    if not vnindex or vnindex.get("status") == "unavailable":
        return ""
    trend = vnindex.get("trend", "?")
    color = {"BULLISH": "#1a7f4b", "BEARISH": "#c0392b", "MIXED": "#e67e22"}.get(trend, "#888")
    r1m = vnindex.get("m1_return_pct", 0)
    r3m = vnindex.get("m3_return_pct", 0)
    return f"""
    <div style="margin:12px 0;padding:10px 16px;background:#f0f8ff;border-radius:6px;
                font-family:sans-serif;font-size:13px;display:inline-block">
      <b>VN-Index:</b> {vnindex.get('price','?'):,.1f}
      &nbsp;·&nbsp; Trend: <span style="color:{color};font-weight:bold">{trend}</span>
      &nbsp;·&nbsp; 1M: <b style="color:{'#1a7f4b' if r1m>0 else '#c0392b'}">{r1m:+.1f}%</b>
      &nbsp;·&nbsp; 3M: <b style="color:{'#1a7f4b' if r3m>0 else '#c0392b'}">{r3m:+.1f}%</b>
    </div>"""


def _top_picks_html(top_picks: list) -> str:
    if not top_picks:
        return ""
    rows = ""
    for p in top_picks:
        action = p.get("action", "")
        conviction = p.get("conviction", "")
        color = "#1a7f4b" if action == "BUY" else "#c0392b"
        conv_color = {"HIGH": "#1a7f4b", "MEDIUM": "#e67e22", "LOW": "#888"}.get(conviction, "#888")
        entry = p.get("entry", 0)
        stop = p.get("stop_loss", 0)
        target = p.get("target", 0)
        risk_pct = round((entry - stop) / entry * 100, 1) if entry else 0
        reward_pct = round((target - entry) / entry * 100, 1) if entry else 0
        rows += f"""
        <tr style="border-bottom:1px solid #eee">
          <td style="padding:10px;font-weight:bold;font-size:15px">{p.get('ticker','')}</td>
          <td style="padding:10px">
            <span style="color:{color};font-weight:bold">{action}</span>
            &nbsp;<span style="color:{conv_color};font-size:12px">[{conviction}]</span>
          </td>
          <td style="padding:10px;font-size:13px;color:#333">{p.get('thesis','')}</td>
          <td style="padding:10px;font-size:12px;text-align:right">
            Entry: <b>{entry:,.0f}</b><br>
            Stop: <span style="color:#c0392b">{stop:,.0f} (-{risk_pct}%)</span><br>
            Target: <span style="color:#1a7f4b">{target:,.0f} (+{reward_pct}%)</span>
          </td>
          <td style="padding:10px;font-size:12px;color:#888;font-style:italic">
            {p.get('risk_note','')}
          </td>
        </tr>"""
    return f"""
    <h3 style="color:#333;margin-top:28px">🎯 Today's Best Picks</h3>
    <table style="border-collapse:collapse;width:100%;font-family:sans-serif;font-size:13px">
      <thead>
        <tr style="background:#f0f0f0">
          <th style="padding:8px 10px;text-align:left">Ticker</th>
          <th style="padding:8px 10px;text-align:left">Signal</th>
          <th style="padding:8px 10px;text-align:left">Thesis</th>
          <th style="padding:8px 10px;text-align:right">Levels (VND)</th>
          <th style="padding:8px 10px;text-align:left">Risk</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>"""


def _signal_table_html(items: list, action: str) -> str:
    if not items:
        return ""
    color = "#1a7f4b" if action == "buy" else "#c0392b"
    emoji = "📈" if action == "buy" else "📉"
    rows = ""
    for item in items[:7]:
        reasons = "; ".join(
            s["reason"] for s in item["signals"] if s["action"] != "HOLD"
        )[:120]
        rows += f"""<tr style="border-bottom:1px solid #f5f5f5">
          <td style="padding:6px 10px;font-weight:bold">{item['ticker']}</td>
          <td style="padding:6px 10px;text-align:right">{item['price']:,.0f} ₫</td>
          <td style="padding:6px 10px;text-align:right;color:{color}">{item['composite']:+.3f}</td>
          <td style="padding:6px 10px;color:#555;font-size:12px">{reasons}</td>
        </tr>"""
    return f"""
    <h3 style="color:{color};margin-top:24px">{emoji} All {action.upper()} Signals</h3>
    <table style="border-collapse:collapse;width:100%;font-family:sans-serif;font-size:13px">
      <thead>
        <tr style="background:#f0f0f0">
          <th style="padding:6px 10px;text-align:left">Ticker</th>
          <th style="padding:6px 10px;text-align:right">Price</th>
          <th style="padding:6px 10px;text-align:right">Score</th>
          <th style="padding:6px 10px;text-align:left">Signals</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>"""


def _capital_warning_html(ranked: dict, capital: float) -> str:
    """Show warning if capital is very small, list unaffordable stocks."""
    unaffordable = ranked.get("buy_unaffordable", [])
    affordable = ranked.get("buy", [])
    if not unaffordable:
        return ""

    skipped = ", ".join(
        f"{i['ticker']} (1 lot = {i['price']*100:,.0f} ₫)" for i in unaffordable[:5]
    )
    affordable_note = (
        f"<b>Affordable signals:</b> {', '.join(i['ticker'] for i in affordable)}"
        if affordable else "<b>No affordable signals today</b> — all signals require more than your capital."
    )
    return f"""
    <div style="background:#fff3e0;border-left:4px solid #e65100;padding:14px 18px;
                margin:16px 0;font-family:sans-serif;font-size:13px;color:#333">
      <b>⚠️ Capital Notice — {capital:,.0f} ₫ available</b><br><br>
      The following signals were skipped because <b>1 lot costs more than your total capital</b>:<br>
      <span style="color:#b71c1c">{skipped}</span><br><br>
      {affordable_note}<br><br>
      <span style="color:#555;font-size:12px">
        💡 With 3M ₫, you can buy at most <b>1–2 stocks</b> (1 lot each). Focus on the single
        strongest signal rather than spreading thin. Consider adding more capital when possible —
        50M ₫+ gives much more flexibility across VN30.
      </span>
    </div>"""


def _vcbs_guide_html(ranked: dict) -> str:
    """
    Step-by-step VCBS Mobies buy guide pre-filled with today's affordable BUY signals,
    including risk-based quantity suggestions.
    """
    buys = [i for i in ranked.get("buy", []) if i["recommendation"] in ("BUY", "STRONG BUY")][:3]
    if not buys:
        buys = ranked.get("buy", [])[:3]
    if not buys:
        return ""

    ticker_cards = ""
    for i, item in enumerate(buys, 1):
        ticker = item["ticker"]
        price = item["price"]      # previous close = reference price

        # Compute valid LO price: 0.5% below close, snapped to correct tick,
        # clamped within today's ceiling/floor band
        lo_info = valid_lo_price(price * 0.995, reference_price=price)
        lo_price = lo_info["price"]
        ceiling = lo_info["ceiling"]
        floor_p = lo_info["floor"]
        tick = lo_info["tick"]

        sizing = item.get("sizing", {})
        qty = sizing.get("qty", 0)
        total_cost = sizing.get("total_cost", 0)
        risk_vnd = sizing.get("risk_vnd", 0)
        risk_pct = sizing.get("risk_pct", 0)
        position_pct = sizing.get("position_pct", 0)
        sizing_note = sizing.get("sizing_note", "")

        qty_block = ""
        if qty:
            qty_block = f"""
            <div style="background:#e8f5e9;border:1px solid #a5d6a7;border-radius:4px;
                        padding:10px 14px;margin:10px 0;font-size:13px">
              <b>📊 Suggested Quantity:
                <span style="font-size:17px;color:#1a7f4b">{qty:,} shares</span>
                ({qty // 100} lots)
              </b><br>
              <table style="margin-top:6px;font-size:12px;color:#333">
                <tr>
                  <td style="padding:2px 16px 2px 0">Total cost</td>
                  <td><b>{total_cost:,.0f} ₫</b>
                    <span style="color:#888"> ({position_pct:.1f}% of capital)</span></td>
                </tr>
                <tr>
                  <td style="padding:2px 16px 2px 0">Max loss if stop hit</td>
                  <td><b style="color:#c0392b">{risk_vnd:,.0f} ₫</b>
                    <span style="color:#888"> ({risk_pct:.2f}% of capital)</span></td>
                </tr>
                <tr>
                  <td style="padding:2px 16px 2px 0">Sizing basis</td>
                  <td style="color:#555">{sizing_note}</td>
                </tr>
              </table>
            </div>"""

        # App-format prices (nghìn đồng — what you actually type into VCBS Mobies)
        app_lo    = _app(lo_price)
        app_ceil  = _app(ceiling)
        app_floor = _app(floor_p)
        app_tick  = _app(tick)      # e.g. tick=50 → "0.05"
        app_ref   = _app(price)

        ticker_cards += f"""
        <div style="background:#f0f8f0;border:1px solid #c8e6c9;border-radius:6px;
                    padding:12px 16px;margin:10px 0;font-family:sans-serif;font-size:13px">
          <b style="font-size:15px;color:#1a7f4b">#{i} {ticker}</b>
          <span style="color:#888;font-size:12px">
            — ref {price:,.0f} ₫ ({app_ref} nghìn) · bước giá {tick} ₫ ({app_tick} nghìn)
          </span>

          <!-- Price band bar -->
          <div style="margin:10px 0;padding:10px 16px;background:#e3f2fd;border-radius:4px;
                      font-size:13px;font-family:sans-serif">
            <table style="width:100%;border-collapse:collapse">
              <tr>
                <td style="color:#c0392b;font-weight:bold">
                  Giá sàn<br>
                  <span style="font-size:11px;color:#888">{floor_p:,.0f} ₫</span><br>
                  <span style="font-size:11px;color:#888">type: <b>{app_floor}</b></span>
                </td>
                <td style="text-align:center">
                  <div style="font-size:11px;color:#555">← any price in this range is valid →</div>
                  <div style="margin:4px 0;padding:6px 12px;background:#1a7f4b;color:white;
                              border-radius:4px;display:inline-block;font-size:15px;font-weight:bold">
                    Type into app: <span style="font-size:20px">{app_lo}</span>
                  </div>
                  <div style="font-size:11px;color:#555">({lo_price:,.0f} ₫)</div>
                </td>
                <td style="text-align:right;color:#1a7f4b;font-weight:bold">
                  Giá trần<br>
                  <span style="font-size:11px;color:#888">{ceiling:,.0f} ₫</span><br>
                  <span style="font-size:11px;color:#888">type: <b>{app_ceil}</b></span>
                </td>
              </tr>
            </table>
          </div>

          {qty_block}

          <b>Steps in VCBS Mobies:</b>
          <ol style="margin:8px 0 0 0;padding-left:20px;line-height:2.2">
            <li>Open <b>VCBS Mobies</b> → tap <b>Đặt lệnh</b></li>
            <li>Search <b style="color:#1a7f4b">{ticker}</b> → select</li>
            <li>Tap <b>MUA</b> tab</li>
            <li>Order type → <b>LO</b></li>
            <li>Price field → type <b style="font-size:16px;color:#1a7f4b">{app_lo}</b>
              <span style="color:#888;font-size:11px">
                &nbsp;(= {lo_price:,.0f} ₫ · app uses nghìn đồng · valid: {app_floor} – {app_ceil} · step {app_tick})
              </span>
            </li>
            <li>Quantity → <b>{qty if qty else '???'}</b> cổ phiếu</li>
            <li>Tap <b>Đặt lệnh</b> → review → confirm PIN</li>
            <li>Check <b>Sổ lệnh</b> — unfilled by 11 AM? Cancel &amp; re-enter at market price</li>
          </ol>

          <div style="margin-top:10px;padding:8px 12px;background:#fff8e1;border-radius:4px;
                      font-size:12px;color:#555">
            <b>📌 Price input rule:</b> VCBS Mobies uses <b>nghìn đồng (thousands)</b>.
            Divide any VND price by 1,000 to get what you type.
            Examples: 26,500 ₫ → <b>26.5</b> &nbsp;·&nbsp;
                      26,550 ₫ → <b>26.55</b> &nbsp;·&nbsp;
                      17,450 ₫ → <b>17.45</b> &nbsp;·&nbsp;
                      150,000 ₫ → <b>150</b><br>
            Step size is <b>{app_tick}</b> — only multiples of {app_tick} are accepted.
          </div>
        </div>"""

    # Shared guidance block
    return f"""
    <h3 style="color:#1565c0;margin-top:32px">📱 How to Execute on VCBS Mobies</h3>

    {_market_status_banner()}
    <div style="background:#e3f2fd;border-left:4px solid #1565c0;padding:12px 16px;
                margin:0 0 12px 0;font-family:sans-serif;font-size:13px">
      <b>⏰ HOSE Trading Hours (Ho Chi Minh City time)</b><br>
      <table style="margin-top:6px;border-collapse:collapse;font-size:12px">
        <tr>
          <td style="padding:3px 12px 3px 0;color:#555">Pre-open (ATO)</td>
          <td><b>9:00 – 9:15 AM</b></td>
          <td style="padding-left:16px;color:#888">ATO orders only — no LO yet</td>
        </tr>
        <tr style="background:#e8f5e9">
          <td style="padding:3px 12px 3px 0;color:#1a7f4b"><b>Morning session ✅</b></td>
          <td><b>9:15 – 11:30 AM</b></td>
          <td style="padding-left:16px;color:#1a7f4b"><b>Best window — place LO orders here</b></td>
        </tr>
        <tr>
          <td style="padding:3px 12px 3px 0;color:#555">Lunch break</td>
          <td><b>11:30 AM – 1:00 PM</b></td>
          <td style="padding-left:16px;color:#c0392b">❌ Market closed — orders rejected</td>
        </tr>
        <tr style="background:#e8f5e9">
          <td style="padding:3px 12px 3px 0;color:#1a7f4b"><b>Afternoon session ✅</b></td>
          <td><b>1:00 – 2:30 PM</b></td>
          <td style="padding-left:16px;color:#1a7f4b"><b>LO orders accepted</b></td>
        </tr>
        <tr>
          <td style="padding:3px 12px 3px 0;color:#555">ATC closing</td>
          <td><b>2:30 – 2:45 PM</b></td>
          <td style="padding-left:16px;color:#888">ATC orders only — LO no longer accepted</td>
        </tr>
        <tr style="background:#fce4ec">
          <td style="padding:3px 12px 3px 0;color:#c0392b"><b>Market closed ❌</b></td>
          <td><b>2:45 PM onwards</b></td>
          <td style="padding-left:16px;color:#c0392b">
            <b>All orders rejected — "OMS process is not valid"</b>
          </td>
        </tr>
      </table>
    </div>

    <div style="background:#fff8e1;border-left:4px solid #f9a825;padding:12px 16px;
                margin:0 0 12px 0;font-family:sans-serif;font-size:13px">
      <b>💰 Position Sizing (safe defaults)</b><br>
      <ul style="margin:6px 0;padding-left:20px;line-height:1.8;color:#333">
        <li>Max <b>15% of portfolio</b> per stock — never go all-in on one signal</li>
        <li>HOSE minimum lot: <b>100 shares</b> — orders must be multiples of 100</li>
        <li>Settlement: <b>T+2</b> — cash is deducted immediately, shares arrive in 2 days</li>
        <li>If signal is <b>WATCH</b>: use ≤5% position — weak signal, higher risk</li>
        <li>Set your stop loss immediately after the order fills (see target levels above)</li>
      </ul>
    </div>

    <div style="background:#fce4ec;border-left:4px solid #c62828;padding:12px 16px;
                margin:0 0 16px 0;font-family:sans-serif;font-size:13px">
      <b>⚠️ Order Type Quick Reference</b><br>
      <table style="margin-top:6px;border-collapse:collapse;font-size:12px;width:100%">
        <tr style="background:#f5f5f5">
          <th style="padding:4px 10px;text-align:left">Type</th>
          <th style="padding:4px 10px;text-align:left">When to use</th>
          <th style="padding:4px 10px;text-align:left">Risk</th>
        </tr>
        <tr>
          <td style="padding:4px 10px"><b>LO</b> (Lệnh giới hạn)</td>
          <td style="padding:4px 10px">Normal trading hours — you control the price</td>
          <td style="padding:4px 10px;color:#555">May not fill if price moves away</td>
        </tr>
        <tr style="background:#f9f9f9">
          <td style="padding:4px 10px"><b>ATO</b></td>
          <td style="padding:4px 10px">Enter at open — fastest fill at 9:15 AM price</td>
          <td style="padding:4px 10px;color:#555">No price control — can gap up/down</td>
        </tr>
        <tr>
          <td style="padding:4px 10px"><b>ATC</b></td>
          <td style="padding:4px 10px">Enter at official closing price</td>
          <td style="padding:4px 10px;color:#555">No price control — closing only</td>
        </tr>
        <tr style="background:#f9f9f9">
          <td style="padding:4px 10px"><b>MP</b></td>
          <td style="padding:4px 10px">HNX stocks only — immediate market fill</td>
          <td style="padding:4px 10px;color:#555">HOSE does not support MP orders</td>
        </tr>
      </table>
    </div>

    <b style="font-family:sans-serif;font-size:14px">Today's buy orders to place:</b>
    {ticker_cards}
    """


def build_html(ranked: dict, timeframe: str, review) -> str:
    today = date.today().strftime("%B %d, %Y")

    # review is a dict (Claude result), None (no credits), or str (legacy fallback)
    if isinstance(review, dict):
        market_summary = review.get("market_summary", "")
        sectors_note = review.get("sectors_to_watch", "")
        top_picks = review.get("top_picks", [])
        confidence = review.get("confidence", "")
        market_ctx = review.get("_market_ctx", {})
    else:
        market_summary = ""
        sectors_note = ""
        top_picks = []
        confidence = ""
        market_ctx = {}

    vnindex_data = market_ctx.get("vnindex", {}) if isinstance(market_ctx, dict) else {}
    sectors_data = market_ctx.get("sectors", {}) if isinstance(market_ctx, dict) else {}
    breadth_data = market_ctx.get("breadth", {}) if isinstance(market_ctx, dict) else {}

    summary_block = ""
    if market_summary:
        conf_badge = (
            f'<span style="font-size:11px;color:#888"> [Analysis confidence: {confidence}]</span>'
            if confidence else ""
        )
        summary_block = f"""
        <div style="background:#f7f9fc;border-left:4px solid #4a90d9;padding:14px 18px;
                    margin:16px 0;font-family:sans-serif;font-size:14px;color:#333">
          {market_summary} {conf_badge}
        </div>"""
    if sectors_note:
        summary_block += f"""
        <div style="font-family:sans-serif;font-size:13px;color:#555;margin:8px 0;
                    font-style:italic">📌 {sectors_note}</div>"""

    return f"""<html><body style="font-family:sans-serif;max-width:900px;margin:auto;padding:24px">
      <h2 style="color:#1a1a1a;border-bottom:2px solid #eee;padding-bottom:10px">
        🇻🇳 VN Stock Daily Brief
        <span style="font-size:14px;font-weight:normal;color:#888">— {timeframe.upper()} · {today}</span>
      </h2>
      {_vnindex_html(vnindex_data)}
      {_breadth_html(breadth_data)}
      {summary_block}
      {_top_picks_html(top_picks)}
      {_capital_warning_html(ranked, market_ctx.get("capital", 0) if isinstance(market_ctx, dict) else 0)}
      {_vcbs_guide_html(ranked)}
      {_sector_heatmap_html(sectors_data)}
      {_signal_table_html(ranked.get('buy', []), 'buy')}
      {_signal_table_html(ranked.get('sell', []), 'sell')}
      <p style="color:#bbb;font-size:11px;margin-top:36px;border-top:1px solid #eee;padding-top:12px">
        Auto-generated by analyst-stock-vn · VN30 universe · Not financial advice. ·
        Signals: RSI · MACD · Stochastic · ADX · Bollinger · MA20/60 · Volume
      </p>
    </body></html>"""


def send(ranked: dict, timeframe: str, review, market_ctx: dict = None) -> None:
    gmail_user = os.environ.get("GMAIL_USER", "")
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    to_addr = os.environ.get("NOTIFY_EMAIL", "")

    if not all([gmail_user, gmail_app_password, to_addr]):
        raise RuntimeError("Set GMAIL_USER, GMAIL_APP_PASSWORD, NOTIFY_EMAIL in .env")

    today = date.today().strftime("%Y-%m-%d")
    buy_count = len(ranked.get("buy", []))
    top_buy = ranked["buy"][0]["ticker"] if ranked.get("buy") else "—"

    # inject market_ctx into review dict for the HTML builder
    if isinstance(review, dict) and market_ctx:
        review["_market_ctx"] = market_ctx

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[VN Stock] {today} · {buy_count} Buy signals · Top: {top_buy}"
    msg["From"] = f"VN Stock Scanner <{gmail_user}>"
    msg["To"] = to_addr

    # Plain text fallback
    if isinstance(review, dict):
        plain = review.get("market_summary", "") + "\n\n"
        for p in review.get("top_picks", []):
            plain += (f"{p['action']} {p['ticker']}: {p['thesis']} "
                      f"Entry {p.get('entry',0):,.0f} Stop {p.get('stop_loss',0):,.0f} "
                      f"Target {p.get('target',0):,.0f}\n")
    else:
        plain = str(review)

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(build_html(ranked, timeframe, review), "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_app_password)
        server.sendmail(gmail_user, to_addr, msg.as_string())
