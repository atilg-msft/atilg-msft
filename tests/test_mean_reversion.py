from datetime import datetime, timezone

from polybot.config import Settings
from polybot.models import Market, OrderBook, PricePoint
from polybot.strategy import mean_reversion


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


def test_compute_deviation_below_average():
    # mean = (0.50+0.50+0.50+0.40)/4 = 0.475, last=0.40 -> deviation negative
    history = make_history([0.50, 0.50, 0.50, 0.40])
    result = mean_reversion.compute_deviation(history)
    assert result is not None
    mean_price, deviation = result
    assert abs(mean_price - 0.475) < 1e-9
    assert deviation < 0


def test_compute_deviation_requires_min_points():
    assert mean_reversion.compute_deviation(make_history([0.4, 0.5])) is None


def test_evaluate_generates_signal_when_price_dropped_below_average(tmp_path):
    settings = make_settings(
        data_dir=tmp_path,
        lookback_minutes=5,
        mean_reversion_threshold=0.08,
        min_price=0.05,
        max_price=0.95,
    )
    market = make_market()
    # mean ~0.50, last price 0.40 -> ~-20% deviation, well past the 8% threshold
    history = make_history([0.50, 0.51, 0.49, 0.50, 0.40], step_seconds=60)
    book = OrderBook(token_id="tok-yes", best_bid=0.39, best_ask=0.40)

    signal = mean_reversion.evaluate(settings, market, "tok-yes", history, book)

    assert signal is not None
    assert signal.token_id == "tok-yes"
    assert signal.outcome == "Yes"
    assert signal.momentum > settings.mean_reversion_threshold
    assert "Mean-reversion" in signal.reason
    assert "threshold" in signal.reason


def test_evaluate_rejects_price_above_average(tmp_path):
    settings = make_settings(data_dir=tmp_path, lookback_minutes=5, mean_reversion_threshold=0.08)
    market = make_market()
    # Price trending up -- last point is *above* the average, not a reversion candidate.
    history = make_history([0.40, 0.43, 0.46, 0.50, 0.55], step_seconds=60)
    assert mean_reversion.evaluate(settings, market, "tok-yes", history, None) is None


def test_evaluate_rejects_below_threshold(tmp_path):
    settings = make_settings(data_dir=tmp_path, lookback_minutes=5, mean_reversion_threshold=0.20)
    market = make_market()
    # Only a small dip below the average -- doesn't clear a 20% threshold.
    history = make_history([0.50, 0.50, 0.49, 0.48], step_seconds=120)
    assert mean_reversion.evaluate(settings, market, "tok-yes", history, None) is None


def test_evaluate_rejects_out_of_price_bounds(tmp_path):
    settings = make_settings(
        data_dir=tmp_path, lookback_minutes=5, mean_reversion_threshold=0.05,
        min_price=0.05, max_price=0.95,
    )
    market = make_market()
    # Large relative dip (well past the 5% threshold) but the last price
    # itself has fallen below min_price=0.05.
    history = make_history([0.12, 0.10, 0.03], step_seconds=120)
    assert mean_reversion.evaluate(settings, market, "tok-yes", history, None) is None


def test_evaluate_rejects_sparse_history(tmp_path):
    settings = make_settings(data_dir=tmp_path, lookback_minutes=15, mean_reversion_threshold=0.05)
    market = make_market()
    # Only spans a fraction of the configured lookback window.
    history = make_history([0.50, 0.40], step_seconds=30)
    assert mean_reversion.evaluate(settings, market, "tok-yes", history, None) is None
