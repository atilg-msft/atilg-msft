from __future__ import annotations

import logging
import time

from .api.gamma import discover_markets
from .config import Settings, load_settings
from .execution.factory import get_executor
from .logging_conf import setup_logging
from .portfolio import Portfolio, utcnow
from .risk import RiskManager
from .scanner import generate_signals
from .strategy.smart_money import SmartMoneyTracker

logger = logging.getLogger(__name__)

MIN_TRADE_USD = 1.0


def run_cycle(
    settings: Settings,
    portfolio: Portfolio,
    risk: RiskManager,
    executor,
    trade_log_path,
    smart_money: SmartMoneyTracker | None = None,
) -> float:
    """Run one scan/manage/trade cycle. Returns the resulting equity estimate."""
    now = utcnow()
    mark_prices: dict[str, float] = {}

    # 1. Manage existing positions first: exit signals take priority over
    # opening new ones so exposure/position-count checks below see fresh state.
    from .api import clob  # local import keeps module import graph simple

    for token_id, position in list(portfolio.positions.items()):
        book = clob.get_order_book(settings, token_id)
        mark = book.mid if book and book.mid is not None else position.entry_price
        mark_prices[token_id] = mark

        reason = risk.check_exit(position, mark, now)
        if reason is None:
            continue
        fill_price = executor.sell(token_id, position.size_tokens, mark)
        if fill_price is None:
            logger.warning("exit signal for %s (%s) but sell failed; will retry next cycle", token_id, reason)
            continue
        portfolio.close_position(
            token_id, fill_price, now, reason, settings.cooldown_minutes, trade_log_path
        )

    equity = portfolio.equity(mark_prices)

    # 2. Look for new entries only if there's room under the risk budget.
    if risk.can_open_new_position(portfolio, equity):
        markets = discover_markets(settings)
        signals = generate_signals(settings, markets, portfolio, now, smart_money=smart_money)
        logger.info("scanned %d markets, %d signals after filters", len(markets), len(signals))

        for signal in signals:
            if not risk.can_open_new_position(portfolio, equity):
                break
            if portfolio.is_in_cooldown(signal.token_id, now):
                continue
            size_usd = risk.position_size_usd(portfolio, equity)
            if size_usd < MIN_TRADE_USD:
                continue
            fill_price = executor.buy(signal.token_id, size_usd, signal.reference_price)
            if fill_price is None:
                logger.warning("buy failed for token %s, skipping", signal.token_id)
                continue
            portfolio.open_position(
                signal.token_id,
                signal.condition_id,
                signal.market_question,
                signal.outcome,
                fill_price,
                size_usd,
                now,
            )
            equity = portfolio.equity({**mark_prices, signal.token_id: fill_price})
    else:
        logger.info("risk budget full (%d positions, $%.2f exposure) — skipping scan", len(portfolio.positions), portfolio.exposure_usd())

    logger.info(
        "cycle done: cash=$%.2f equity=$%.2f open_positions=%d realized_pnl=$%.2f",
        portfolio.cash,
        equity,
        len(portfolio.positions),
        portfolio.realized_pnl,
    )
    return equity


def main() -> None:
    settings = load_settings()
    setup_logging(settings.data_dir)

    logger.info("starting polybot in %s mode", settings.mode)
    if settings.mode == "live":
        logger.warning(
            "LIVE MODE: real orders, real funds. Ctrl+C now if this wasn't intentional."
        )

    portfolio_path = settings.data_dir / "portfolio.json"
    trade_log_path = settings.data_dir / "trades.csv"
    portfolio = Portfolio.load_or_create(portfolio_path, settings.starting_cash)
    risk = RiskManager(settings)
    executor = get_executor(settings)
    smart_money = SmartMoneyTracker() if settings.smart_wallet_enabled else None

    try:
        while True:
            try:
                run_cycle(settings, portfolio, risk, executor, trade_log_path, smart_money=smart_money)
            except Exception:
                logger.exception("cycle failed, will retry after poll interval")
            finally:
                portfolio.save(portfolio_path)
            time.sleep(settings.poll_interval_seconds)
    except KeyboardInterrupt:
        logger.info("shutting down, saving portfolio state")
        portfolio.save(portfolio_path)


if __name__ == "__main__":
    main()
