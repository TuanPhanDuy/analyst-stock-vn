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
            model = _make_model()
            model.fit(X, y)
            return model
    except Exception as e:
        logger.warning("ML calibrator training failed: %s", e)
        return None


def _make_model():
    """Instantiate the classifier (LightGBM preferred, sklearn GB fallback)."""
    try:
        from lightgbm import LGBMClassifier
        return LGBMClassifier(
            n_estimators=200, max_depth=4, learning_rate=0.05, num_leaves=15,
            subsample=0.8, colsample_bytree=0.8, verbose=-1, random_state=42,
        )
    except ImportError:
        from sklearn.ensemble import GradientBoostingClassifier
        return GradientBoostingClassifier(
            n_estimators=100, max_depth=3, learning_rate=0.1, random_state=42,
        )


def validate(min_samples: int = 20, n_splits: int = 5) -> dict:
    """
    Walk-forward (time-series) out-of-sample validation of the calibrator.

    The production model is trained on ALL closed trades (that's correct — you
    want to use every observation). The danger is *trusting* an overfit model.
    This function estimates genuine out-of-sample skill with a TimeSeriesSplit:
    train on the past, predict the next block, never peeking forward.

    Returns:
        {
          reliable: bool,          # True only if the model beats the base-rate baseline OOS
          reason: str,
          n_samples: int,
          oos_auc: float | None,   # ROC-AUC on pooled OOS predictions (0.5 = coin flip)
          oos_brier: float | None, # Brier score of the model (lower = better)
          baseline_brier: float | None,  # Brier of always predicting the base win rate
        }

    `reliable` requires oos_auc ≥ 0.55 AND a Brier score below the naive baseline,
    so calibration only ever activates when it demonstrably improves on doing nothing.
    """
    closed = _load_closed_trades()
    n = len(closed)
    if n < min_samples:
        return {"reliable": False, "reason": f"insufficient data ({n}/{min_samples})",
                "n_samples": n, "oos_auc": None, "oos_brier": None, "baseline_brier": None}

    # Order chronologically so the split respects time.
    if "scan_date" in closed.columns:
        closed = closed.sort_values("scan_date")

    X = _build_features(closed).reset_index(drop=True)
    y = (closed["outcome_pnl_pct"] > 0).astype(int).reset_index(drop=True)
    if y.nunique() < 2:
        return {"reliable": False, "reason": "only one outcome class present",
                "n_samples": n, "oos_auc": None, "oos_brier": None, "baseline_brier": None}

    splits = max(2, min(n_splits, n // 10))
    try:
        from sklearn.metrics import brier_score_loss, roc_auc_score
        from sklearn.model_selection import TimeSeriesSplit
    except Exception as e:  # pragma: no cover - sklearn is a hard dep
        return {"reliable": False, "reason": f"sklearn unavailable: {e}",
                "n_samples": n, "oos_auc": None, "oos_brier": None, "baseline_brier": None}

    oos_true, oos_pred, base_pred = [], [], []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for train_idx, test_idx in TimeSeriesSplit(n_splits=splits).split(X):
            y_train = y.iloc[train_idx]
            if y_train.nunique() < 2:
                continue  # can't learn from a single-class training fold
            try:
                model = _make_model()
                model.fit(X.iloc[train_idx], y_train)
                proba = model.predict_proba(X.iloc[test_idx])[:, 1]
            except Exception:
                continue
            oos_true.extend(y.iloc[test_idx].tolist())
            oos_pred.extend(proba.tolist())
            base_pred.extend([float(y_train.mean())] * len(test_idx))

    if len(oos_true) < 5 or len(set(oos_true)) < 2:
        return {"reliable": False, "reason": "not enough OOS folds with both classes",
                "n_samples": n, "oos_auc": None, "oos_brier": None, "baseline_brier": None}

    oos_auc = float(roc_auc_score(oos_true, oos_pred))
    oos_brier = float(brier_score_loss(oos_true, oos_pred))
    baseline_brier = float(brier_score_loss(oos_true, base_pred))
    reliable = oos_auc >= 0.55 and oos_brier < baseline_brier
    return {
        "reliable": reliable,
        "reason": "ok" if reliable else "no out-of-sample edge over base rate",
        "n_samples": n,
        "oos_auc": round(oos_auc, 3),
        "oos_brier": round(oos_brier, 4),
        "baseline_brier": round(baseline_brier, 4),
    }


# Module-level caches (computed once per process; cleared by reload()).
_model: Optional[object] = None
_model_initialized: bool = False
_validation: Optional[dict] = None


def _get_model(min_samples: int = 20) -> Optional[object]:
    global _model, _model_initialized
    if not _model_initialized:
        _model = train(min_samples=min_samples)
        _model_initialized = True
    return _model


def get_validation(min_samples: int = 20) -> dict:
    """Cached walk-forward validation result for this process."""
    global _validation
    if _validation is None:
        _validation = validate(min_samples=min_samples)
    return _validation


def _get_validated_model(min_samples: int = 20, require_validation: bool = True):
    """Return the trained model only if it passes OOS validation (else None)."""
    model = _get_model(min_samples=min_samples)
    if model is None:
        return None
    if not require_validation:
        return model
    return model if get_validation(min_samples).get("reliable") else None


def reload():
    """Force re-train + re-validate on next call (use after new outcomes are logged)."""
    global _model_initialized, _validation
    _model_initialized = False
    _validation = None


def calibrate_confidence(
    confidence: float,
    composite_score: float,
    action: str = "BUY",
    streak_days: int = 0,
    consistency_score: float = 0.0,
    blend: float = 0.50,
    min_samples: int = 20,
    require_validation: bool = True,
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
        require_validation: only calibrate if the model passes OOS validation
            (default True — never apply an unvalidated, possibly-overfit model).

    Returns:
        Calibrated confidence (0–1).
        Returns raw confidence unchanged if no validated model is available.
    """
    model = _get_validated_model(min_samples=min_samples, require_validation=require_validation)
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

    # ML model status — a model only counts as "active" once it passes OOS validation.
    model = _get_model()
    if model is None:
        ml_status = f"inactive (need ≥20 closed trades, have {len(closed)})"
    else:
        v = get_validation()
        if v.get("reliable"):
            ml_status = f"active & validated (OOS AUC={v.get('oos_auc')})"
        else:
            ml_status = f"trained but not applied — {v.get('reason')}"

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
