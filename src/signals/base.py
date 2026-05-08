"""Shared types for all signal modules."""
from dataclasses import dataclass
from typing import Literal


@dataclass
class Signal:
    ticker: str
    timeframe: Literal["daily", "monthly", "yearly"]
    action: Literal["BUY", "SELL", "HOLD"]
    confidence: float        # 0.0 – 1.0
    reason: str
    price: float             # latest close

    def is_actionable(self, min_confidence: float = 0.60) -> bool:
        return self.action != "HOLD" and self.confidence >= min_confidence

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "timeframe": self.timeframe,
            "action": self.action,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "price": self.price,
        }
