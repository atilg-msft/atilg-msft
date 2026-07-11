import time

from polybot.config import Settings
from polybot.models import OrderBook
from polybot.service import BotService, BotState


def fake_order_book(settings, token_id):
    return OrderBook(token_id=token_id, best_bid=0.45, best_ask=0.46)


def make_settings(tmp_path, **overrides):
    overrides.setdefault("poll_interval_seconds", 1)
    return Settings(data_dir=tmp_path, **overrides)


def test_service_defaults_to_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr("polybot.bot.discover_markets", lambda settings: [])
    monkeypatch.setattr("polybot.api.clob.get_order_book", fake_order_book)

    service = BotService(make_settings(tmp_path))
    try:
        assert service.status()["state"] == "stopped"
        assert service.status()["last_cycle_at"] is None
    finally:
        service.stop()


def test_start_runs_cycles_and_stop_halts_thread(tmp_path, monkeypatch):
    monkeypatch.setattr("polybot.bot.discover_markets", lambda settings: [])
    monkeypatch.setattr("polybot.api.clob.get_order_book", fake_order_book)

    service = BotService(make_settings(tmp_path))
    service.start()
    assert service.status()["state"] == "running"

    deadline = time.monotonic() + 5
    while service.status()["last_cycle_at"] is None and time.monotonic() < deadline:
        time.sleep(0.1)
    assert service.status()["last_cycle_at"] is not None

    service.stop()
    assert service.status()["state"] == "stopped"
    assert not service._thread.is_alive()


def test_status_includes_current_value_for_open_positions(tmp_path, monkeypatch):
    monkeypatch.setattr("polybot.bot.discover_markets", lambda settings: [])
    monkeypatch.setattr("polybot.api.clob.get_order_book", fake_order_book)

    service = BotService(make_settings(tmp_path))
    from polybot.portfolio import utcnow

    service.portfolio.open_position(
        token_id="tok-yes",
        condition_id="0xabc",
        market_question="Will it happen?",
        outcome="Yes",
        fill_price=0.40,
        cost_usd=20.0,
        opened_at=utcnow(),
    )

    service.start()
    try:
        deadline = time.monotonic() + 5
        while service.status()["last_cycle_at"] is None and time.monotonic() < deadline:
            time.sleep(0.1)

        position = service.status()["open_positions"][0]
        # fake_order_book: best_bid=0.45, best_ask=0.46 -> mid=0.455
        assert position["current_price"] == 0.455
        assert position["current_value_usd"] == 50 * 0.455
        assert round(position["unrealized_pnl_usd"], 6) == round(50 * 0.455 - 20.0, 6)
        assert position["unrealized_pnl_pct"] > 0
    finally:
        service.stop()


def test_close_position_closes_only_the_requested_one(tmp_path, monkeypatch):
    monkeypatch.setattr("polybot.bot.discover_markets", lambda settings: [])
    monkeypatch.setattr("polybot.api.clob.get_order_book", fake_order_book)

    service = BotService(make_settings(tmp_path))
    from polybot.portfolio import utcnow

    service.portfolio.open_position(
        token_id="tok-yes",
        condition_id="0xabc",
        market_question="Will it happen?",
        outcome="Yes",
        fill_price=0.40,
        cost_usd=20.0,
        opened_at=utcnow(),
    )
    service.portfolio.open_position(
        token_id="tok-no",
        condition_id="0xdef",
        market_question="Will it not happen?",
        outcome="No",
        fill_price=0.60,
        cost_usd=20.0,
        opened_at=utcnow(),
    )

    service.start()  # closing one position shouldn't stop the loop
    try:
        result = service.close_position("tok-yes")

        assert result["token_id"] == "tok-yes"
        assert "tok-yes" not in service.portfolio.positions
        assert "tok-no" in service.portfolio.positions  # untouched
        assert service.status()["state"] == "running"  # unaffected by the close
        assert "manual_close" in (tmp_path / "trades.csv").read_text()
    finally:
        service.stop()


def test_close_position_raises_for_unknown_token(tmp_path, monkeypatch):
    monkeypatch.setattr("polybot.bot.discover_markets", lambda settings: [])
    monkeypatch.setattr("polybot.api.clob.get_order_book", fake_order_book)

    service = BotService(make_settings(tmp_path))
    try:
        try:
            service.close_position("no-such-token")
            assert False, "expected KeyError"
        except KeyError:
            pass
    finally:
        service.stop()


