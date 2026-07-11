from datetime import datetime, timezone

from polybot.config import Settings
from polybot.models import Market, OrderBook, PricePoint, Signal
from polybot.portfolio import Portfolio
from polybot.scanner import _apply_smart_money_filter, generate_signals


class FakeTracker:
    def __init__(self, confirmed_tokens, raise_on_refresh=False):
        self.confirmed_tokens = confirmed_tokens
        self.raise_on_refresh = raise_on_refresh
        self.refreshed = False

    def refresh_recent_buys(self, settings):
        if self.raise_on_refresh:
            raise RuntimeError("data-api unreachable")
        self.refreshed = True

    def confirms(self, token_id):
        if token_id in self.confirmed_tokens:
            from datetime import datetime, timezone

            from polybot.models import WalletBuy

            return WalletBuy(wallet="0xabc", token_id=token_id, usd_size=100.0, timestamp=datetime.now(timezone.utc))
        return None


def make_signal(token_id, momentum=0.1):
    return Signal(
        token_id=token_id,
        condition_id="0xcond",
        market_question="Q?",
        outcome="Yes",
        momentum=momentum,
        reference_price=0.5,
    )


def test_filter_keeps_only_confirmed_signals(tmp_path):
    settings = Settings(data_dir=tmp_path)
    signals = [make_signal("tok-a"), make_signal("tok-b")]
    tracker = FakeTracker(confirmed_tokens={"tok-a"})

    result = _apply_smart_money_filter(settings, signals, tracker)

    assert [s.token_id for s in result] == ["tok-a"]
    assert tracker.refreshed is True


def test_filter_drops_everything_on_refresh_failure(tmp_path):
    settings = Settings(data_dir=tmp_path)
    signals = [make_signal("tok-a")]
    tracker = FakeTracker(confirmed_tokens={"tok-a"}, raise_on_refresh=True)

    result = _apply_smart_money_filter(settings, signals, tracker)

    assert result == []  # fail closed: no confirmation possible -> no trades


def test_filter_noop_on_empty_signals(tmp_path):
    settings = Settings(data_dir=tmp_path)
    tracker = FakeTracker(confirmed_tokens=set())

    result = _apply_smart_money_filter(settings, [], tracker)

    assert result == []
    assert tracker.refreshed is False


def test_generate_signals_skips_the_locked_out_opposite_outcome(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, momentum_threshold=0.05, min_price=0.05, max_price=0.95)
    market = Market(
        condition_id="0xcond",
        question="Will it happen?",
        slug="will-it-happen",
        token_ids=["tok-yes", "tok-no"],
        outcomes=["Yes", "No"],
        volume_24h=10_000,
        liquidity=5_000,
    )

    def fake_history(settings, token_id, lookback_minutes):
        span = lookback_minutes * 60
        # Strong upward momentum for both tokens -- both would qualify as
        # signals if the market-level lock didn't block the opposite side.
        return [
            PricePoint(ts=0, price=0.40),
            PricePoint(ts=span // 2, price=0.45),
            PricePoint(ts=span, price=0.55),
        ]

    def fake_book(settings, token_id):
        return OrderBook(token_id=token_id, best_bid=0.54, best_ask=0.56)

    monkeypatch.setattr("polybot.scanner.clob.get_price_history", fake_history)
    monkeypatch.setattr("polybot.scanner.clob.get_order_book", fake_book)

    portfolio = Portfolio(cash=1000)
    portfolio.traded_markets["0xcond"] = "tok-yes"  # market already committed to the Yes side

    signals = generate_signals(settings, [market], portfolio, datetime.now(timezone.utc))

    # tok-no is permanently locked out; tok-yes (same side) is still a valid signal.
    assert [s.token_id for s in signals] == ["tok-yes"]


def test_generate_signals_uses_mean_reversion_when_selected(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path,
        strategy="mean_reversion",
        mean_reversion_threshold=0.05,
        min_price=0.05,
        max_price=0.95,
    )
    market = Market(
        condition_id="0xcond",
        question="Will it happen?",
        slug="will-it-happen",
        token_ids=["tok-yes"],
        outcomes=["Yes"],
        volume_24h=10_000,
        liquidity=5_000,
    )

    def fake_history(settings, token_id, lookback_minutes):
        span = lookback_minutes * 60
        # Rising momentum would reject this under the momentum strategy, but
        # mean-reversion only cares that the last point is below average --
        # which it isn't here, so this exercises the "wrong direction" branch.
        return [
            PricePoint(ts=0, price=0.50),
            PricePoint(ts=span // 2, price=0.50),
            PricePoint(ts=span, price=0.40),  # below the window average -> reversion candidate
        ]

    def fake_book(settings, token_id):
        return OrderBook(token_id=token_id, best_bid=0.39, best_ask=0.40)

    monkeypatch.setattr("polybot.scanner.clob.get_price_history", fake_history)
    monkeypatch.setattr("polybot.scanner.clob.get_order_book", fake_book)

    portfolio = Portfolio(cash=1000)
    signals = generate_signals(settings, [market], portfolio, datetime.now(timezone.utc))

    assert [s.token_id for s in signals] == ["tok-yes"]
    assert "Mean-reversion" in signals[0].reason
