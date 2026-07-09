from __future__ import annotations

import logging
import time

from ..config import Settings
from ..models import OrderBook, PricePoint
from .http import get_json

logger = logging.getLogger(__name__)


def get_order_book(settings: Settings, token_id: str) -> OrderBook | None:
    url = f"{settings.clob_api_url}/book"
    try:
        raw = get_json(url, params={"token_id": token_id})
    except RuntimeError:
        logger.warning("failed to fetch order book for token %s", token_id)
        return None

    bids = raw.get("bids") or []
    asks = raw.get("asks") or []
    try:
        best_bid = max((float(b["price"]) for b in bids), default=None)
        best_ask = min((float(a["price"]) for a in asks), default=None)
    except (TypeError, ValueError, KeyError):
        return None
    return OrderBook(token_id=token_id, best_bid=best_bid, best_ask=best_ask)


def get_price_history(
    settings: Settings, token_id: str, lookback_minutes: int
) -> list[PricePoint]:
    url = f"{settings.clob_api_url}/prices-history"
    now = int(time.time())
    start = now - lookback_minutes * 60
    try:
        raw = get_json(
            url,
            params={
                "market": token_id,
                "startTs": start,
                "endTs": now,
                "fidelity": 1,
            },
        )
    except RuntimeError:
        logger.warning("failed to fetch price history for token %s", token_id)
        return []

    history = raw.get("history") if isinstance(raw, dict) else None
    if not history:
        return []
    points: list[PricePoint] = []
    for entry in history:
        try:
            points.append(PricePoint(ts=int(entry["t"]), price=float(entry["p"])))
        except (KeyError, TypeError, ValueError):
            continue
    points.sort(key=lambda p: p.ts)
    return points