def test_liquidate_closes_all_open_positions(tmp_path, monkeypatch):
    monkeypatch.setattr("polybot.bot.discover_markets", lambda settings: [])
    monkeypatch.setattr("polybot.api.clob.get_order_book", fake_order_book)

    service = BotService(make_settings(tmp_path))
    from polybot.portfolio import utcnow

    service.portfolio.open_position(
        token_id="tok-yes",
        condition_id="0xabc",
        market_question="Will it happen?",
        outcome="Yes",
        fill_price=0.40,
        cost_usd=20.0,
        opened_at=utcnow(),
    )

    result = service.liquidate()

    assert len(result["closed_positions"]) == 1
    assert result["closed_positions"][0]["token_id"] == "tok-yes"
    assert service.portfolio.positions == {}
    assert service.status()["state"] == "stopped"
    assert (tmp_path / "trades.csv").exists()
    assert "manual_liquidation" in (tmp_path / "trades.csv").read_text()


def test_desired_state_survives_ungraceful_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("polybot.bot.discover_markets", lambda settings: [])
    monkeypatch.setattr("polybot.api.clob.get_order_book", fake_order_book)

    settings = make_settings(tmp_path)
    service1 = BotService(settings)
    service1.start()
    assert (tmp_path / "control_state.json").read_text() == '{"state": "running"}'

    # Simulate a crash: kill the loop thread without going through stop(),
    # which would otherwise persist "stopped" and defeat this test.
    service1._stop_event.set()
    service1._thread.join(timeout=5)

    service2 = BotService(settings)
    try:
        assert service2.status()["state"] == "running"
    finally:
        service2.stop()


def test_get_strategy_info_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr("polybot.bot.discover_markets", lambda settings: [])
    monkeypatch.setattr("polybot.api.clob.get_order_book", fake_order_book)

    service = BotService(make_settings(tmp_path))
    try:
        info = service.get_strategy_info()
        assert info["strategy"] == "momentum"
        assert info["signal_filter"] == "none"
        assert info["strategy_options"] == ["momentum"]
        assert "smart_money" in info["signal_filter_options"]
        assert info["overridden"] is False
    finally:
        service.stop()


def test_set_strategy_persists_and_overrides_app_config(tmp_path, monkeypatch):
    monkeypatch.setattr("polybot.bot.discover_markets", lambda settings: [])
    monkeypatch.setattr("polybot.api.clob.get_order_book", fake_order_book)

    settings = make_settings(tmp_path)
    service = BotService(settings)
    try:
        info = service.set_strategy(strategy=None, signal_filter="smart_money")
        assert info["signal_filter"] == "smart_money"
        assert info["overridden"] is True
        assert service.settings.signal_filter == "smart_money"
        assert (tmp_path / "strategy_override.json").exists()

        # A settings_provider (e.g. App Configuration refresh) that would
        # reset signal_filter back to "none" must not win over the override.
        service.settings_provider = lambda: Settings(data_dir=tmp_path, signal_filter="none")
        with service.lock:
            refreshed = service.settings_provider()
            if service._manual_overrides:
                from dataclasses import replace

                refreshed = replace(refreshed, **service._manual_overrides)
            service.settings = refreshed
        assert service.settings.signal_filter == "smart_money"
    finally:
        service.stop()


def test_set_strategy_rejects_invalid_choice(tmp_path, monkeypatch):
    monkeypatch.setattr("polybot.bot.discover_markets", lambda settings: [])
    monkeypatch.setattr("polybot.api.clob.get_order_book", fake_order_book)

    service = BotService(make_settings(tmp_path))
    try:
        try:
            service.set_strategy(strategy=None, signal_filter="not-a-real-option")
            assert False, "expected ValueError"
        except ValueError:
            pass
        assert service.get_strategy_info()["overridden"] is False
    finally:
        service.stop()


def test_strategy_override_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("polybot.bot.discover_markets", lambda settings: [])
    monkeypatch.setattr("polybot.api.clob.get_order_book", fake_order_book)

    settings = make_settings(tmp_path)
    service1 = BotService(settings)
    service1.set_strategy(strategy=None, signal_filter="smart_money")
    service1.stop()

    service2 = BotService(settings)
    try:
        assert service2.get_strategy_info()["signal_filter"] == "smart_money"
        assert service2.get_strategy_info()["overridden"] is True
    finally:
        service2.stop()


