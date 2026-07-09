from __future__ import annotations

import logging

from ..api import clob
from ..config import Settings
from .base import ExecutorBase

logger = logging.getLogger(__name__)


class PaperExecutor(ExecutorBase):
    """Simulated fills: takes the current order book's best price and
    applies a slippage + fee haircut. No funds or orders ever leave the
    process — this is the default, safe mode."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _slippage_adjusted(self, price: float, side: str) -> float:
        slippage = self.settings.slippage_bps / 10_000
        fee = self.settings.fee_bps / 10_000
        if side == "buy":
            return price * (1 + slippage + fee)
        return price * (1 - slippage - fee)

    def buy(self, token_id: str, usd_amount: float, reference_price: float) -> float | None:
        book = clob.get_order_book(self.settings, token_id)
        base_price = book.best_ask if book and book.best_ask is not None else reference_price
        if base_price is None or base_price <= 0:
            return None
        return self._slippage_adjusted(base_price, "buy")

    def sell(self, token_id: str, size_tokens: float, reference_price: float) -> float | None:
        book = clob.get_order_book(self.settings, token_id)
        base_price = book.best_bid if book and book.best_bid is not None else reference_price
        if base_price is None or base_price <= 0:
            return None
        return self._slippage_adjusted(base_price, "sell")
