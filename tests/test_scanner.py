from polybot.config import Settings
from polybot.models import Signal
from polybot.scanner import _apply_smart_money_filter


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
