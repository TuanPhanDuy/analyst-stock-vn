# analyst-stock-vn

A Vietnamese stock-market analysis system that generates **buy / sell / hold**
recommendations across daily, monthly, and yearly timeframes. It ingests market
data from Vietnamese financial sources, layers on technical + fundamental
analysis, learns from its own past calls, and delivers ranked signals to your
inbox every morning.

> Prices are in **VND**, volumes in **shares**. Tickers use HOSE/HNX format
> (`VCB`, `FPT`, `HPG`). Signal confidence is a float `0.0–1.0`; only signals
> ≥ the per-timeframe threshold are surfaced.

---

## Table of contents

- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [CLI reference](#cli-reference)
- [Signal timeframes](#signal-timeframes)
- [Conviction scoring & adjustments](#conviction-scoring--adjustments)
- [ML signal calibration](#ml-signal-calibration)
- [Trade logging & outcome feedback loop](#trade-logging--outcome-feedback-loop)
- [Dashboards & reports](#dashboards--reports)
- [Automation](#automation)
- [Configuration](#configuration)
- [Project layout](#project-layout)
- [Testing & linting](#testing--linting)
- [Further reading](#further-reading)

---

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure secrets (copy the example and fill in)
cp .env.example .env
#   ANTHROPIC_API_KEY=...        # for the Claude-powered agents
#   GMAIL_USER / GMAIL_APP_PASSWORD / NOTIFY_EMAIL   # daily email delivery

# 3. Run the morning scan on the VN30 watchlist
python main.py scan --timeframe daily

# 4. Deep-dive a single ticker
python main.py analyze --ticker VCB --timeframe monthly

# 5. Launch the visual dashboard
python main.py dashboard
```

API credentials are always read from environment variables, never hardcoded.
See [`.env.example`](.env.example) for the full list.

---

## How it works

```
                    ┌──────────────┐
   data sources ───▶│   fetcher    │  vnstock / yfinance / scrapers  (cached)
   (vnstock,        └──────┬───────┘
    yfinance,              │  OHLCV, fundamentals, foreign flow, insider, news
    scrapers)             ▼
                    ┌──────────────┐
                    │  indicators  │  RSI, MACD, BB, MA, ATR, volume
                    │ fundamental  │  P/E, P/B, ROE, debt, growth
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │   signals    │  daily / monthly / yearly / multiday / patterns
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐   ┌──────────────────────────┐
                    │   scoring    │◀──│  adjustments:            │
                    │ (conviction) │   │  regime × ML + foreign   │
                    └──────┬───────┘   │  + insider               │
                           │           └──────────────────────────┘
                           ▼
                    ┌──────────────┐
                    │  ranking &   │  top-N by conviction, position sizing
                    │   sizing     │
                    └──────┬───────┘
                           ▼
              ┌────────────┴────────────┐
              ▼                          ▼
      ┌──────────────┐          ┌──────────────┐
      │   reports    │          │  trade log   │──▶ outcome resolution ──▶ ML
      │ email / CLI  │          │ (feedback)   │      (closes the loop)
      │ dashboard    │          └──────────────┘
      └──────────────┘
```

A scan fetches OHLCV in bulk, runs multi-day signal analysis (looking back
several trading-day offsets so a signal must *persist* to rank), computes a
composite conviction score, folds in market-regime / foreign-flow / insider /
ML adjustments, then ranks the top candidates and formats them for output.

---

## CLI reference

All commands run through `main.py`:

| Command | What it does |
|---|---|
| `scan --timeframe {daily,monthly,yearly} --watchlist {vn30,vn100,hnx30}` | Scan a watchlist and rank buy/sell signals |
| `analyze --ticker VCB --timeframe monthly` | Claude-powered deep analysis of one ticker |
| `backtest --days 400 --hold 5` | Walk-forward backtest on cached OHLCV |
| `advise --portfolio data/portfolio.json` | Portfolio rebalancing advice |
| `news --tickers VCB,FPT` | Monitor news/events for tickers |
| `update-outcomes --days 5` | Resolve PENDING signal outcomes from the trade log |
| `regime` | Detect current market regime (BULL / BEAR / SIDEWAYS) |
| `sector` | Sector-rotation analysis |
| `optimize` | Portfolio optimization (min-variance + Kelly) |
| `accuracy` | Signal-accuracy report from trade history |
| `foreign --tickers VCB,FPT` | Foreign-investor flow for tickers |
| `dashboard` | Launch the Streamlit visual dashboard |
| `risk --tickers ... --weights ... --nav ...` | Portfolio risk report: VaR, beta, stress test |
| `alerts {add,list,remove,reset,check}` | Manage price alerts |
| `nav [--no-benchmark]` | Portfolio NAV history & performance vs VN-Index |
| `patterns --ticker VCB` | Chart-pattern + intraday-signal detection |
| `futures` | VN30F futures snapshot & basis signal |
| `peers --ticker VCB [--peers ...]` | Rank a ticker against its sector peers |

Run `python main.py <command> --help` for full flags.

---

## Signal timeframes

| Timeframe | Horizon | Primary signals |
|-----------|---------|-----------------|
| **Daily** | 1–5 days | RSI divergence, MACD crossover, volume spike, intraday breakout |
| **Monthly** | 1–3 months | Moving-average trend, sector momentum, earnings surprise |
| **Yearly** | 6–24 months | P/E vs sector, ROE growth, revenue CAGR, debt reduction |

Each signal module returns a structured dict:
`{ticker, timeframe, action, confidence, reason}`.

**Multi-day conviction.** Rather than trusting a single day's reading, `scan`
evaluates signals across several trading-day offsets (`multiday.lookback_offsets`,
default `[0, 1, 3, 5]`). A signal must persist for `min_streak` consecutive days
(default 2) to be ranked, and consistency across offsets earns up to a +30 %
conviction bonus.

---

## Conviction scoring & adjustments

The number that actually drives **ranking and position sizing** is the
*adjusted conviction*, computed in [`src/scoring/adjustments.py`](src/scoring/adjustments.py):

```
adjusted = base_conviction × regime_mult × ml_factor + foreign_delta + insider_delta
```

- **Regime multiplier** — a BUY in a BULL market is scaled up; in a BEAR market,
  down (multiplicative). From `src/regime.py`.
- **ML factor** — learned win-probability calibration (multiplicative, see below).
- **Foreign-flow delta** — additive confirmation bounded to ±0.15, signed
  bullish-positive. Heavy foreign selling dampens a BUY.
- **Insider delta** — additive confirmation bounded to ±0.20. Heavy insider
  selling can flip a marginal BUY.

Each factor is toggled independently in `config.yaml → signal_adjustments`.
These signals were previously *display-only* (they appeared in tables and
emails but never changed the ranking); they are now wired into the score.

---

## ML signal calibration

[`src/ml_signal.py`](src/ml_signal.py) trains a classifier on the system's own
resolved trades (`data/signals.jsonl`) and adjusts raw confidence using learned
win-probability estimates.

- **Model preference:** LightGBM → scikit-learn RandomForest → no model
  (raw confidence unchanged).
- **Features:** confidence, composite score, action, streak days, consistency.
- **Label:** profitable (`outcome_pnl_pct > 0`) vs not.
- **Safe by default:** calibration only applies when a walk-forward
  (`TimeSeriesSplit`) out-of-sample check shows genuine skill —
  **OOS AUC ≥ 0.55 and a Brier score better than the base rate**. Otherwise the
  raw confidence passes through untouched. Requires `min_samples` (default 20)
  closed signals before it activates at all.

Tune via `config.yaml → ml_signal`.

---

## Trade logging & outcome feedback loop

The system learns from itself:

1. Every ranked signal is written to `data/signals.jsonl` as `PENDING`.
2. `update-outcomes` (also run automatically at the start of each scan) walks
   forward `trade_log.hold_days` and resolves each signal to a
   target-hit / stop-hit / expiry outcome with a realized `pnl_pct`.
3. The ML calibrator retrains on those resolved outcomes.

Trades are recorded via `scripts/record_trade.py` (BUY/SELL). On a SELL, closed
lots are booked to the **realized-P&L ledger** (`src/realized_pnl.py` →
`data/realized_pnl.jsonl`) so booked gains aren't lost when a position leaves
`portfolio.json`.

---

## Dashboards & reports

- **Streamlit dashboard** (`python main.py dashboard` / `dashboard.py`) —
  signal tables, NAV / equity-curve, and realized-P&L panels.
- **Email** — the daily scan emails ranked signals and portfolio sell alerts
  (see [Automation](#automation)).
- **CLI table / JSON** — `src/report/formatter.py`.
- **Telegram** — `src/report/telegram.py` (optional).
- **Mobile trade-log UI** — [`docs/index.html`](docs/index.html), a
  GitHub-Pages-hosted form that submits trades through a Cloudflare Worker
  (`workers/trade-proxy.js`) into the `record_trade` GitHub Action.

---

## Automation

GitHub Actions under [`.github/workflows/`](.github/workflows/):

| Workflow | Trigger | Purpose |
|---|---|---|
| `daily_scan.yml` | schedule | Run the daily scan and email results |
| `market_open_advisor.yml` | schedule | Market-open advisory (`scripts/market_open_advisor.py`) |
| `record_trade.yml` | issue / dispatch | Record a BUY/SELL trade from the mobile UI or an issue |
| `ci.yml` | push / PR | `pytest` gate + advisory `ruff` |
| `deploy-pages.yml` | push | Publish `docs/` to GitHub Pages |

**Ops safety:** `scripts/notify_failure.py` emails on workflow failure and is
wired into the scan and advisor jobs. The advisor exits non-zero on a total data
outage instead of finishing green with no email sent.

Locally, `run_daily.sh` retries the scan every 5 minutes until the email is sent
or the 14:30 ICT market-close cutoff is reached.

---

## Configuration

Everything lives in [`config.yaml`](config.yaml). Key sections:

| Section | Controls |
|---|---|
| `portfolio` | Capital, risk-per-trade, max position / total risk |
| `watchlist` | `vn30`, `vn100`, `hnx30` ticker lists |
| `sectors` | Sector → ticker mapping (for rotation & peer comparison) |
| `thresholds` | Per-timeframe confidence cutoffs & indicator params |
| `multiday` | Lookback offsets, min streak, consistency bonus |
| `regime` | MA windows, ADX threshold, bull/bear/sideways adjustments |
| `foreign_flow`, `insider` | Enable flags, lookback, cache TTLs |
| `signal_adjustments` | Toggle regime / foreign / insider folding into conviction |
| `ml_signal` | Enable, min samples, blend weight |
| `risk_levels` | ATR stop / target multipliers |
| `backtest` | Commission, slippage |
| `data_cache_ttl` | Cache lifetimes per data type |

Secrets (`ANTHROPIC_API_KEY`, email creds, data tokens) come from the
environment / `.env`, never `config.yaml`.

---

## Project layout

```
analyst-stock-vn/
├── main.py                  # CLI entrypoint (all commands)
├── config.yaml              # watchlists, thresholds, feature toggles
├── notify_daily.py          # daily scan + email pipeline
├── dashboard.py             # Streamlit dashboard
├── run_daily.sh             # local retry-until-sent wrapper
├── agents/                  # Claude-powered analysis agents
│   ├── market_scanner.py    fundamental_analyst.py
│   ├── portfolio_advisor.py news_monitor.py  trading_desk.py
├── src/
│   ├── fetcher/             # vnstock/yfinance clients, foreign flow, insider, news, futures
│   ├── indicators/          # technical indicators
│   ├── signals/             # daily / monthly / yearly / multiday / intraday / patterns
│   ├── scoring/             # scorer + conviction adjustments
│   ├── report/              # email / formatter / telegram
│   ├── backtesting/         # walk-forward runner + metrics
│   ├── ml_signal.py         # ML calibration (walk-forward validated)
│   ├── realized_pnl.py      # closed-lot ledger
│   ├── regime.py  sector_rotation.py  peer_comparison.py
│   ├── portfolio_*.py       # monitor / optimizer / tracker
│   ├── risk_manager.py  position_sizer.py  alerts.py  earnings.py
│   └── trade_log/           # signal outcome logging
├── scripts/                 # record_trade, market_open_advisor, notify_failure, parse_issue
├── workers/trade-proxy.js   # Cloudflare Worker for the mobile trade UI
├── docs/index.html          # mobile trade-log UI (GitHub Pages)
├── .github/workflows/       # scan / advisor / trade / CI / pages
└── tests/                   # pytest suite
```

---

## Testing & linting

```bash
pytest tests/                    # run the full suite
pytest tests/test_signals.py -v  # a single file

ruff check src/ && mypy src/     # lint + type-check
ruff format src/                 # format
```

CI runs the `pytest` gate on every push and PR.

---

## Further reading

- [`docs/enhancements.md`](docs/enhancements.md) — deep dive on the signal-wiring,
  ML validation, ops-safety, and dashboard work.
- [`CLAUDE.md`](CLAUDE.md) — guidance for working in this repo with Claude Code.
