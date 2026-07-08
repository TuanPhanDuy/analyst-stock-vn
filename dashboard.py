#!/usr/bin/env python3
"""
Streamlit dashboard for analyst-stock-vn.

Launch:
    streamlit run dashboard.py

Shows:
  - Market regime (BULL/BEAR/SIDEWAYS)
  - Sector rotation heatmap
  - Top buy/sell signals table
  - Portfolio P&L tracker
  - Foreign flow summary
  - Signal accuracy metrics
"""
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

st.set_page_config(
    page_title="VN Stock Analyst",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Config ───────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def load_cfg() -> dict:
    with open("config.yaml") as f:
        return yaml.safe_load(f)


# ── Data loaders ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner="Fetching market data...")
def fetch_data(tickers: list, days: int = 400) -> dict:
    from src.fetcher.yfinance_client import get_ohlcv_bulk
    return get_ohlcv_bulk(tickers, days=days)


@st.cache_data(ttl=600, show_spinner="Running signal analysis...")
def run_signals(tickers_key: str, days: int = 400) -> tuple:
    cfg = load_cfg()
    tickers = cfg["watchlist"]["vn30"]
    ohlcv_map = fetch_data(tickers, days=days)

    from src.signals.multiday import build_multiday_scores, rank_by_conviction
    from src.indicators.technical import entry_exit_levels

    analyses = build_multiday_scores(ohlcv_map, cfg, max_workers=8)
    ranked = rank_by_conviction(analyses, top_n=10, min_streak=2)
    entry_levels = {t: entry_exit_levels(df) for t, df in ohlcv_map.items() if df is not None}
    return ohlcv_map, analyses, ranked, entry_levels


@st.cache_data(ttl=3600, show_spinner="Detecting market regime...")
def get_regime(tickers_key: str) -> dict:
    cfg = load_cfg()
    tickers = cfg["watchlist"]["vn30"]
    ohlcv_map = fetch_data(tickers)
    from src.market_context import _synthetic_index
    from src import regime as regime_mod
    idx_df = _synthetic_index(ohlcv_map)
    return regime_mod.detect(idx_df, cfg.get("regime", {}))


@st.cache_data(ttl=1800, show_spinner="Analyzing sectors...")
def get_sector_rotation(tickers_key: str) -> dict:
    cfg = load_cfg()
    tickers = cfg["watchlist"]["vn30"]
    ohlcv_map = fetch_data(tickers)
    from src import sector_rotation as sr
    return sr.analyze(ohlcv_map, cfg.get("sectors", {}), cfg)


@st.cache_data(ttl=86400, show_spinner="Loading signal accuracy...")
def get_accuracy() -> dict:
    from src.ml_signal import accuracy_report
    return accuracy_report()


@st.cache_data(ttl=1800, show_spinner="Loading NAV history...")
def get_nav() -> tuple:
    from src.portfolio_tracker import equity_curve, summary
    # benchmark=False avoids a network call inside the cached dashboard load.
    return summary(benchmark=False), equity_curve()


@st.cache_data(ttl=1800, show_spinner="Loading realized P&L...")
def get_realized() -> tuple:
    from src.realized_pnl import load_history, summary
    return summary(), load_history()


def load_portfolio() -> list:
    from src.portfolio_monitor import load_portfolio as _lp
    return _lp()


# ── Sidebar ───────────────────────────────────────────────────────────────────

def sidebar() -> dict:
    st.sidebar.title("VN Stock Analyst")
    st.sidebar.caption(f"Today: {date.today()}")

    cfg = load_cfg()
    watchlist_choice = st.sidebar.selectbox(
        "Watchlist",
        options=["vn30", "vn100", "hnx30"],
        index=0,
    )
    days = st.sidebar.slider("History (days)", min_value=100, max_value=500, value=400, step=50)
    min_streak = st.sidebar.slider("Min signal streak (days)", min_value=1, max_value=5, value=2)
    top_n = st.sidebar.slider("Top N signals", min_value=5, max_value=20, value=10)

    st.sidebar.markdown("---")
    if st.sidebar.button("Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    return {
        "watchlist": watchlist_choice,
        "tickers": cfg["watchlist"].get(watchlist_choice, cfg["watchlist"]["vn30"]),
        "days": days,
        "min_streak": min_streak,
        "top_n": top_n,
    }


# ── Regime panel ─────────────────────────────────────────────────────────────

def show_regime(regime: dict) -> None:
    r = regime.get("regime", "UNKNOWN")
    conf = regime.get("confidence", 0.0)
    color = {"BULL": "🟢", "BEAR": "🔴", "SIDEWAYS": "🟡", "UNKNOWN": "⚪"}.get(r, "⚪")

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Regime", f"{color} {r}", f"{conf:.0%} confidence")
    col2.metric("1M Return", f"{regime.get('r1m_pct', 0):+.1f}%")
    col3.metric("3M Return", f"{regime.get('r3m_pct', 0):+.1f}%")
    col4.metric("ADX", f"{regime.get('adx', 0):.0f}", "trending" if regime.get("is_trending") else "ranging")
    col5.metric("52W Drawdown", f"{regime.get('drawdown_from_52w_pct', 0):.1f}%")
    st.caption(regime.get("description", ""))


# ── Sector rotation panel ─────────────────────────────────────────────────────

def show_sector_rotation(sr_result: dict) -> None:
    rankings = sr_result.get("rankings", [])
    if not rankings:
        st.info("No sector data available.")
        return

    st.caption(sr_result.get("summary", ""))

    rows = []
    for e in rankings:
        sig = e.get("rotation_signal", "HOLD")
        sig_icon = {"ROTATE_IN": "▲ IN", "ROTATE_OUT": "▼ OUT", "HOLD": "— HOLD"}.get(sig, sig)
        rows.append({
            "Rank": e["rank"],
            "Sector": e["sector"],
            "Signal": sig_icon,
            "1M Avg %": e.get("avg_return_1m_pct", 0),
            "3M Avg %": e.get("avg_return_3m_pct", 0),
            "Alpha 1M": e.get("alpha_1m_pct", 0),
            "Above MA20 %": e.get("above_ma20_pct", 0),
            "Score": e.get("score", 0),
            "Label": e.get("label", ""),
        })

    df = pd.DataFrame(rows)

    def _color_signal(val):
        if "IN" in str(val):
            return "background-color: #d4edda; color: #155724"
        if "OUT" in str(val):
            return "background-color: #f8d7da; color: #721c24"
        return ""

    def _color_return(val):
        try:
            v = float(val)
            if v > 3:
                return "color: #28a745"
            if v < -3:
                return "color: #dc3545"
        except (ValueError, TypeError):
            pass
        return ""

    styled = (
        df.style
        .applymap(_color_signal, subset=["Signal"])
        .applymap(_color_return, subset=["1M Avg %", "3M Avg %", "Alpha 1M"])
        .format({
            "1M Avg %": "{:+.1f}%",
            "3M Avg %": "{:+.1f}%",
            "Alpha 1M": "{:+.1f}%",
            "Above MA20 %": "{:.0f}%",
            "Score": "{:+.2f}",
        })
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)


# ── Signals table ─────────────────────────────────────────────────────────────

def show_signals(ranked: dict, entry_levels: dict, action: str) -> None:
    items = ranked.get(action, [])
    if not items:
        st.info(f"No {action.upper()} signals.")
        return

    rows = []
    for item in items:
        el = entry_levels.get(item["ticker"], {})
        rows.append({
            "Ticker": item["ticker"],
            "Price (₫)": f"{item.get('price', 0):,.0f}",
            "Conviction": f"{item.get('conviction_score', 0):+.3f}",
            "Label": item.get("conviction_label", ""),
            "Streak": f"{item.get('streak_days', 0)}d",
            "Consistency": f"{item.get('consistency_score', 0)*100:.0f}%",
            "Rec": item.get("recommendation", ""),
            "Entry (₫)": f"{el.get('entry', 0):,.0f}" if el else "",
            "Stop (₫)": f"{el.get('stop_loss', 0):,.0f}" if el else "",
            "Target (₫)": f"{el.get('target', 0):,.0f}" if el else "",
            "Risk %": f"-{el.get('risk_pct', 0):.1f}%" if el else "",
            "Reward %": f"+{el.get('reward_pct', 0):.1f}%" if el else "",
        })

    df = pd.DataFrame(rows)

    def _color_label(val):
        colors = {"HIGH": "#d4edda", "MEDIUM": "#fff3cd", "LOW": "#f8f9fa"}
        return f"background-color: {colors.get(str(val), '')}"

    styled = df.style.applymap(_color_label, subset=["Label"])
    st.dataframe(styled, use_container_width=True, hide_index=True)


# ── Portfolio panel ───────────────────────────────────────────────────────────

def show_portfolio(ohlcv_map: dict, entry_levels: dict) -> None:
    positions = load_portfolio()
    if not positions:
        st.info("No positions in data/portfolio.json. Edit the file to add positions.")
        return

    from src.portfolio_monitor import analyze_positions
    statuses = analyze_positions(positions, ohlcv_map, entry_levels)

    if not statuses:
        st.info("Could not load price data for portfolio positions.")
        return

    rows = []
    for s in statuses:
        p = s.position
        status_icon = {
            "SELL_STOP": "🔴",
            "SELL_TARGET": "🟢",
            "WATCH": "🟡",
            "HOLD": "⚪",
        }.get(s.status, "")
        rows.append({
            "Ticker": p.ticker,
            "Buy Date": p.buy_date,
            "Buy ₫": f"{p.buy_price:,.0f}",
            "Now ₫": f"{s.current_price:,.0f}",
            "P&L %": s.pnl_pct,
            "P&L ₫": f"{s.pnl_vnd:,.0f}" if p.qty else "—",
            "Status": f"{status_icon} {s.status}",
            # Show the levels actually used for the decision. If the stored
            # stop/target were 0, these are ATR-based fallbacks — flagged with *.
            "Stop ₫": f"{s.effective_stop:,.0f}" + ("*" if s.levels_are_fallback else ""),
            "Target ₫": f"{s.effective_target:,.0f}" + ("*" if s.levels_are_fallback else ""),
            "From Stop": f"{s.pct_from_stop:+.1f}%",
            "From Target": f"{s.pct_from_target:+.1f}%",
        })

    df = pd.DataFrame(rows)

    def _color_pnl(val):
        try:
            v = float(val)
            return "color: #28a745; font-weight:bold" if v > 0 else "color: #dc3545; font-weight:bold"
        except (ValueError, TypeError):
            return ""

    def _color_status(val):
        if "SELL_STOP" in str(val):
            return "background-color: #f8d7da"
        if "SELL_TARGET" in str(val):
            return "background-color: #d4edda"
        if "WATCH" in str(val):
            return "background-color: #fff3cd"
        return ""

    styled = (
        df.style
        .applymap(_color_pnl, subset=["P&L %"])
        .applymap(_color_status, subset=["Status"])
        .format({"P&L %": "{:+.2f}%"})
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)
    if any(s.levels_are_fallback for s in statuses):
        st.caption("\\* Stop/Target recomputed from ATR (no level set in portfolio.json).")

    # Portfolio summary metrics
    pnl_values = [s.pnl_pct for s in statuses]
    if pnl_values:
        m1, m2, m3 = st.columns(3)
        m1.metric("Positions", len(statuses))
        m2.metric("Avg P&L", f"{sum(pnl_values)/len(pnl_values):+.2f}%")
        actions_needed = sum(1 for s in statuses if s.needs_action)
        m3.metric("Actions Required", actions_needed,
                  delta="⚠ Review needed" if actions_needed else None)


# ── Signal accuracy panel ─────────────────────────────────────────────────────

def show_accuracy(acc: dict) -> None:
    if "message" in acc and "total_pending" not in acc:
        st.info(acc["message"])
        return

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Closed Trades", acc.get("total_closed", 0))
    col2.metric("Win Rate", f"{acc.get('overall_win_rate_pct', 0):.1f}%")
    col3.metric("Avg P&L", f"{acc.get('avg_pnl_pct', 0):+.2f}%")
    col4.metric("Pending", acc.get("total_pending", 0))

    if acc.get("by_action"):
        st.markdown("**By Action**")
        rows = [
            {"Action": a, **v}
            for a, v in acc["by_action"].items()
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    if acc.get("top_tickers"):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Top Performers**")
            st.dataframe(pd.DataFrame(acc["top_tickers"]), use_container_width=True, hide_index=True)
        with c2:
            st.markdown("**Worst Performers**")
            st.dataframe(pd.DataFrame(acc["worst_tickers"]), use_container_width=True, hide_index=True)

    ml_status = acc.get("ml_calibrator", "unknown")
    st.caption(f"ML calibrator: {ml_status}")


# ── NAV / equity-curve panel ───────────────────────────────────────────────────

def show_nav(summary: dict, curve: pd.DataFrame) -> None:
    if summary.get("error") or curve is None or curve.empty:
        st.info("No NAV history recorded yet. The daily scan records a snapshot each run.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("NAV", f"{summary.get('nav_current', 0):,.0f} ₫")
    c2.metric("Total Return", f"{summary.get('total_return_pct', 0):+.2f}%")
    c3.metric("Max Drawdown", f"{summary.get('max_drawdown_pct', 0):.2f}%")
    c4.metric("Sharpe", f"{summary.get('sharpe', 0):.2f}")

    st.markdown("**Equity curve**")
    st.line_chart(curve["nav"], height=220)
    st.markdown("**Drawdown**")
    st.area_chart(curve["drawdown"] * 100, height=140)
    st.caption(
        f"{summary.get('trading_days', 0)} snapshots · CAGR "
        f"{summary.get('cagr_pct', 0):+.2f}%"
        + (f" · alpha vs VN-Index {summary['alpha_pct']:+.2f}%"
           if summary.get("alpha_pct") is not None else "")
    )


# ── Realized-P&L panel ─────────────────────────────────────────────────────────

def show_realized(summary: dict, history: list) -> None:
    if summary.get("message"):
        st.info(summary["message"])
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Realized P&L", f"{summary.get('total_realized_vnd', 0):+,.0f} ₫")
    c2.metric("Closed Lots", summary.get("closed_lots", 0))
    c3.metric("Win Rate", f"{summary.get('win_rate_pct', 0):.1f}%")
    c4.metric("Avg P&L", f"{summary.get('avg_pnl_pct', 0):+.2f}%")

    if history:
        rows = [
            {
                "Sell Date": r["sell_date"],
                "Ticker": r["ticker"],
                "Qty": r["qty"],
                "Buy ₫": f"{r['buy_price']:,.0f}",
                "Sell ₫": f"{r['sell_price']:,.0f}",
                "P&L ₫": f"{r['realized_pnl_vnd']:+,.0f}",
                "P&L %": r["pnl_pct"],
            }
            for r in sorted(history, key=lambda x: x["sell_date"], reverse=True)
        ]
        styled = (
            pd.DataFrame(rows).style
            .applymap(
                lambda v: "color:#28a745;font-weight:bold" if v > 0 else "color:#dc3545;font-weight:bold",
                subset=["P&L %"],
            )
            .format({"P&L %": "{:+.2f}%"})
        )
        st.dataframe(styled, use_container_width=True, hide_index=True)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    opts = sidebar()
    tickers = opts["tickers"]
    tickers_key = ",".join(sorted(tickers))

    st.title("VN Stock Analyst Dashboard")
    st.caption(f"Watchlist: {opts['watchlist'].upper()} ({len(tickers)} tickers) · {date.today()}")

    # ── Market Regime ─────────────────────────────────────────────────────────
    st.subheader("Market Regime")
    with st.spinner("Detecting regime..."):
        try:
            regime = get_regime(tickers_key)
            show_regime(regime)
        except Exception as e:
            st.warning(f"Regime detection failed: {e}")

    st.divider()

    # ── Sector Rotation ───────────────────────────────────────────────────────
    st.subheader("Sector Rotation")
    with st.spinner("Analyzing sectors..."):
        try:
            sr_result = get_sector_rotation(tickers_key)
            show_sector_rotation(sr_result)
        except Exception as e:
            st.warning(f"Sector analysis failed: {e}")

    st.divider()

    # ── Signals ───────────────────────────────────────────────────────────────
    st.subheader("Signal Analysis")
    with st.spinner("Running signal analysis..."):
        try:
            ohlcv_map, analyses, ranked, entry_levels = run_signals(tickers_key, opts["days"])
            from src.signals.multiday import rank_by_conviction
            ranked = rank_by_conviction(analyses, top_n=opts["top_n"], min_streak=opts["min_streak"])
        except Exception as e:
            st.error(f"Signal analysis failed: {e}")
            return

    tab_buy, tab_sell, tab_watch = st.tabs(["BUY Signals", "SELL Signals", "WATCH (new today)"])

    with tab_buy:
        show_signals(ranked, entry_levels, "buy")

    with tab_sell:
        show_signals(ranked, entry_levels, "sell")

    with tab_watch:
        if analyses:
            watch_buys = sorted(
                [a for a in analyses if a.conviction_score > 0 and a.streak_days < opts["min_streak"]],
                key=lambda x: x.conviction_score, reverse=True
            )[:10]
            if watch_buys:
                rows = [{"Ticker": a.ticker, "Price": f"{a.current_price:,.0f}",
                          "Score": f"{a.conviction_score:+.3f}", "Rec": a.current_recommendation,
                          "Note": "First signal — confirm tomorrow"} for a in watch_buys]
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            else:
                st.info("No new signals today (all signals already have prior confirmation).")

    st.divider()

    # ── Portfolio ─────────────────────────────────────────────────────────────
    st.subheader("My Portfolio")
    show_portfolio(ohlcv_map, entry_levels)

    st.divider()

    # ── Portfolio Performance (NAV) ───────────────────────────────────────────
    st.subheader("Portfolio Performance (NAV)")
    try:
        nav_summary, nav_curve = get_nav()
        show_nav(nav_summary, nav_curve)
    except Exception as e:
        st.warning(f"NAV panel failed: {e}")

    st.divider()

    # ── Realized P&L ──────────────────────────────────────────────────────────
    st.subheader("Realized P&L (Closed Trades)")
    try:
        realized_summary, realized_history = get_realized()
        show_realized(realized_summary, realized_history)
    except Exception as e:
        st.warning(f"Realized P&L panel failed: {e}")

    st.divider()

    # ── Signal Accuracy ───────────────────────────────────────────────────────
    st.subheader("Signal Accuracy (Historical)")
    acc = get_accuracy()
    show_accuracy(acc)

    st.caption(
        "Data: Yahoo Finance (OHLCV) · VNDirect (foreign flow, events) · "
        "Signals refresh hourly · Dashboard refresh: manual or wait 10min cache"
    )


if __name__ == "__main__":
    main()
