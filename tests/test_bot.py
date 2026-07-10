from polybot.bot import run_cycle
from polybot.config import Settings
from polybot.execution.paper import PaperExecutor
from polybot.models import OrderBook
from polybot.portfolio import Portfolio, utcnow
from polybot.risk import RiskManager


def fake_order_book(settings, token_id):
    return OrderBook(token_id=token_id, best_bid=0.45, best_ask=0.46)


def test_run_cycle_returns_equity_and_mark_prices_for_open_positions(tmp_path, monkeypatch):
    monkeypatch.setattr("polybot.bot.discover_markets", lambda settings: [])
    monkeypatch.setattr("polybot.api.clob.get_order_book", fake_order_book)

    settings = Settings(data_dir=tmp_path)
    portfolio = Portfolio(cash=980)
    portfolio.open_position(
        token_id="tok-yes",
        condition_id="0xabc",
        market_question="Will it happen?",
        outcome="Yes",
        fill_price=0.40,
        cost_usd=20.0,
        opened_at=utcnow(),
    )
    risk = RiskManager(settings)
    executor = PaperExecutor(settings)

    equity, mark_prices = run_cycle(
        settings, portfolio, risk, executor, tmp_path / "trades.csv"
    )

    assert mark_prices["tok-yes"] == 0.455  # (0.45 + 0.46) / 2
    assert equity == portfolio.cash + 50 * 0.455
