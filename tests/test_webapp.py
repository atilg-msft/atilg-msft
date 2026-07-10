from fastapi.testclient import TestClient

from polybot.config import Settings
from polybot.models import OrderBook
from polybot.service import BotService
from polybot.webapp.app import create_app


def fake_order_book(settings, token_id):
    return OrderBook(token_id=token_id, best_bid=0.45, best_ask=0.46)


def test_control_panel_start_stop_liquidate(tmp_path, monkeypatch):
    monkeypatch.setattr("polybot.bot.discover_markets", lambda settings: [])
    monkeypatch.setattr("polybot.api.clob.get_order_book", fake_order_book)

    settings = Settings(data_dir=tmp_path, poll_interval_seconds=1)
    service = BotService(settings)
    client = TestClient(create_app(service))

    try:
        assert client.get("/").status_code == 200

        status = client.get("/api/status").json()
        assert status["state"] == "stopped"

        status = client.post("/api/start").json()
        assert status["state"] == "running"

        status = client.post("/api/stop").json()
        assert status["state"] == "stopped"

        result = client.post("/api/liquidate").json()
        assert result["closed_positions"] == []
        assert result["status"]["state"] == "stopped"
    finally:
        service.stop()


def test_strategy_selection_endpoints(tmp_path, monkeypatch):
    monkeypatch.setattr("polybot.bot.discover_markets", lambda settings: [])
    monkeypatch.setattr("polybot.api.clob.get_order_book", fake_order_book)

    settings = Settings(data_dir=tmp_path, poll_interval_seconds=1)
    service = BotService(settings)
    client = TestClient(create_app(service))

    try:
        info = client.get("/api/strategy").json()
        assert info["strategy"] == "momentum"
        assert info["signal_filter"] == "none"
        assert info["overridden"] is False

        info = client.post("/api/strategy", json={"signal_filter": "smart_money"}).json()
        assert info["signal_filter"] == "smart_money"
        assert info["overridden"] is True

        resp = client.post("/api/strategy", json={"signal_filter": "not-real"})
        assert resp.status_code == 400
    finally:
        service.stop()


def test_trades_endpoint_reflects_closed_positions(tmp_path, monkeypatch):
    monkeypatch.setattr("polybot.bot.discover_markets", lambda settings: [])
    monkeypatch.setattr("polybot.api.clob.get_order_book", fake_order_book)

    settings = Settings(data_dir=tmp_path, poll_interval_seconds=1)
    service = BotService(settings)
    client = TestClient(create_app(service))

    try:
        assert client.get("/api/trades").json() == []

        from polybot.portfolio import utcnow

        service.portfolio.open_position(
            token_id="tok-yes",
            condition_id="0xabc",
            market_question="Will it happen?",
            outcome="Yes",
            fill_price=0.40,
            cost_usd=20.0,
            opened_at=utcnow(),
            entry_reason="Momentum +12.0% over 15min (threshold 8.0%)",
        )
        service.liquidate()

        trades = client.get("/api/trades").json()
        assert len(trades) == 1
        assert trades[0]["reason"] == "manual_liquidation"
        assert trades[0]["entry_reason"].startswith("Momentum")
        assert "control panel" in trades[0]["exit_reason_detail"]
    finally:
        service.stop()


def test_close_position_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr("polybot.bot.discover_markets", lambda settings: [])
    monkeypatch.setattr("polybot.api.clob.get_order_book", fake_order_book)

    settings = Settings(data_dir=tmp_path, poll_interval_seconds=1)
    service = BotService(settings)
    client = TestClient(create_app(service))

    try:
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

        resp = client.post("/api/positions/tok-yes/close")
        assert resp.status_code == 200
        body = resp.json()
        assert body["token_id"] == "tok-yes"
        assert body["status"]["open_positions"] == []

        resp = client.post("/api/positions/tok-yes/close")
        assert resp.status_code == 404
    finally:
        service.stop()
