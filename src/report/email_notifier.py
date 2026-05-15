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
        if "signals" in item:
            reasons = "; ".join(
                s["reason"] for s in item["signals"] if s["action"] != "HOLD"
            )[:120]
        else:
            snaps = item.get("snapshots", [])
            snap_str = "  ".join(
                f"{s['date'][5:]}:{s['action'][0]}({s['composite']:+.2f})"
                for s in snaps
            )
            reasons = (f"streak={item.get('streak_days', 0)}d  "
                       f"consistency={item.get('consistency_score', 0)*100:.0f}%  "
                       f"{snap_str}")[:120]
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
    buys = [i for i in ranked.get("buy", []) if i["recommendation"] in ("BUY", "STRONG BUY")][:1]
    if not buys:
        buys = ranked.get("buy", [])[:1]
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

    return f"""
    <h3 style="color:#1565c0;margin-top:28px">📱 Place Order — VCBS Mobies</h3>
    {_market_status_banner()}
    {ticker_cards}
    """


def _portfolio_html(statuses: list) -> str:
    """Render open positions table with SELL alerts."""
    if not statuses:
        return ""

    has_action = any(s.needs_action for s in statuses)

    alert_banner = ""
    if has_action:
        alert_banner = """
    <div style="background:#c0392b;color:white;padding:14px 18px;border-radius:6px;
                margin-bottom:12px;font-family:sans-serif;font-size:15px;font-weight:bold">
      🚨 ACTION REQUIRED — A position has hit its stop loss or target.
      Open VCBS Mobies and place a SELL order today.
    </div>"""

    rows = ""
    for s in statuses:
        p = s.position
        pnl_color = "#1a7f4b" if s.pnl_pct >= 0 else "#c0392b"

        if s.status == "SELL_STOP":
            row_bg = "#ffebee"
            action_cell = '<b style="color:#c0392b;font-size:14px">⚠️ SELL (Stop Hit)</b>'
        elif s.status == "SELL_TARGET":
            row_bg = "#e8f5e9"
            action_cell = '<b style="color:#1a7f4b;font-size:14px">🎯 SELL (Target Hit)</b>'
        elif s.status == "WATCH":
            row_bg = "#fff8e1"
            action_cell = '<span style="color:#e67e22;font-weight:bold">⚡ WATCH</span>'
        else:
            row_bg = "white"
            action_cell = '<span style="color:#555">✓ HOLD</span>'

        qty_note = f"<br><span style='color:#888;font-size:11px'>{p.qty:,} shares</span>" if p.qty else ""
        pnl_vnd = f"<br><span style='font-size:11px'>{s.pnl_vnd:+,.0f} ₫</span>" if p.qty else ""

        rows += f"""
        <tr style="background:{row_bg};border-bottom:1px solid #eee">
          <td style="padding:10px;font-weight:bold;font-size:15px">{p.ticker}</td>
          <td style="padding:10px;font-size:12px;color:#555">{p.buy_date}<br>
            <b>{p.buy_price:,.0f} ₫</b>{qty_note}</td>
          <td style="padding:10px;font-size:15px;font-weight:bold">{s.current_price:,.0f} ₫</td>
          <td style="padding:10px;color:{pnl_color};font-weight:bold">{s.pnl_pct:+.2f}%{pnl_vnd}</td>
          <td style="padding:10px;font-size:12px">
            <span style="color:#c0392b">{p.stop_loss:,.0f}</span>
            <br><span style="color:#888;font-size:11px">{s.pct_from_stop:+.1f}% away</span>
          </td>
          <td style="padding:10px;font-size:12px">
            <span style="color:#1a7f4b">{p.target:,.0f}</span>
            <br><span style="color:#888;font-size:11px">{s.pct_from_target:+.1f}% away</span>
          </td>
          <td style="padding:10px">{action_cell}</td>
        </tr>"""

    return f"""
    <h3 style="color:#333;margin-top:28px">💼 My Portfolio</h3>
    {alert_banner}
    <table style="border-collapse:collapse;width:100%;font-family:sans-serif;font-size:13px">
      <thead>
        <tr style="background:#f0f0f0">
          <th style="padding:8px 10px;text-align:left">Ticker</th>
          <th style="padding:8px 10px;text-align:left">Bought</th>
          <th style="padding:8px 10px;text-align:left">Price Now</th>
          <th style="padding:8px 10px;text-align:left">P&amp;L</th>
          <th style="padding:8px 10px;text-align:left">Stop Loss</th>
          <th style="padding:8px 10px;text-align:left">Target</th>
          <th style="padding:8px 10px;text-align:left">Action</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>"""