def test_executor_rebuilds_when_mode_flips_to_live(tmp_path, monkeypatch):
    monkeypatch.setattr("polybot.bot.discover_markets", lambda settings: [])
    monkeypatch.setattr("polybot.api.clob.get_order_book", fake_order_book)

    calls = []

    def fake_get_executor(settings):
        calls.append(settings.mode)
        return f"executor-for-{settings.mode}"

    monkeypatch.setattr("polybot.service.get_executor", fake_get_executor)

    settings = make_settings(tmp_path, mode="paper")
    service = BotService(settings)
    assert calls == ["paper"]
    assert service.executor == "executor-for-paper"

    live_settings = make_settings(tmp_path, mode="live", private_key="0xabc")
    service.settings_provider = lambda: live_settings

    service.start()
    try:
        deadline = time.monotonic() + 5
        while len(calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert calls == ["paper", "live"]
        assert service.executor == "executor-for-live"
        assert service.settings.mode == "live"
    finally:
        service.stop()


def test_executor_rebuild_failure_rolls_back_settings_too(tmp_path, monkeypatch):
    monkeypatch.setattr("polybot.bot.discover_markets", lambda settings: [])
    monkeypatch.setattr("polybot.api.clob.get_order_book", fake_order_book)

    def fake_get_executor(settings):
        if settings.mode == "live":
            raise RuntimeError("bad credentials")
        return "paper-executor"

    monkeypatch.setattr("polybot.service.get_executor", fake_get_executor)

    settings = make_settings(tmp_path, mode="paper")
    service = BotService(settings)
    assert service.executor == "paper-executor"

    live_settings = make_settings(tmp_path, mode="live", private_key="0xabc")
    service.settings_provider = lambda: live_settings

    service.start()
    try:
        deadline = time.monotonic() + 5
        while service.last_cycle_at is None and time.monotonic() < deadline:
            time.sleep(0.05)
        # The executor swap failed, so both settings and executor must have
        # been rolled back together -- never mode="live" with the old
        # (paper) executor still in place, or vice versa.
        assert service.executor == "paper-executor"
        assert service.settings.mode == "paper"
        assert service.status()["state"] == "running"  # loop kept going
    finally:
        service.stop()


def test_fast_tick_manages_positions_without_waiting_for_full_cycle(tmp_path, monkeypatch):
    scan_calls = []

    def counting_discover_markets(settings):
        scan_calls.append(1)
        return []

    monkeypatch.setattr("polybot.bot.discover_markets", counting_discover_markets)
    monkeypatch.setattr("polybot.api.clob.get_order_book", fake_order_book)

    # poll_interval_seconds is deliberately huge so the full scan cycle would
    # never run within the test's timeout -- only the fast position-check
    # tick (1s here) can be what closes the position below.
    settings = make_settings(tmp_path, poll_interval_seconds=3600, position_check_interval_seconds=1)
    service = BotService(settings)
    from polybot.portfolio import utcnow

    service.portfolio.open_position(
        token_id="tok-yes",
        condition_id="0xabc",
        market_question="Will it happen?",
        outcome="Yes",
        fill_price=1.0,  # mid=0.455 is a huge drop past the default 10% stop-loss
        cost_usd=20.0,
        opened_at=utcnow(),
    )

    service.start()
    try:
        deadline = time.monotonic() + 5
        while "tok-yes" in service.portfolio.positions and time.monotonic() < deadline:
            time.sleep(0.05)
        assert "tok-yes" not in service.portfolio.positions
        assert scan_calls == []  # the 3600s full cycle never fired
    finally:
        service.stop()


def test_keyvault_provider_refreshed_every_cycle(tmp_path, monkeypatch):
    monkeypatch.setattr("polybot.bot.discover_markets", lambda settings: [])
    monkeypatch.setattr("polybot.api.clob.get_order_book", fake_order_book)

    class FakeKeyVaultProvider:
        def __init__(self):
            self.refresh_count = 0

        def refresh(self):
            self.refresh_count += 1

    keyvault_provider = FakeKeyVaultProvider()
    service = BotService(make_settings(tmp_path), keyvault_provider=keyvault_provider)
    service.start()
    try:
        deadline = time.monotonic() + 5
        while keyvault_provider.refresh_count < 1 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert keyvault_provider.refresh_count >= 1
    finally:
        service.stop()
