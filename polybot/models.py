from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class Market:
    condition_id: str
    question: str
    slug: str
    token_ids: list[str]
    outcomes: list[str]
    volume_24h: float
    liquidity: float

    def token_outcome(self, token_id: str) -> str:
        try:
            idx = self.token_ids.index(token_id)
            return self.outcomes[idx]
        except (ValueError, IndexError):
            return "?"


@dataclass(frozen=True)
class PricePoint:
    ts: int  # unix seconds
    price: float


@dataclass(frozen=True)
class OrderBook:
    token_id: str
    best_bid: float | None
    best_ask: float | None

    @property
    def mid(self) -> float | None:
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2
        return self.best_bid if self.best_bid is not None else self.best_ask


@dataclass
class Signal:
    token_id: str
    condition_id: str
    market_question: str
    outcome: str
    momentum: float
    reference_price: float
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class WalletBuy:
    wallet: str
    token_id: str
    usd_size: float
    timestamp: datetime
