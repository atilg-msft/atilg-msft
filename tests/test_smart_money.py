import time

from polybot.config import Settings
from polybot.strategy.smart_money import SmartMoneyTracker


def make_settings(tmp_path, **overrides):
    overrides.setdefault("smart_wallet_refresh_minutes", 60)
    overrides.setdefault("smart_wallet_lookback_minutes", 30)
    return Settings(data_dir=tmp_path, **overrides)


def test_refresh_recent_buys_confirms_matching_token(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, smart_wallet_count=5)

    monkeypatch.setattr(
        "polybot.strategy.smart_money.data_api.get_leaderboard",
        lambda settings, period, order_by, limit: [{"wallet": "0xabc"}, {"wallet": "0xdef"}],
    )

    now = time.time()

    def fake_activity(settings, wallet, limit):
        if wallet == "0xabc":
            return [{"side": "BUY", "token_id": "tok-yes", "usd_size": 500.0, "timestamp": now - 60}]
        return [{"side": "SELL", "token_id": "tok-no", "usd_size": 100.0, "timestamp": now - 60}]

    monkeypatch.setattr("polybot.strategy.smart_money.data_api.get_wallet_activity", fake_activity)

    tracker = SmartMoneyTracker()
    tracker.refresh_recent_buys(settings)

    confirmed = tracker.confirms("tok-yes")
    assert confirmed is not None
    assert confirmed.wallet == "0xabc"
    assert tracker.confirms("tok-no") is None  # was a SELL, not a BUY
    assert tracker.confirms("tok-other") is None


def test_stale_buys_outside_lookback_window_are_ignored(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, smart_wallet_count=5, smart_wallet_lookback_minutes=10)

    monkeypatch.setattr(
        "polybot.strategy.smart_money.data_api.get_leaderboard",
        lambda settings, period, order_by, limit: [{"wallet": "0xabc"}],
    )

    stale_ts = time.time() - 3600  # 1 hour ago, older than the 10-minute window

    monkeypatch.setattr(
        "polybot.strategy.smart_money.data_api.get_wallet_activity",
        lambda settings, wallet, limit: [
            {"side": "BUY", "token_id": "tok-yes", "usd_size": 500.0, "timestamp": stale_ts}
        ],
    )

    tracker = SmartMoneyTracker()
    tracker.refresh_recent_buys(settings)

    assert tracker.confirms("tok-yes") is None


def test_overrides_are_merged_with_leaderboard(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, smart_wallet_overrides="0xoverride, 0xabc")

    monkeypatch.setattr(
        "polybot.strategy.smart_money.data_api.get_leaderboard",
        lambda settings, period, order_by, limit: [{"wallet": "0xabc"}],
    )
    monkeypatch.setattr(
        "polybot.strategy.smart_money.data_api.get_wallet_activity",
        lambda settings, wallet, limit: [],
    )

    tracker = SmartMoneyTracker()
    tracker.refresh_recent_buys(settings)

    assert set(tracker._wallets) == {"0xabc", "0xoverride"}


def test_leaderboard_failure_keeps_previous_wallet_list(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, smart_wallet_refresh_minutes=60)

    calls = {"n": 0}

    def flaky_leaderboard(settings, period, order_by, limit):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"wallet": "0xabc"}]
        raise RuntimeError("leaderboard unreachable")

    monkeypatch.setattr("polybot.strategy.smart_money.data_api.get_leaderboard", flaky_leaderboard)
    monkeypatch.setattr("polybot.strategy.smart_money.data_api.get_wallet_activity", lambda *a, **k: [])

    tracker = SmartMoneyTracker()
    tracker.refresh_recent_buys(settings)
    assert tracker._wallets == ["0xabc"]

    # Force a refresh despite the cache window, simulating a later failed call.
    tracker._wallets_refreshed_at = 0
    tracker.refresh_recent_buys(settings)
    assert tracker._wallets == ["0xabc"]  # unchanged, not wiped by the failure
