from __future__ import annotations

import logging

from ..config import Settings
from .base import ExecutorBase

logger = logging.getLogger(__name__)

_SIGNATURE_TYPES = {"eoa": 0, "proxy": 1, "gnosis-safe": 2}


class LiveExecutor(ExecutorBase):
    """Places real orders on Polymarket's CLOB via py-clob-client.

    EXPERIMENTAL: exercised far less than PaperExecutor. Before pointing
    this at real funds: fund + approve the wallet exactly like
    polymarket-cli's `approve set` step does (this bot does not manage
    ERC-20/ERC-1155 approvals itself), start with tiny POLYBOT_MAX_POSITION_USD,
    and watch the first few fills manually.
    """

    def __init__(self, settings: Settings) -> None:
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import MarketOrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY, SELL
        except ImportError as exc:
            raise RuntimeError(
                "Live trading requires the optional dependency: "
                "pip install -e '.[live]'"
            ) from exc

        if settings.signature_type not in _SIGNATURE_TYPES:
            raise ValueError(f"Unknown signature_type: {settings.signature_type}")

        self._MarketOrderArgs = MarketOrderArgs
        self._OrderType = OrderType
        self._BUY = BUY
        self._SELL = SELL
        self.settings = settings

        self.client = ClobClient(
            settings.clob_api_url,
            key=settings.private_key,
            chain_id=settings.chain_id,
            signature_type=_SIGNATURE_TYPES[settings.signature_type],
            funder=settings.funder_address,
        )
        creds = self.client.create_or_derive_api_creds()
        self.client.set_api_creds(creds)
        logger.warning("LIVE TRADING ENABLED — real orders will be placed with real funds.")

    def _submit_market_order(self, token_id: str, amount: float, side: str) -> dict:
        order_args = self._MarketOrderArgs(token_id=token_id, amount=amount, side=side)
        signed_order = self.client.create_market_order(order_args)
        return self.client.post_order(signed_order, self._OrderType.FOK)

    @staticmethod
    def _extract_fill_price(resp: dict, fallback: float) -> float:
        for key in ("price", "avgPrice", "average_price"):
            value = resp.get(key) if isinstance(resp, dict) else None
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        logger.warning("could not parse fill price from order response, using reference price")
        return fallback

    def buy(self, token_id: str, usd_amount: float, reference_price: float) -> float | None:
        try:
            resp = self._submit_market_order(token_id, usd_amount, self._BUY)
        except Exception:
            logger.exception("live buy order failed for token %s", token_id)
            return None
        return self._extract_fill_price(resp, reference_price)

    def sell(self, token_id: str, size_tokens: float, reference_price: float) -> float | None:
        try:
            resp = self._submit_market_order(token_id, size_tokens, self._SELL)
        except Exception:
            logger.exception("live sell order failed for token %s", token_id)
            return None
        return self._extract_fill_price(resp, reference_price)
