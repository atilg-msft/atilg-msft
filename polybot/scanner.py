from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from .api import clob
from .config import Settings
from .models import Market, Signal
from .portfolio import Portfolio
from .strategy import momentum
from .strategy.smart_money import SmartMoneyTracker

logger = logging.getLogger(__name__)

MAX_WORKERS = 20


def _evaluate_token(settings: Settings, market: Market, token_id: str) -> Signal | None:
    history = clob.get_price_history(settings, token_id, settings.lookback_minutes)
    book = clob.get_order_book(settings, token_id)
    return momentum.evaluate(settings, market, token_id, history, book)


def generate_signals(
    settings: Settings,
    markets: list[Market],
    portfolio: Portfolio,
    now: datetime,
    smart_money: SmartMoneyTracker | None = None,
) -> list[Signal]:
    """Fan out price-history/order-book lookups across a thread pool, mirroring
    poly_data's concurrent-fetch pattern in update_markets.py."""
    candidates: list[tuple[Market, str]] = []
    for market in markets:
        for token_id in market.token_ids:
            if token_id in portfolio.positions:
                continue
            if portfolio.is_in_cooldown(token_id, now):
                continue
            if portfolio.is_market_locked_out(market.condition_id, token_id):
                continue
            candidates.append((market, token_id))

    if not candidates:
        return []

    signals: list[Signal] = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(candidates))) as pool:
        futures = {
            pool.submit(_evaluate_token, settings, market, token_id): token_id
            for market, token_id in candidates
        }
        for future in as_completed(futures):
            token_id = futures[future]
            try:
                signal = future.result()
            except Exception:
                logger.exception("signal evaluation failed for token %s", token_id)
                continue
            if signal is not None:
                signals.append(signal)

    if settings.signal_filter == "smart_money" and smart_money is not None:
        signals = _apply_smart_money_filter(settings, signals, smart_money)

    signals.sort(key=lambda s: s.momentum, reverse=True)
    return signals


def _apply_smart_money_filter(
    settings: Settings, signals: list[Signal], smart_money: SmartMoneyTracker
) -> list[Signal]:
    if not signals:
        return signals
    try:
        smart_money.refresh_recent_buys(settings)
    except Exception:
        logger.exception("smart-money refresh failed; dropping all candidate signals this cycle")
        return []

    confirmed = []
    for signal in signals:
        buy = smart_money.confirms(signal.token_id)
        if buy is None:
            logger.debug("no smart-money confirmation for %s, dropping candidate", signal.token_id)
            continue
        logger.info(
            "smart-money confirmed %s: wallet %s bought $%.2f",
            signal.market_question[:60],
            buy.wallet,
            buy.usd_size,
        )
        signal.reason += f"; confirmed by wallet {buy.wallet[:10]}… buying ${buy.usd_size:.0f}"
        confirmed.append(signal)
    return confirmed