def _regime_banner_html(regime_result: dict) -> str:
    """Full-width regime banner with key metrics."""
    if not regime_result or regime_result.get("regime") in (None, "UNKNOWN"):
        return ""
    regime = regime_result.get("regime", "UNKNOWN")
    conf = regime_result.get("confidence", 0)
    adx = regime_result.get("adx")
    r1m = regime_result.get("r1m_pct")
    dd = regime_result.get("drawdown_from_52w_pct")
    ma50 = regime_result.get("ma50")
    ma200 = regime_result.get("ma200")

    color_map = {
        "BULL": ("#1a7f4b", "#e8f5e9", "↑ BULL MARKET"),
        "BEAR": ("#c0392b", "#ffebee", "↓ BEAR MARKET"),
        "SIDEWAYS": ("#e67e22", "#fff8e1", "↔ SIDEWAYS"),
        "UNKNOWN": ("#888", "#f5f5f5", "? UNKNOWN"),
    }
    border, bg, label = color_map.get(regime, color_map["UNKNOWN"])

    metrics = []
    if conf is not None:
        metrics.append(f"Confidence: <b>{conf:.0%}</b>")
    if adx is not None:
        metrics.append(f"ADX: <b>{adx:.1f}</b>")
    if r1m is not None:
        color = "#1a7f4b" if r1m >= 0 else "#c0392b"
        metrics.append(f"1M return: <b style='color:{color}'>{r1m:+.1f}%</b>")
    if dd is not None:
        metrics.append(f"From 52w high: <b style='color:#c0392b'>{dd:.1f}%</b>")
    if ma50 and ma200:
        golden = ma50 > ma200
        cross = "Golden Cross ✓" if golden else "Death Cross ✗"
        cross_color = "#1a7f4b" if golden else "#c0392b"
        metrics.append(f"MA: <span style='color:{cross_color}'><b>{cross}</b></span>")

    metrics_str = "&nbsp;·&nbsp;".join(metrics)
    return f"""
    <div style="background:{bg};border-left:5px solid {border};padding:12px 18px;
                margin-bottom:14px;font-family:sans-serif;font-size:13px;border-radius:0 6px 6px 0">
      <span style="color:{border};font-size:16px;font-weight:bold">{label}</span>
      &nbsp;&nbsp;{metrics_str}
    </div>"""


def _sector_rotation_html(rotation_result: dict) -> str:
    """Compact sector rotation summary with ROTATE_IN / ROTATE_OUT tags."""
    if not rotation_result or not rotation_result.get("rankings"):
        return ""
    rankings = rotation_result["rankings"]
    rotate_in = rotation_result.get("rotate_into", [])
    rotate_out = rotation_result.get("rotate_out_of", [])

    rows = ""
    for r in rankings[:8]:
        sector = r.get("sector", "?")
        score = r.get("score", 0)
        ret_1m = r.get("avg_return_1m", 0)
        signal = r.get("signal", "HOLD")
        if signal == "ROTATE_IN":
            sig_html = '<span style="color:#1a7f4b;font-weight:bold">↑ ROTATE IN</span>'
        elif signal == "ROTATE_OUT":
            sig_html = '<span style="color:#c0392b;font-weight:bold">↓ ROTATE OUT</span>'
        else:
            sig_html = '<span style="color:#888">→ HOLD</span>'
        ret_color = "#1a7f4b" if ret_1m >= 0 else "#c0392b"
        rows += f"""<tr style="border-bottom:1px solid #eee">
          <td style="padding:5px 10px">{sector}</td>
          <td style="padding:5px 10px;text-align:right;color:{ret_color};font-weight:bold">{ret_1m:+.1f}%</td>
          <td style="padding:5px 10px">{sig_html}</td>
        </tr>"""

    in_str = ", ".join(rotate_in[:3]) if rotate_in else "—"
    out_str = ", ".join(rotate_out[:3]) if rotate_out else "—"

    return f"""
    <h3 style="color:#333;margin-top:24px">🔄 Sector Rotation</h3>
    <div style="font-family:sans-serif;font-size:13px;margin-bottom:8px">
      <span style="color:#1a7f4b">Rotate into:</span> <b>{in_str}</b>
      &nbsp;&nbsp;
      <span style="color:#c0392b">Rotate out of:</span> <b>{out_str}</b>
    </div>
    <table style="border-collapse:collapse;font-family:sans-serif;font-size:13px;width:380px">
      <thead>
        <tr style="background:#f0f0f0">
          <th style="padding:5px 10px;text-align:left">Sector</th>
          <th style="padding:5px 10px;text-align:right">1M Return</th>
          <th style="padding:5px 10px;text-align:left">Signal</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>"""


