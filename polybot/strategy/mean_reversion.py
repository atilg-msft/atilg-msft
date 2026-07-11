from __future__ import annotations

from ..config import Settings
from ..models import Market, OrderBook, PricePoint, Signal

MIN_HISTORY_POINTS = 3
MIN_SPAN_FRACTION = 0.5


def compute_deviation(history: list[PricePoint]) -> tuple[float, float] | None:
    """Mean price over the window, and how far the newest point sits below
    it as a fraction of the mean (negative = below, positive = above)."""
    if len(history) < MIN_HISTORY_POINTS:
        return None
    prices = [p.price for p in history]
    mean_price = sum(prices) / len(prices)
    if mean_price <= 0:
        return None
    last = history[-1].price
    return mean_price, (last - mean_price) / mean_price


def evaluate(
    settings: Settings,
    market: Market,
    token_id: str,
    history: list[PricePoint],
    order_book: OrderBook | None,
) -> Signal | None:
    """Long-only mean-reversion signal for a single outcome token.

    The mirror image of momentum: instead of betting a move continues, this
    bets a token that has dropped well below its own recent average bounces
    back toward it. Only the downward case is a signal (long-only, no
    short-selling) -- a token overextended *upward* shows up as its
    complementary outcome (YES vs NO) being overextended downward, scanned
    independently, same as momentum's YES/NO symmetry.
    """
    if len(history) >= MIN_HISTORY_POINTS:
        span = history[-1].ts - history[0].ts
        if span < settings.lookback_minutes * 60 * MIN_SPAN_FRACTION:
            return None
        last_price = history[-1].price
        result = compute_deviation(history)
    else:
        return None

    if result is None:
        return None
    mean_price, deviation = result
    if not (settings.min_price <= last_price <= settings.max_price):
        return None
    if deviation > -settings.mean_reversion_threshold:
        return None

    # Sanity-check against the live order book when available: don't chase
    # a stale price-history point that the current book no longer supports.
    if order_book is not None and order_book.best_ask is not None:
        if not (settings.min_price <= order_book.best_ask <= settings.max_price):
            return None

    reason = (
        f"Mean-reversion {deviation:+.1%} vs {settings.lookback_minutes}min average "
        f"{mean_price:.3f} (threshold -{settings.mean_reversion_threshold:.1%}), price {last_price:.3f}"
    )
    return Signal(
        token_id=token_id,
        condition_id=market.condition_id,
        market_question=market.question,
        outcome=market.token_outcome(token_id),
        momentum=abs(deviation),  # reused as generic "signal strength" for ranking (see scanner.generate_signals)
        reference_price=last_price,
        reason=reason,
    )
