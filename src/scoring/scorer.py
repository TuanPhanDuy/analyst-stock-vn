"""Aggregate signals across timeframes into a composite ranked list."""
from dataclasses import dataclass, field
from typing import List

from src.signals.base import Signal


@dataclass
class CompositeScore:
    ticker: str
    price: float
    signals: List[Signal] = field(default_factory=list)

    @property
    def composite(self) -> float:
        if not self.signals:
            return 0.0
        weights = {"daily": 0.25, "monthly": 0.40, "yearly": 0.35}
        total, weight_sum = 0.0, 0.0
        for s in self.signals:
            if s.action == "HOLD":
                # HOLD signals still contribute their weight so the denominator
                # is normalised only over timeframes that actually fired
                continue
            direction = 1 if s.action == "BUY" else -1
            w = weights.get(s.timeframe, 0.33)
            total += direction * s.confidence * w
            weight_sum += w
        # If no timeframe fired a BUY/SELL, score is 0
        return total / weight_sum if weight_sum else 0.0

    @property
    def recommendation(self) -> str:
        c = self.composite
        if c >= 0.55:
            return "STRONG BUY"
        if c >= 0.30:
            return "BUY"
        if c <= -0.55:
            return "STRONG SELL"
        if c <= -0.30:
            return "SELL"
        return "WATCH"  # weak signal — monitor but don't act

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "price": self.price,
            "composite": round(self.composite, 3),
            "recommendation": self.recommendation,
            "signals": [s.to_dict() for s in self.signals],
        }


def rank(scores: List[CompositeScore], top_n: int = 10) -> dict:
    buys = sorted(
        [s for s in scores if s.composite > 0],
        key=lambda x: x.composite, reverse=True
    )
    sells = sorted(
        [s for s in scores if s.composite < 0],
        key=lambda x: x.composite
    )
    return {
        "buy": [s.to_dict() for s in buys[:top_n]],
        "sell": [s.to_dict() for s in sells[:top_n]],
    }
