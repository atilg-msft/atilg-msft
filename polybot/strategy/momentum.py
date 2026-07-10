from __future__ import annotations

from ..config import Settings
from ..models import Market, OrderBook, PricePoint, Signal

MIN_HISTORY_POINTS = 3
MIN_SPAN_FRACTION = 0.5


def compute_momentum(history: list[PricePoint]) -> float | None:
    """Relative price change between the oldest and newest point in the window."""
    if len(history) < MIN_HISTORY_POINTS:
        return None
    reference = history[0].price
    last = history[-1].price
    if reference <= 0:
        return None
    return (last - reference) / reference


def evaluate(
    settings: Settings,
    market: Market,
    token_id: str,
    history: list[PricePoint],
    order_book: OrderBook | None,
) -> Signal | None:
    """Long-only momentum signal for a single outcome token.

    Each outcome of a binary market (YES and NO) is scanned as an
    independent tradable token, so a falling YES price shows up as
    positive momentum on the NO token — no short-selling required.
    """
    if len(history) >= MIN_HISTORY_POINTS:
        span = history[-1].ts - history[0].ts
        if span < settings.lookback_minutes * 60 * MIN_SPAN_FRACTION:
            return None
        last_price = history[-1].price
        momentum = compute_momentum(history)
    else:
        return None

    if momentum is None:
        return None
    if not (settings.min_price <= last_price <= settings.max_price):
        return None
    if momentum < settings.momentum_threshold:
        return None

    # Sanity-check against the live order book when available: don't chase
    # a stale price-history point that the current book no longer supports.
    if order_book is not None and order_book.best_ask is not None:
        if not (settings.min_price <= order_book.best_ask <= settings.max_price):
            return None

    reason = (
        f"Momentum {momentum:+.1%} over {settings.lookback_minutes}min "
        f"(threshold {settings.momentum_threshold:.1%}), price {last_price:.3f}"
    )
    return Signal(
        token_id=token_id,
        condition_id=market.condition_id,
        market_question=market.question,
        outcome=market.token_outcome(token_id),
        momentum=momentum,
        reference_price=last_price,
        reason=reason,
    )
