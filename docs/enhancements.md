# Enhancements: signal wiring, ML validation, ops safety & dashboards

This document explains the changes introduced to **activate features that were
computed but never applied**, close the signal→outcome feedback loop, harden
operations, and add performance dashboards. It complements the summary in the
main [README](../README.md).

---

## 1. Signal quality — wiring dead signals into the ranking

Three signals were being computed on every run but only ever *displayed* (in
printed tables, emails, and LLM prompts). They never touched the number that
drives ranking and position sizing. They are now folded into the conviction
score in [`src/scoring/adjustments.py`](../src/scoring/adjustments.py):

```
adjusted = base_conviction × regime_mult × ml_factor + foreign_delta + insider_delta
```

| Factor | Type | Bound | Source | Rationale |
|---|---|---|---|---|
| Market regime | multiplicative | — | `src.regime.signal_multiplier` | A BUY in a BULL market is more trustworthy; a BUY in a BEAR market less so. |
| ML calibration | multiplicative | — | `src.ml_signal` | Learned win-probability (§2). Defaults to `1.0` (no effect) until validated. |
| Foreign flow | additive | ±0.15 | `src.fetcher.foreign_flow` | Net foreign buying/selling confirmation. |
| Insider | additive | ±0.20 | `src.fetcher.insider` | Insider transaction confirmation. |

Foreign/insider deltas are **signed bullish-positive**, so they correctly
dampen *or* reinforce both BUY (positive) and SELL (negative) convictions — and
a strong opposing flow can flip a marginal signal (e.g. heavy insider selling on
a weak BUY).

Each factor toggles independently via `config.yaml → signal_adjustments`:

```yaml
signal_adjustments:
  use_regime: true        # multiply conviction by regime signal_multiplier
  use_foreign_flow: true  # add foreign-flow score_delta (±0.15)
  use_insider: true       # add insider score_delta (±0.20)
```

**Pipeline ordering.** `notify_daily.py` now defers ranking until *after* the
foreign/insider fetch completes, then applies the adjustment — previously the
ranking was fixed before those signals were even available.

---

## 2. ML calibration — safe by default

Previously, all logged signals stayed `PENDING` forever, so the ML calibrator
never received any labelled data and could not learn.

- **Auto-resolve outcomes** — each run now calls `update_pending_outcomes` to
  walk past signals forward and resolve them (target/stop/expiry) before
  training. This closes the feedback loop.
- **Walk-forward validation** — `ml_signal.validate()` uses `TimeSeriesSplit`
  for out-of-sample evaluation. Calibration is applied **only** when the model
  demonstrates genuine skill:
  - out-of-sample **AUC ≥ 0.55**, and
  - **Brier score better than the base rate**.

  Otherwise `_get_validated_model()` returns nothing and raw confidence passes
  through unchanged — the model can never *hurt* rankings on unproven data.
- **Feature enrichment** — `SignalRecord` now carries real `confidence`,
  `streak`, and `consistency` values; previously these were degenerate zeros
  that gave the model nothing to learn from.

Config in `config.yaml → ml_signal` (`enabled`, `min_samples`,
`confidence_blend`).

---

## 3. Ops safety

- **Failure alerts** — `scripts/notify_failure.py` emails when a GitHub Actions
  run fails. Wired into `daily_scan.yml` and `market_open_advisor.yml`.
- **Fail loud, not silent** — the market-open advisor now **exits non-zero on a
  total data outage** instead of finishing green having sent no email. A silent
  green run previously looked like success when nothing had been delivered.
- **CI gate** — `.github/workflows/ci.yml` runs `pytest` on every push/PR, plus
  advisory `ruff`.

---

## 4. Correctness / tech-debt fixes

- **`vnstock_client._make_stock` restored** — it had been deleted but was still
  called by `get_financials` / `get_company_overview`, raising `NameError` at
  runtime. OHLCV schema parsing was also hardened.
- **De-duplicated OHLCV fetch** — `market_open_advisor.py` now uses the shared
  cached `get_ohlcv` instead of issuing its own duplicate fetch.
- **Portfolio monitor** — exposes `effective_stop` / `effective_target` so the
  dashboard no longer displays a contradictory `0`; provisional HPG stop/target
  set.

---

## 5. Dashboards

- **Realized-P&L ledger** — [`src/realized_pnl.py`](../src/realized_pnl.py)
  records each closed lot to `data/realized_pnl.jsonl` (append-only, one line per
  lot). Wired into the SELL path of `scripts/record_trade.py`. Without it, booked
  gains were lost the moment a sold position left `portfolio.json`, which only
  tracks open positions and unrealized P&L.
- **Dashboard panels** — `dashboard.py` adds a **NAV / equity-curve** panel and a
  **realized-P&L** panel.

---

## Tests

The change set added **27 tests** (adjustments, ML validation, portfolio
monitor, realized P&L), bringing the suite to 85 passing.

```bash
pytest tests/test_adjustments.py tests/test_ml_signal.py \
       tests/test_portfolio_monitor.py tests/test_realized_pnl.py -v
```
