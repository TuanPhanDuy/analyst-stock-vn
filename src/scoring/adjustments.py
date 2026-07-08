"""
Fold external confirmation signals into a base conviction score.

Three signals were previously computed but never applied to the ranking —
they only appeared in printed tables, email and LLM prompts:

  - market regime multiplier      (src.regime.signal_multiplier)
  - foreign investor flow delta   (src.fetcher.foreign_flow.foreign_signal → score_delta)
  - insider transaction delta      (src.fetcher.insider.signal → score_delta)

This module wires them into the number that actually drives ranking/sizing:

    adjusted = base_conviction × regime_mult + foreign_delta + insider_delta

Rationale:
  - Regime *scales* the base signal — a BUY in a BULL market is more trustworthy,
    a BUY in a BEAR market less so (multiplicative).
  - Foreign/insider deltas are *additive* confirmations, bounded to ±0.15 / ±0.20
    at source. They are signed bullish-positive, so they correctly dampen or
    reinforce both BUY (positive) and SELL (negative) convictions, and can flip a
    weak signal when a strong opposing flow is present (e.g. heavy insider selling
    on a marginal BUY).

Each factor can be toggled via the `signal_adjustments` section of config.yaml.
"""
from __future__ import annotations

from typing import Optional

from src.regime import signal_multiplier


def adjust_conviction(
    base_conviction: float,
    *,
    regime_result: Optional[dict] = None,
    foreign: Optional[dict] = None,
    insider: Optional[dict] = None,
    ml_factor: float = 1.0,
    cfg: Optional[dict] = None,
) -> dict:
    """
    Compute the adjusted conviction and the breakdown of factors applied.

        adjusted = base × regime_mult × ml_factor + foreign_delta + insider_delta

    regime and ML scale the base signal (multiplicative), while foreign/insider
    flows are additive confirmations. ml_factor defaults to 1.0 (no ML applied).

    Returns:
        {adjusted, regime_mult, ml_factor, foreign_delta, insider_delta}
    """
    cfg = cfg or {}
    adj_cfg = cfg.get("signal_adjustments", {})
    use_regime = adj_cfg.get("use_regime", True)
    use_foreign = adj_cfg.get("use_foreign_flow", True)
    use_insider = adj_cfg.get("use_insider", True)

    action = "BUY" if base_conviction > 0 else ("SELL" if base_conviction < 0 else "HOLD")

    regime_mult = 1.0
    if use_regime and regime_result and action in ("BUY", "SELL"):
        regime_mult = signal_multiplier(regime_result, action, cfg.get("regime", {}))

    foreign_delta = 0.0
    if use_foreign and foreign:
        foreign_delta = float(foreign.get("score_delta", 0.0) or 0.0)

    insider_delta = 0.0
    if use_insider and insider:
        insider_delta = float(insider.get("score_delta", 0.0) or 0.0)

    adjusted = base_conviction * regime_mult * ml_factor + foreign_delta + insider_delta
    return {
        "adjusted": round(adjusted, 4),
        "regime_mult": round(regime_mult, 3),
        "ml_factor": round(ml_factor, 3),
        "foreign_delta": round(foreign_delta, 3),
        "insider_delta": round(insider_delta, 3),
    }


def _ml_factor_for(analysis, base: float, ml_calibrate, cfg: dict) -> float:
    """
    Turn a validated ML win-probability into a multiplicative conviction factor.

    ml_calibrate(confidence, composite_score, action, streak_days, consistency_score)
    returns a calibrated confidence in [0, 1]. We convert the ratio of calibrated
    to raw confidence into a factor, clamped to [0.5, 1.5] so a single model call
    can never dominate or invert the base signal. Returns 1.0 when unavailable.
    """
    if ml_calibrate is None or base == 0:
        return 1.0
    conf_in = abs(base)
    if conf_in <= 0:
        return 1.0
    action = "BUY" if base > 0 else "SELL"
    ml_cfg = (cfg or {}).get("ml_signal", {})
    try:
        calibrated = ml_calibrate(
            confidence=min(1.0, conf_in),
            composite_score=analysis.current_composite,
            action=action,
            streak_days=analysis.streak_days,
            consistency_score=analysis.consistency_score,
            blend=ml_cfg.get("confidence_blend", 0.50),
            min_samples=ml_cfg.get("min_samples", 20),
        )
    except Exception:
        return 1.0
    factor = calibrated / min(1.0, conf_in)
    return max(0.5, min(1.5, factor))


def apply_to_analyses(
    analyses: list,
    foreign_flows: Optional[dict] = None,
    insider_signals: Optional[dict] = None,
    regime_result: Optional[dict] = None,
    cfg: Optional[dict] = None,
    ml_calibrate=None,
) -> list:
    """
    Mutate each MultiDayAnalysis in place: fold regime/foreign/insider (and, if a
    validated calibrator is supplied, ML) factors into its `conviction_score`,
    preserving the pre-adjustment value in `raw_conviction_score` and recording
    the individual factors on the object.

    Args:
        ml_calibrate: optional callable (typically ml_signal.calibrate_confidence
            with require_validation=True already applied). It is only invoked when
            the model is validated, so passing it is safe even with no history —
            it simply returns the raw confidence and yields ml_factor == 1.0.

    Missing signals (fetch failed / ticker absent) contribute a neutral factor,
    so the base conviction passes through unchanged. Returns the same list.
    """
    foreign_flows = foreign_flows or {}
    insider_signals = insider_signals or {}
    for a in analyses:
        base = a.conviction_score
        ml_factor = _ml_factor_for(a, base, ml_calibrate, cfg)
        out = adjust_conviction(
            base,
            regime_result=regime_result,
            foreign=foreign_flows.get(a.ticker),
            insider=insider_signals.get(a.ticker),
            ml_factor=ml_factor,
            cfg=cfg,
        )
        a.raw_conviction_score = round(base, 4)
        a.regime_mult = out["regime_mult"]
        a.ml_factor = out["ml_factor"]
        a.foreign_delta = out["foreign_delta"]
        a.insider_delta = out["insider_delta"]
        a.conviction_score = out["adjusted"]
    return analyses
