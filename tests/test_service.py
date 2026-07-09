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
