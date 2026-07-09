from __future__ import annotations

from abc import ABC, abstractmethod


class ExecutorBase(ABC):
    """A buy fills at (approximately) the best ask, a sell at the best bid.

    Both paper and live implementations share this interface so the bot loop
    doesn't need to know which one it's talking to.
    """

    @abstractmethod
    def buy(self, token_id: str, usd_amount: float, reference_price: float) -> float | None:
        """Attempt to spend usd_amount buying token_id. Returns the average
        fill price, or None if the order could not be filled at all."""

    @abstractmethod
    def sell(self, token_id: str, size_tokens: float, reference_price: float) -> float | None:
        """Attempt to sell size_tokens of token_id. Returns the average fill
        price, or None if the order could not be filled at all."""
