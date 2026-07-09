from datetime import datetime, timezone

from polybot.config import Settings
from polybot.models import Market, OrderBook, PricePoint
from polybot.strategy import momentum


def make_settings(**overrides) -> Settings:
    return Settings(data_dir=overrides.pop("data_dir"), **overrides)


def make_history(prices: list[float], step_seconds: int = 60) -> list[PricePoint]:
    now = int(datetime.now(timezone.utc).timestamp())
    n = len(prices)
    return [
        PricePoint(ts=now - (n - 1 - i) * step_seconds, price=p)
        for i, p in enumerate(prices)
    ]


def make_market() -> Market:
    return Market(
        condition_id="0xabc",
        question="Will it happen?",
        slug="will-it-happen",
        token_ids=["tok-yes", "tok-no"],
        outcomes=["Yes", "No"],
        volume_24h=5000,
        liquidity=2000,
    )


def test_compute_momentum_positive():
    history = make_history([0.40, 0.42, 0.45, 0.50])
    m = momentum.compute_momentum(history)
    assert m is not None
    assert abs(m - (0.50 - 0.40) / 0.40) < 1e-9


def test_compute_momentum_requires_min_points():
    assert momentum.compute_momentum(make_history([0.4, 0.5])) is None


def test_evaluate_generates_signal_above_threshold(tmp_path):
    settings = make_settings(
        data_dir=tmp_path,
        lookback_minutes=5,
        momentum_threshold=0.08,
        min_price=0.05,
        max_price=0.95,
    )
    market = make_market()
    history = make_history([0.40, 0.43, 0.46, 0.50, 0.55], step_seconds=60)
    book = OrderBook(token_id="tok-yes", best_bid=0.54, best_ask=0.55)

    signal = momentum.evaluate(settings, market, "tok-yes", history, book)

    assert signal is not None
    assert signal.token_id == "tok-yes"
    assert signal.outcome == "Yes"
    assert signal.momentum > settings.momentum_threshold


def test_evaluate_rejects_below_threshold(tmp_path):
    settings = make_settings(data_dir=tmp_path, lookback_minutes=5, momentum_threshold=0.20)
    market = make_market()
    history = make_history([0.40, 0.41, 0.42], step_seconds=120)
    assert momentum.evaluate(settings, market, "tok-yes", history, None) is None


def test_evaluate_rejects_out_of_price_bounds(tmp_path):
    settings = make_settings(
        data_dir=tmp_path, lookback_minutes=5, momentum_threshold=0.05,
        min_price=0.05, max_price=0.95,
    )
    market = make_market()
    # Momentum is large in relative terms but the price is right at the edge.
    history = make_history([0.90, 0.94, 0.97], step_seconds=120)
    assert momentum.evaluate(settings, market, "tok-yes", history, None) is None


def test_evaluate_rejects_sparse_history(tmp_path):
    settings = make_settings(data_dir=tmp_path, lookback_minutes=15, momentum_threshold=0.05)
    market = make_market()
    # Only spans a fraction of the configured lookback window.
    history = make_history([0.40, 0.50], step_seconds=30)
    assert momentum.evaluate(settings, market, "tok-yes", history, None) is None