def _risk_summary_html(risk_report: dict) -> str:
    """Compact risk metrics block."""
    if not risk_report:
        return ""
    pvar = risk_report.get("portfolio_var_95", {})
    pbeta = risk_report.get("portfolio_beta", {})
    flags = risk_report.get("flags", [])

    if not pvar and not pbeta:
        return ""

    items = []
    if pvar.get("var_pct") is not None:
        items.append(f"VaR 95% (1d): <b style='color:#c0392b'>{pvar['var_pct']:.2f}%</b>")
    if pvar.get("cvar_pct") is not None:
        items.append(f"CVaR 95%: <b style='color:#c0392b'>{pvar['cvar_pct']:.2f}%</b>")
    if pbeta.get("portfolio_beta") is not None:
        b = pbeta["portfolio_beta"]
        bcolor = "#c0392b" if b > 1.3 else ("#1a7f4b" if b < 0.8 else "#333")
        items.append(f"Beta: <b style='color:{bcolor}'>{b:.2f}</b>")
    interp = pbeta.get("interpretation", "")
    if interp:
        items.append(f"Profile: <b>{interp}</b>")

    flags_html = ""
    if flags:
        flag_items = "".join(f"<li style='color:#c0392b'>{f}</li>" for f in flags)
        flags_html = f"<ul style='margin:6px 0;padding-left:20px;font-size:12px'>{flag_items}</ul>"

    metrics_str = "&nbsp;·&nbsp;".join(items)
    return f"""
    <div style="background:#fff3e0;border-left:4px solid #e65100;padding:12px 16px;
                margin:14px 0;font-family:sans-serif;font-size:13px">
      <b>📊 Portfolio Risk</b>&nbsp;&nbsp;{metrics_str}
      {flags_html}
    </div>"""


