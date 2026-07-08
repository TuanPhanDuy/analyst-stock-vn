"""Tests for conviction-score adjustment (regime / foreign flow / insider)."""
from dataclasses import dataclass

from src.scoring.adjustments import adjust_conviction, apply_to_analyses


BULL = {"regime": "BULL", "confidence": 1.0}
BEAR = {"regime": "BEAR", "confidence": 1.0}
CFG = {"regime": {"bull_signal_boost": 0.15, "bear_signal_penalty": 0.10,
                  "sideways_haircut": 0.10}}


def test_no_signals_passthrough():
    out = adjust_conviction(0.50)
    assert out["adjusted"] == 0.50
    assert out["regime_mult"] == 1.0
    assert out["foreign_delta"] == 0.0
    assert out["insider_delta"] == 0.0


def test_bull_regime_boosts_buy():
    out = adjust_conviction(0.50, regime_result=BULL, cfg=CFG)
    # bull_signal_boost 0.15 * confidence 1.0 → ×1.15
    assert out["regime_mult"] == 1.15
    assert out["adjusted"] == round(0.50 * 1.15, 4)


def test_bear_regime_penalises_buy():
    out = adjust_conviction(0.50, regime_result=BEAR, cfg=CFG)
    assert out["regime_mult"] < 1.0
    assert out["adjusted"] < 0.50


def test_foreign_selling_dampens_buy():
    foreign = {"score_delta": -0.15}
    out = adjust_conviction(0.40, foreign=foreign)
    assert out["foreign_delta"] == -0.15
    assert out["adjusted"] == round(0.40 - 0.15, 4)


def test_insider_buying_reinforces_buy():
    insider = {"score_delta": 0.20}
    out = adjust_conviction(0.30, insider=insider)
    assert out["adjusted"] == round(0.30 + 0.20, 4)


def test_strong_opposing_insider_can_flip_weak_buy():
    # A marginal BUY with heavy insider selling should flip negative (→ SELL).
    out = adjust_conviction(0.05, insider={"score_delta": -0.20})
    assert out["adjusted"] < 0


def test_combined_factors():
    out = adjust_conviction(
        0.40, regime_result=BULL,
        foreign={"score_delta": 0.10}, insider={"score_delta": 0.20}, cfg=CFG,
    )
    expected = round(0.40 * 1.15 + 0.10 + 0.20, 4)
    assert out["adjusted"] == expected


def test_toggles_disable_factors():
    cfg = {**CFG, "signal_adjustments": {"use_regime": False,
                                         "use_foreign_flow": False,
                                         "use_insider": False}}
    out = adjust_conviction(
        0.40, regime_result=BULL,
        foreign={"score_delta": 0.10}, insider={"score_delta": 0.20}, cfg=cfg,
    )
    assert out["adjusted"] == 0.40
    assert out["regime_mult"] == 1.0


@dataclass
class _FakeAnalysis:
    ticker: str
    conviction_score: float
    current_composite: float = 0.0
    streak_days: int = 3
    consistency_score: float = 0.8
    raw_conviction_score: float = 0.0
    regime_mult: float = 1.0
    ml_factor: float = 1.0
    foreign_delta: float = 0.0
    insider_delta: float = 0.0


def test_apply_to_analyses_mutates_and_preserves_raw():
    analyses = [
        _FakeAnalysis("HPG", 0.50),
        _FakeAnalysis("VCB", -0.40),
    ]
    apply_to_analyses(
        analyses,
        foreign_flows={"HPG": {"score_delta": -0.15}},
        insider_signals={"VCB": {"score_delta": 0.20}},
        regime_result=BULL,
        cfg=CFG,
    )
    hpg = analyses[0]
    assert hpg.raw_conviction_score == 0.50
    assert hpg.foreign_delta == -0.15
    assert hpg.regime_mult == 1.15
    assert hpg.conviction_score == round(0.50 * 1.15 - 0.15, 4)

    vcb = analyses[1]
    # SELL (negative) in a bull market is discounted (regime_mult < 1),
    # and insider buying (positive delta) dampens the sell.
    assert vcb.raw_conviction_score == -0.40
    assert vcb.insider_delta == 0.20
    assert vcb.conviction_score > -0.40


def test_apply_to_analyses_handles_missing_signals():
    analyses = [_FakeAnalysis("FPT", 0.25)]
    apply_to_analyses(analyses)  # no signals at all
    assert analyses[0].conviction_score == 0.25
    assert analyses[0].raw_conviction_score == 0.25
    assert analyses[0].ml_factor == 1.0


def test_ml_factor_scales_conviction():
    # A calibrator that raises confidence should push ml_factor above 1.0.
    def fake_calibrate(*, confidence, composite_score, action, streak_days,
                       consistency_score, blend, min_samples):
        return min(1.0, confidence * 1.3)

    analyses = [_FakeAnalysis("HPG", 0.50, current_composite=0.50)]
    apply_to_analyses(analyses, ml_calibrate=fake_calibrate)
    assert analyses[0].ml_factor > 1.0
    assert analyses[0].conviction_score > 0.50


def test_ml_factor_is_clamped():
    # An extreme calibrator must not blow up the score — factor clamps at 1.5.
    def fake_calibrate(**kwargs):
        return 1.0  # max confidence regardless of input

    analyses = [_FakeAnalysis("HPG", 0.10, current_composite=0.10)]
    apply_to_analyses(analyses, ml_calibrate=fake_calibrate)
    assert analyses[0].ml_factor == 1.5  # clamped, not 10x


def test_ml_calibrate_none_is_neutral():
    analyses = [_FakeAnalysis("HPG", 0.40, current_composite=0.40)]
    apply_to_analyses(analyses, ml_calibrate=None)
    assert analyses[0].ml_factor == 1.0
    assert analyses[0].conviction_score == 0.40
