from polybot.api import data_api
from polybot.config import Settings


def make_settings(tmp_path):
    return Settings(data_dir=tmp_path)


def test_get_leaderboard_extracts_wallet_field(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polybot.api.data_api.get_json",
        lambda url, params: [{"proxyWallet": "0xabc", "pnl": 1000}, {"wallet": "0xdef"}],
    )
    result = data_api.get_leaderboard(make_settings(tmp_path), period="month", order_by="pnl", limit=10)
    assert [r["wallet"] for r in result] == ["0xabc", "0xdef"]


def test_get_leaderboard_handles_wrapped_response(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polybot.api.data_api.get_json",
        lambda url, params: {"data": [{"user": "0x123"}]},
    )
    result = data_api.get_leaderboard(make_settings(tmp_path), period="week", order_by="volume", limit=5)
    assert result == [{"wallet": "0x123", "raw": {"user": "0x123"}}]


def test_get_leaderboard_returns_empty_on_failure(tmp_path, monkeypatch):
    def raise_error(url, params):
        raise RuntimeError("boom")

    monkeypatch.setattr("polybot.api.data_api.get_json", raise_error)
    assert data_api.get_leaderboard(make_settings(tmp_path), "month", "pnl", 10) == []


def test_get_wallet_activity_normalizes_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polybot.api.data_api.get_json",
        lambda url, params: [
            {"side": "buy", "asset": "tok-1", "usdcSize": "42.5", "timestamp": 1000},
            {"side": "SELL", "tokenId": "tok-2", "size": 10, "time": 2000},
            {"side": "BUY", "timestamp": 3000},  # missing token id -> dropped
        ],
    )
    result = data_api.get_wallet_activity(make_settings(tmp_path), "0xabc", limit=20)
    assert result == [
        {"side": "BUY", "token_id": "tok-1", "usd_size": 42.5, "timestamp": 1000.0},
        {"side": "SELL", "token_id": "tok-2", "usd_size": 10.0, "timestamp": 2000.0},
    ]