def _institutional_brief_html(brief: dict) -> str:
    """
    Primary section of the pre-market email.
    Shows Head of Trading directive + decision table + agent consensus.
    """
    if not brief or not brief.get("head_directive"):
        return ""

    directive = brief.get("head_directive", "")
    decisions = brief.get("decisions", [])
    summaries = brief.get("agent_summaries", {})
    risk_alerts = brief.get("risk_alerts", [])
    themes = brief.get("key_themes", [])
    exec_note = brief.get("execution_note", "")

    # ── Directive banner ──────────────────────────────────────────────────────
    stance = brief.get("market_stance", "")
    stance_upper = stance.upper() if stance else ""
    if "BULL" in stance_upper:
        dir_color, dir_bg = "#1a7f4b", "#e8f5e9"
    elif "BEAR" in stance_upper:
        dir_color, dir_bg = "#c0392b", "#ffebee"
    else:
        dir_color, dir_bg = "#1565c0", "#e3f2fd"

    directive_html = f"""
    <div style="background:{dir_bg};border-left:5px solid {dir_color};padding:14px 18px;
                margin-bottom:16px;font-family:sans-serif;border-radius:0 6px 6px 0">
      <div style="font-size:11px;color:{dir_color};font-weight:bold;letter-spacing:1px;
                  text-transform:uppercase;margin-bottom:4px">Head of Trading — Daily Directive</div>
      <div style="font-size:15px;font-weight:bold;color:#1a1a1a">{directive}</div>
    </div>"""

    # ── Decision table ────────────────────────────────────────────────────────
    action_styles = {
        "BUY":   ("#1a7f4b", "#e8f5e9", "↑ BUY"),
        "SELL":  ("#c0392b", "#ffebee", "↓ SELL"),
        "EXIT":  ("#c0392b", "#ffebee", "✕ EXIT"),
        "TRIM":  ("#e67e22", "#fff8e1", "◀ TRIM"),
        "HOLD":  ("#555",    "#f9f9f9", "— HOLD"),
        "WATCH": ("#888",    "#f5f5f5", "◎ WATCH"),
    }

    rows = ""
    for d in decisions:
        action = d.get("action", "HOLD")
        ticker = d.get("ticker", "?")
        entry = d.get("entry", 0)
        stop = d.get("stop", 0)
        target = d.get("target", 0)
        qty = d.get("qty", 0)
        conviction = d.get("conviction", "LOW")
        rationale = d.get("rationale", "")
        agent = d.get("agent", "")

        ac, abg, alabel = action_styles.get(action, action_styles["HOLD"])
        conv_color = {"HIGH": "#1a7f4b", "MEDIUM": "#e67e22", "LOW": "#888"}.get(conviction, "#888")

        entry_str = f"{entry:,.0f} ₫" if entry else "—"
        stop_str = f"<span style='color:#c0392b'>{stop:,.0f} ₫</span>" if stop else "—"
        target_str = f"<span style='color:#1a7f4b'>{target:,.0f} ₫</span>" if target else "—"
        qty_str = f"{qty:,}" if qty else "—"

        rows += f"""
        <tr style="border-bottom:1px solid #eee">
          <td style="padding:10px 12px;background:{abg}">
            <span style="color:{ac};font-weight:bold;font-size:13px">{alabel}</span>
          </td>
          <td style="padding:10px 12px;font-weight:bold;font-size:16px">{ticker}</td>
          <td style="padding:10px 12px;font-size:13px">{entry_str}</td>
          <td style="padding:10px 12px;font-size:13px">{stop_str}</td>
          <td style="padding:10px 12px;font-size:13px">{target_str}</td>
          <td style="padding:10px 12px;text-align:center">
            <span style="color:{conv_color};font-size:12px;font-weight:bold">{conviction}</span>
          </td>
          <td style="padding:10px 12px;font-size:12px;color:#555;max-width:200px">{rationale}</td>
        </tr>"""

    table_html = ""
    if rows:
        table_html = f"""
        <h3 style="color:#1a1a1a;margin:20px 0 8px 0;font-size:14px;
                   letter-spacing:0.5px;text-transform:uppercase;font-family:sans-serif">
          Today's Decisions
        </h3>
        <table style="border-collapse:collapse;width:100%;font-family:sans-serif;font-size:13px;
                      border:1px solid #e0e0e0;border-radius:6px;overflow:hidden">
          <thead>
            <tr style="background:#f5f5f5;border-bottom:2px solid #ddd">
              <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">ACTION</th>
              <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">TICKER</th>
              <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">ENTRY</th>
              <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">STOP</th>
              <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">TARGET</th>
              <th style="padding:8px 12px;text-align:center;font-size:11px;color:#666">CONVICTION</th>
              <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">RATIONALE</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>"""

    # ── Risk alerts ───────────────────────────────────────────────────────────
    alerts_html = ""
    if risk_alerts:
        items = "".join(
            f'<div style="margin:4px 0">⚠ {a}</div>' for a in risk_alerts
        )
        alerts_html = f"""
        <div style="background:#fff3e0;border-left:4px solid #e65100;padding:12px 16px;
                    margin:14px 0;font-family:sans-serif;font-size:13px;color:#c0392b">
          <b>Risk Alerts</b><br>{items}
        </div>"""

    # ── Agent consensus ───────────────────────────────────────────────────────
    agent_labels = [
        ("macro",      "Macro"),
        ("research",   "Research"),
        ("quant",      "Quant"),
        ("portfolio",  "Portfolio"),
        ("risk",       "Risk Mgmt"),
        ("compliance", "Compliance"),
    ]
    consensus_rows = ""
    for key, label in agent_labels:
        note = summaries.get(key, "")
        if not note:
            continue
        is_clear = "CLEAR" in note.upper() and key == "compliance"
        is_alert = any(w in note.upper() for w in ("ALERT", "BREACH", "WARN", "REJECT"))
        row_color = "#f5f5f5"
        note_color = "#c0392b" if is_alert else ("#1a7f4b" if is_clear else "#333")
        consensus_rows += f"""
        <tr style="border-bottom:1px solid #eee;background:{row_color}">
          <td style="padding:7px 12px;font-size:11px;color:#888;font-weight:bold;
                     white-space:nowrap;width:90px">{label}</td>
          <td style="padding:7px 12px;font-size:13px;color:{note_color}">{note}</td>
        </tr>"""

    consensus_html = ""
    if consensus_rows:
        consensus_html = f"""
        <h3 style="color:#1a1a1a;margin:20px 0 8px 0;font-size:14px;
                   letter-spacing:0.5px;text-transform:uppercase;font-family:sans-serif">
          Agent Consensus
        </h3>
        <table style="border-collapse:collapse;width:100%;font-family:sans-serif;
                      border:1px solid #e0e0e0">
          <tbody>{consensus_rows}</tbody>
        </table>"""

    # ── Themes + execution note ───────────────────────────────────────────────
    footer_bits = []
    if themes:
        footer_bits.append("Key themes: " + " · ".join(f"<b>{t}</b>" for t in themes))
    if exec_note:
        footer_bits.append(f"Execution: {exec_note}")
    footer_html = ""
    if footer_bits:
        footer_html = f"""
        <div style="margin-top:14px;font-family:sans-serif;font-size:12px;color:#555;
                    padding:10px 14px;background:#f9f9f9;border-radius:4px">
          {" &nbsp;|&nbsp; ".join(footer_bits)}
        </div>"""

    return directive_html + table_html + alerts_html + consensus_html + footer_html


