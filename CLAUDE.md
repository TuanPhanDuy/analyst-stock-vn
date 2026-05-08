# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**analyst-stock-vn** is a Vietnamese stock market analysis system that generates buy/sell recommendations across daily, monthly, and yearly timeframes. It fetches data from popular Vietnamese financial data sources, applies technical and fundamental analysis, and surfaces actionable signals.

## Data Sources

Primary Vietnamese stock data providers to integrate:

- **VNDirect API** – market data, financial statements, screener
- **SSI iBoard / SSI Fast Connect API** – real-time and historical OHLCV
- **FireAnt** (`fireant.vn`) – news, financials, watchlists
- **CafeF** (`cafef.vn`) – market news, financial reports
- **VietStock** (`vietstock.vn`) – fundamental data, analyst reports
- **HOSE/HNX official feeds** – authoritative listing and price data
- **vnstock** Python library (`pip install vnstock`) – unified wrapper over multiple VN data sources

Prefer `vnstock` as the primary data-access layer; fall back to direct HTTP scraping only when vnstock does not cover a required field.

## Intended Architecture

```
analyst-stock-vn/
├── data/               # raw & cached market data (gitignored)
├── src/
│   ├── fetcher/        # data ingestion: vnstock wrappers + scrapers
│   ├── indicators/     # technical indicators (RSI, MACD, BB, MA, volume analysis)
│   ├── fundamental/    # P/E, P/B, ROE, debt ratios, earnings trend
│   ├── signals/        # buy/sell signal generation per timeframe
│   │   ├── daily.py    # intraday & swing signals (momentum, breakout)
│   │   ├── monthly.py  # trend-following, sector rotation
│   │   └── yearly.py   # value investing, long-term fundamental screens
│   ├── scoring/        # composite score aggregation across signals
│   ├── portfolio/      # position sizing, risk management helpers
│   ├── report/         # output formatters: CLI table, JSON, HTML, Telegram
│   └── scheduler/      # cron/APScheduler jobs for automated runs
├── agents/             # Claude-powered analysis agents (see Agents section)
├── tests/
├── config.yaml         # tickers watchlist, thresholds, API keys references
└── main.py             # CLI entrypoint
```

## Timeframe Signal Logic

| Timeframe | Horizon | Primary Signals |
|-----------|---------|-----------------|
| Daily | 1–5 days | RSI divergence, MACD crossover, volume spike, intraday breakout |
| Monthly | 1–3 months | Moving average trend, sector momentum, earnings surprise |
| Yearly | 6–24 months | P/E vs sector, ROE growth, revenue CAGR, debt reduction |

Each signal module returns a structured dict: `{ticker, timeframe, action, confidence, reason}`.

## Agents (Planned)

Claude-powered agents under `agents/`:

- **market-scanner**: scans all HOSE+HNX tickers each morning, runs scoring, outputs top 10 buy/sell candidates
- **fundamental-analyst**: deep-dives a single ticker on request (financials + news sentiment)
- **portfolio-advisor**: given a current portfolio, suggests rebalancing based on signals
- **news-monitor**: watches FireAnt/CafeF headlines, flags material events for held positions

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the full daily scan
python main.py scan --timeframe daily

# Analyze a single ticker
python main.py analyze --ticker VCB --timeframe monthly

# Run all tests
pytest tests/

# Run a single test file
pytest tests/test_signals.py -v

# Lint
ruff check src/ && mypy src/

# Format
ruff format src/
```

## Configuration

`config.yaml` holds:
- `watchlist`: list of tickers to track (default: VN30 index constituents)
- `thresholds`: signal confidence cutoffs per timeframe
- `data_cache_ttl`: seconds before refetching (daily data: 3600, fundamentals: 86400)
- API credentials are read from environment variables, never hardcoded (`VNSTOCK_TOKEN`, `SSI_CLIENT_ID`, etc.)

## Key Conventions

- All prices in VND, volumes in shares (not lots).
- Tickers follow HOSE/HNX format (e.g., `VCB`, `FPT`, `HPG`).
- DataFrames use `date` as index (pandas `DatetimeIndex`), columns: `open high low close volume`.
- Signal confidence is a float 0.0–1.0; only surface signals ≥ 0.6 to the user.
- Fetcher modules cache responses to `data/cache/` to avoid rate-limiting.
