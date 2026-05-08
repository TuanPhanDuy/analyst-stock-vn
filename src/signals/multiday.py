"""
Multi-day signal analysis.

Runs the full signal pipeline at multiple historical offsets using already-cached
OHLCV data (no extra network calls). Computes signal consistency, confidence trend,
and streak length to produce a conviction_score that rewards signals that have
been building over multiple days rather than appearing for the first time today.

conviction_score = current_composite
                   × (1 + consistency_bonus × consistency_score)
                   × trend_multiplier
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

from src.scoring.scorer import CompositeScore
from src.signals import daily, monthly, yearly


@dataclass
class DaySnapshot:
    date: str           # ISO date of the last bar in the slice
    offset: int         # 0 = today, 1 = yesterday, N = N trading days ago
    action: str         # BUY | SELL | HOLD
    confidence: float
    composite: float    # directional composite score (-1 to +1)


@dataclass
class MultiDayAnalysis:
    ticker: str
    current_price: float
    current_composite: float       # today's composite score (signed)
    current_recommendation: str    # STRONG BUY / BUY / WATCH / SELL / STRONG SELL

    snapshots: List[DaySnapshot] = field(default_factory=list)

    # Derived consistency metrics
    consistency_score: float = 0.0   # 0.0–1.0: % of days same direction as today
    confidence_trend: float = 0.0    # linear slope of confidence over window
    streak_days: int = 0             # consecutive trading days same direction
    conviction_score: float = 0.0    # final adjusted score for ranking (signed)
    conviction_label: str = "LOW"    # HIGH | MEDIUM | LOW

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "price": self.current_price,
            "composite": round(self.current_composite, 3),
            "recommendation": self.current_recommendation,
            "conviction_score": round(self.conviction_score, 3),
            "conviction_label": self.conviction_label,
            "consistency_score": round(self.consistency_score, 3),
            "confidence_trend": round(self.confidence_trend, 4),
            "streak_days": self.streak_days,
            "snapshots": [
                {
                    "date": s.date,
                    "offset": s.offset,
                    "action": s.action,
                    "confidence": round(s.confidence, 3),
                    "composite": round(s.composite, 3),
                }
                for s in self.snapshots
            ],
        }


def _snapshot(ticker: str, df: pd.DataFrame, offset: int,
              cfg: dict, fundamentals: dict = None) -> DaySnapshot | None:
    """Run signals on df sliced to `offset` bars before the end. Returns None on failure."""
    if offset > 0:
        if len(df) <= offset + 60:   # need at least 60 bars for indicators
            return None
        df_slice = df.iloc[:-offset].copy()
    else:
        df_slice = df

    if len(df_slice) < 60:
        return None

    try:
        price = float(df_slice["close"].iloc[-1])
        last_date = df_slice.index[-1].date().isoformat()
        sigs = [
            daily.analyze(ticker, df_slice, cfg["thresholds"]["daily"]),
            monthly.analyze(ticker, df_slice, cfg["thresholds"]["monthly"]),
            yearly.analyze(ticker, price, fundamentals or {}, cfg["thresholds"]["yearly"]),
        ]
        comp = CompositeScore(ticker=ticker, price=price, signals=sigs)
        composite = comp.composite
        # Take the action from the signal with the highest confidence
        dominant = max(sigs, key=lambda s: s.confidence if s.action != "HOLD" else -1)
        action = dominant.action
        confidence = dominant.confidence
        return DaySnapshot(
            date=last_date,
            offset=offset,
            action=action,
            confidence=confidence,
            composite=composite,
        )
    except Exception as e:
        logger.warning("snapshot failed %s offset=%d: %s", ticker, offset, e)
        return None


def _confidence_slope(snapshots: list[DaySnapshot]) -> float:
    """Linear regression slope of confidence over snapshots (oldest → newest)."""
    if len(snapshots) < 2:
        return 0.0
    xs = np.array([s.offset for s in snapshots], dtype=float)
    ys = np.array([s.confidence for s in snapshots], dtype=float)
    # offset 0 = newest, higher offset = older; negate x so slope is newest-forward
    xs = -xs
    if xs.std() == 0:
        return 0.0
    return float(np.polyfit(xs, ys, 1)[0])


def analyze_ticker(
    ticker: str,
    df: pd.DataFrame,
    cfg: dict,
    fundamentals: dict = None,
    lookback_offsets: list[int] = None,
    consistency_bonus: float = 0.30,
) -> MultiDayAnalysis | None:
    """
    Run multi-day analysis for a single ticker.

    Args:
        ticker: ticker symbol.
        df: full OHLCV DataFrame (400-day window).
        cfg: config dict from config.yaml.
        fundamentals: optional dict from vnstock_client.get_financials().
        lookback_offsets: trading-day offsets to analyze. Default [0, 1, 3, 5].
        consistency_bonus: max score multiplier bonus for perfect consistency (0.30 = +30%).

    Returns:
        MultiDayAnalysis, or None if insufficient data.
    """
    if lookback_offsets is None:
        md_cfg = cfg.get("multiday", {})
        lookback_offsets = md_cfg.get("lookback_offsets", [0, 1, 3, 5])

    # Always include offset 0 (today)
    offsets = sorted(set([0] + [o for o in lookback_offsets if o >= 0]))

    snapshots = []
    for offset in offsets:
        snap = _snapshot(ticker, df, offset, cfg, fundamentals)
        if snap is not None:
            snapshots.append(snap)

    if not snapshots:
        return None

    today_snap = snapshots[0]   # offset=0 is first (sorted ascending)
    current_composite = today_snap.composite

    comp_today = CompositeScore(
        ticker=ticker,
        price=float(df["close"].iloc[-1]),
        signals=[],
    )

    # Use a fresh CompositeScore for labels
    price = float(df["close"].iloc[-1])
    sigs = [
        daily.analyze(ticker, df, cfg["thresholds"]["daily"]),
        monthly.analyze(ticker, df, cfg["thresholds"]["monthly"]),
        yearly.analyze(ticker, price, fundamentals or {}, cfg["thresholds"]["yearly"]),
    ]
    comp_full = CompositeScore(ticker=ticker, price=price, signals=sigs)
    current_composite = comp_full.composite
    current_recommendation = comp_full.recommendation

    # ── Consistency score ─────────────────────────────────────────────────────
    today_direction = 1 if current_composite > 0 else (-1 if current_composite < 0 else 0)
    if today_direction == 0:
        consistency_score = 0.0
    else:
        same = sum(
            1 for s in snapshots
            if (1 if s.composite > 0 else (-1 if s.composite < 0 else 0)) == today_direction
        )
        consistency_score = same / len(snapshots)

    # ── Confidence trend ─────────────────────────────────────────────────────
    # Slope of |composite| over time (are signals getting stronger or weaker?)
    confidence_trend = _confidence_slope(snapshots)

    # ── Streak ───────────────────────────────────────────────────────────────
    # Count consecutive trading days (by offset) that match today's direction
    streak = 0
    for s in sorted(snapshots, key=lambda x: x.offset):
        snap_dir = 1 if s.composite > 0 else (-1 if s.composite < 0 else 0)
        if snap_dir == today_direction:
            streak += 1
        else:
            break

    # ── Conviction score ─────────────────────────────────────────────────────
    consistency_mult = 1 + consistency_bonus * consistency_score
    # Trend multiplier: positive slope (strengthening) adds up to +20% boost
    # Negative slope (weakening) applies a discount up to -15%
    trend_mult = 1.0 + max(-0.15, min(0.20, confidence_trend * 5))
    conviction_score = current_composite * consistency_mult * trend_mult

    # Label — thresholds configurable via multiday config
    md_cfg = cfg.get("multiday", {})
    high_min = md_cfg.get("high_conviction_min_consistency", 0.75)
    medium_min = md_cfg.get("medium_conviction_min_consistency", 0.50)
    if consistency_score >= high_min and confidence_trend >= 0:
        conviction_label = "HIGH"
    elif consistency_score >= medium_min or confidence_trend >= 0:
        conviction_label = "MEDIUM"
    else:
        conviction_label = "LOW"

    return MultiDayAnalysis(
        ticker=ticker,
        current_price=price,
        current_composite=round(current_composite, 4),
        current_recommendation=current_recommendation,
        snapshots=snapshots,
        consistency_score=round(consistency_score, 3),
        confidence_trend=round(confidence_trend, 4),
        streak_days=streak,
        conviction_score=round(conviction_score, 4),
        conviction_label=conviction_label,
    )


def build_multiday_scores(
    ohlcv_map: dict,
    cfg: dict,
    fundamentals_map: dict = None,
    max_workers: int = 8,
) -> list[MultiDayAnalysis]:
    """
    Run multi-day analysis for all tickers in ohlcv_map in parallel.
    Returns list of MultiDayAnalysis objects (one per ticker with sufficient data).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if fundamentals_map is None:
        fundamentals_map = {}
    md_cfg = cfg.get("multiday", {})
    offsets = md_cfg.get("lookback_offsets", [0, 1, 3, 5])
    bonus = md_cfg.get("consistency_bonus", 0.30)

    def _worker(args):
        ticker, df = args
        return analyze_ticker(
            ticker, df, cfg,
            fundamentals=fundamentals_map.get(ticker, {}),
            lookback_offsets=offsets,
            consistency_bonus=bonus,
        )

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_worker, (t, df)): t for t, df in ohlcv_map.items()}
        for fut in as_completed(futures):
            try:
                result = fut.result()
                if result is not None:
                    results.append(result)
            except Exception as e:
                print(f"  [warn] {futures[fut]}: {e}")
    return results


def rank_by_conviction(
    analyses: list[MultiDayAnalysis],
    top_n: int = 10,
    min_streak: int = 1,
) -> dict:
    """
    Rank MultiDayAnalysis results by conviction_score.

    Args:
        analyses: list from build_multiday_scores().
        top_n: maximum items per buy/sell list.
        min_streak: minimum streak_days required to appear in results (default 1 = any signal).

    Returns:
        {"buy": [...], "sell": [...]} — each item is a to_dict() with conviction fields.
    """
    buys = sorted(
        [a for a in analyses if a.conviction_score > 0 and a.streak_days >= min_streak],
        key=lambda x: x.conviction_score,
        reverse=True,
    )
    sells = sorted(
        [a for a in analyses if a.conviction_score < 0 and a.streak_days >= min_streak],
        key=lambda x: x.conviction_score,
    )
    return {
        "buy": [a.to_dict() for a in buys[:top_n]],
        "sell": [a.to_dict() for a in sells[:top_n]],
    }