def build_html(
    ranked: dict,
    timeframe: str,
    review,
    portfolio_statuses: list = None,
    regime_result: dict = None,
    sector_rotation: dict = None,
    risk_report: dict = None,
    institutional_brief: dict = None,
) -> str:
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

    return f"""<html><body style="font-family:sans-serif;max-width:820px;margin:auto;padding:20px">
      <table style="width:100%;margin-bottom:14px;border-collapse:collapse">
        <tr>
          <td style="font-size:18px;font-weight:bold;color:#1a1a1a">
            🇻🇳 VN Trading Desk — Pre-Market Brief
          </td>
          <td style="text-align:right;font-size:12px;color:#888;vertical-align:bottom">
            {timeframe.upper()} &nbsp;·&nbsp; {today}
          </td>
        </tr>
      </table>
      <hr style="border:none;border-top:2px solid #eee;margin:0 0 16px 0">

      {_institutional_brief_html(institutional_brief or {})}

      {_portfolio_html(portfolio_statuses or [])}

      {_capital_warning_html(ranked, market_ctx.get("capital", 0) if isinstance(market_ctx, dict) else 0)}
      {_vcbs_guide_html(ranked)}

      <div style="margin-top:20px;padding-top:14px;border-top:1px solid #eee">
        {_regime_banner_html(regime_result or {})}
        {_vnindex_html(vnindex_data)}
        {_sector_rotation_html(sector_rotation or {})}
      </div>

      <p style="color:#bbb;font-size:11px;margin-top:24px;border-top:1px solid #eee;padding-top:10px">
        Institutional Trading Desk · VN30 universe · Not financial advice · {today}
      </p>
    </body></html>"""


def send(
    ranked: dict,
    timeframe: str,
    review,
    market_ctx: dict = None,
    portfolio_statuses: list = None,
    regime_result: dict = None,
    sector_rotation: dict = None,
    risk_report: dict = None,
    institutional_brief: dict = None,
) -> None:
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

    # Subject: sell alert takes priority; else show head directive
    sell_alerts = [s for s in (portfolio_statuses or []) if s.needs_action]
    if sell_alerts:
        tickers = ", ".join(s.position.ticker for s in sell_alerts)
        subject = f"🚨 SELL ALERT: {tickers} — {today}"
    elif institutional_brief and institutional_brief.get("head_directive"):
        directive_short = institutional_brief["head_directive"][:60]
        subject = f"[VN Desk] {today} · {directive_short}"
    else:
        subject = f"[VN Desk] {today} · {buy_count} signals · Top: {top_buy}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"VN Trading Desk <{gmail_user}>"
    msg["To"] = to_addr

    # Plain text fallback
    plain = ""
    if sell_alerts:
        plain += "SELL ALERT\n" + "\n".join(
            f"  {s.position.ticker}: {s.message}" for s in sell_alerts
        ) + "\n\n"
    if institutional_brief:
        plain += institutional_brief.get("head_directive", "") + "\n\n"
        for d in institutional_brief.get("decisions", []):
            plain += (f"{d.get('action','?'):5s} {d.get('ticker','?'):6s} "
                      f"entry={d.get('entry',0):,.0f}  stop={d.get('stop',0):,.0f}  "
                      f"target={d.get('target',0):,.0f}  [{d.get('conviction','?')}]\n")
    elif isinstance(review, dict):
        plain += review.get("market_summary", "") + "\n"

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(
        build_html(ranked, timeframe, review, portfolio_statuses,
                   regime_result, sector_rotation, risk_report, institutional_brief),
        "html",
    ))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_app_password)
        server.sendmail(gmail_user, to_addr, msg.as_string())
