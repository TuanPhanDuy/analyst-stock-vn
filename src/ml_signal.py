"""
ML-based signal calibration using historical signal outcomes.

Reads data/signals.jsonl, trains a classifier on resolved trades,
and adjusts raw confidence scores using learned win-probability estimates.

Model preference:
  1. LightGBM (fast, handles tabular data well) — installed via `pip install lightgbm`
  2. scikit-learn RandomForestClassifier (fallback)
  3. No model — returns raw confidence unchanged

Feature set:
  - confidence (raw signal confidence 0–1)
  - composite_score (signed composite score)
  - action (BUY=1, SELL=-1)
  - streak_days (from multiday analysis, if available)
  - consistency_score (from multiday analysis, if available)

Label:
  - 1 = profitable trade (outcome_pnl_pct > 0)
  - 0 = unprofitable
"""
from __future__ import annotations

import json
import logging
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

LOG_PATH = Path(__file__).parent.parent / "data" / "signals.jsonl"


def _load_closed_trades() -> pd.DataFrame:
    """Load resolved signal records from signals.jsonl."""
    if not LOG_PATH.exists() or LOG_PATH.stat().st_size == 0:
        return pd.DataFrame()

    records = []
    for line in LOG_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    return df[df["outcome_result"].isin(["HIT_TARGET", "HIT_STOP", "EXPIRED"])].copy()


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Extract numeric feature matrix from signal records."""
    feats = pd.DataFrame(index=df.index)
    feats["confidence"] = pd.to_numeric(df.get("confidence", 0), errors="coerce").fillna(0.5)
    feats["composite_score"] = pd.to_numeric(df.get("composite_score", 0), errors="coerce").fillna(0)
    feats["action_dir"] = df.get("action", "BUY").apply(lambda a: 1 if a == "BUY" else -1)

    # Optional multiday fields (may not be in older records)
    for col in ("streak_days", "consistency_score"):
        if col in df.columns:
            feats[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        else:
            feats[col] = 0.0

    return feats.fillna(0.0)


def train(min_samples: int = 20) -> Optional[object]:
    """
    Train a signal quality classifier on historical closed trades.

    Returns the trained model, or None if insufficient data.
    """
    closed = _load_closed_trades()
    if len(closed) < min_samples:
        logger.debug("ML calibrator: only %d closed trades, need %d", len(closed), min_samples)
        return None

    X = _build_features(closed)
    y = (closed["outcome_pnl_pct"] > 0).astype(int)

    # Ensure some positive class samples exist
    if y.sum() < 3 or (len(y) - y.sum()) < 3:
        return None

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                from lightgbm import LGBMClassifier
                model = LGBMClassifier(
                    n_estimators=200,
                    max_depth=4,
                    learning_rate=0.05,
                    num_leaves=15,
                    subsample=0.8,
                    colsample_bytree=0.8,
                    verbose=-1,
                    random_state=42,
                )
                logger.debug("ML calibrator: using LightGBM")
            except ImportError:
                from sklearn.ensemble import GradientBoostingClassifier
                model = GradientBoostingClassifier(
                    n_estimators=100,
                    max_depth=3,
                    learning_rate=0.1,
                    random_state=42,
                )
                logger.debug("ML calibrator: using GradientBoosting (sklearn)")

            model.fit(X, y)
            return model
    except Exception as e:
        logger.warning("ML calibrator training failed: %s", e)
        return None


# Module-level model cache (loaded once per process)
_model: Optional[object] = None
_model_initialized: bool = False


def _get_model(min_samples: int = 20) -> Optional[object]:
    global _model, _model_initialized
    if not _model_initialized:
        _model = train(min_samples=min_samples)
        _model_initialized = True
    return _model


def reload():
    """Force re-train the model on next call (use after new signal outcomes are logged)."""
    global _model_initialized
    _model_initialized = False


def calibrate_confidence(
    confidence: float,
    composite_score: float,
    action: str = "BUY",
    streak_days: int = 0,
    consistency_score: float = 0.0,
    blend: float = 0.50,
    min_samples: int = 20,
) -> float:
    """
    Adjust a signal's raw confidence using ML win-probability estimate.

    Args:
        confidence: raw signal confidence (0–1)
        composite_score: signed composite score from scorer
        action: "BUY" or "SELL"
        streak_days: from multiday analysis
        consistency_score: from multiday analysis
        blend: weight of ML probability (0.5 = 50/50 blend with raw)
        min_samples: minimum closed trades before ML activates

    Returns:
        Calibrated confidence (0–1).
        Returns raw confidence unchanged if model is not available.
    """
    model = _get_model(min_samples=min_samples)
    if model is None:
        return confidence

    try:
        X = pd.DataFrame([[
            confidence,
            composite_score,
            1 if action == "BUY" else -1,
            streak_days,
            consistency_score,
        ]], columns=["confidence", "composite_score", "action_dir", "streak_days", "consistency_score"])

        proba = float(model.predict_proba(X)[0][1])   # P(win)
        calibrated = confidence * (1 - blend) + proba * blend
        return round(min(1.0, max(0.0, calibrated)), 3)
    except Exception as e:
        logger.debug("calibrate_confidence failed: %s", e)
        return confidence


def accuracy_report() -> dict:
    """
    Compute signal accuracy metrics from historical data.

    Returns a dict suitable for display or JSON export.
    """
    if not LOG_PATH.exists():
        return {"message": "No signal history (data/signals.jsonl not found)"}

    all_lines = [l for l in LOG_PATH.read_text().splitlines() if l.strip()]
    if not all_lines:
        return {"message": "Signal log is empty"}

    all_records = [json.loads(l) for l in all_lines]
    df = pd.DataFrame(all_records)

    closed = df[df["outcome_result"].isin(["HIT_TARGET", "HIT_STOP", "EXPIRED"])].copy()
    pending = df[df["outcome_result"] == "PENDING"]

    if closed.empty:
        return {
            "message": "No resolved signals yet",
            "total_pending": len(pending),
        }

    wins = closed[closed["outcome_pnl_pct"] > 0]
    overall_wr = round(len(wins) / len(closed) * 100, 1)
    avg_pnl = round(float(closed["outcome_pnl_pct"].mean()), 2)
    avg_win = round(float(wins["outcome_pnl_pct"].mean()), 2) if not wins.empty else 0.0
    avg_loss = round(float(closed[closed["outcome_pnl_pct"] <= 0]["outcome_pnl_pct"].mean()), 2) if len(closed) > len(wins) else 0.0

    # By action
    by_action = {}
    for action in ["BUY", "SELL"]:
        sub = closed[closed["action"] == action]
        if not sub.empty:
            w = sub[sub["outcome_pnl_pct"] > 0]
            by_action[action] = {
                "count": len(sub),
                "win_rate_pct": round(len(w) / len(sub) * 100, 1),
                "avg_pnl_pct": round(float(sub["outcome_pnl_pct"].mean()), 2),
            }

    # By outcome type
    outcome_counts = closed["outcome_result"].value_counts().to_dict()

    # Top and worst tickers
    ticker_perf = (
        closed.groupby("ticker")["outcome_pnl_pct"]
        .agg(["mean", "count"])
        .rename(columns={"mean": "avg_pnl", "count": "trades"})
        .sort_values("avg_pnl", ascending=False)
    )
    top_tickers = [
        {"ticker": t, "avg_pnl_pct": round(row["avg_pnl"], 2), "trades": int(row["trades"])}
        for t, row in ticker_perf.head(5).iterrows()
    ]
    worst_tickers = [
        {"ticker": t, "avg_pnl_pct": round(row["avg_pnl"], 2), "trades": int(row["trades"])}
        for t, row in ticker_perf.tail(5).iterrows()
    ]

    # ML model status
    model = _get_model()
    ml_status = "active" if model is not None else f"inactive (need ≥{20} closed trades, have {len(closed)})"

    return {
        "total_closed": len(closed),
        "total_pending": len(pending),
        "overall_win_rate_pct": overall_wr,
        "avg_pnl_pct": avg_pnl,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "by_action": by_action,
        "outcome_breakdown": outcome_counts,
        "top_tickers": top_tickers,
        "worst_tickers": worst_tickers,
        "ml_calibrator": ml_status,
        "kelly_recommended_risk_pct": round(
            kelly_from_overall(overall_wr / 100, avg_win, abs(avg_loss)) * 100, 2
        ) if avg_win > 0 and avg_loss < 0 else None,
    }


def kelly_from_overall(win_rate: float, avg_win_pct: float, avg_loss_pct: float) -> float:
    """Quarter-Kelly from overall portfolio stats."""
    if avg_loss_pct <= 0 or avg_win_pct <= 0:
        return 0.02
    q = 1.0 - win_rate
    r = avg_win_pct / avg_loss_pct
    full_kelly = win_rate - q / r
    return round(max(0.005, min(0.25, full_kelly * 0.25)), 4)
