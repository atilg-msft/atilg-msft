import pytest
import requests

from polybot.api import http


class FakeResponse:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json_data = json_data

    def json(self):
        if self._json_data is None:
            raise ValueError("no json body")
        return self._json_data


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def test_get_json_gives_up_immediately_on_404(monkeypatch):
    fake = FakeSession([FakeResponse(404)])
    monkeypatch.setattr(http, "session", lambda: fake)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)

    with pytest.raises(RuntimeError):
        http.get_json("http://example.test/book")

    assert fake.calls == 1  # no retries for a non-retryable 4xx


def test_get_json_retries_on_500_then_succeeds(monkeypatch):
    fake = FakeSession([FakeResponse(500), FakeResponse(200, {"ok": True})])
    monkeypatch.setattr(http, "session", lambda: fake)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)

    result = http.get_json("http://example.test/book")

    assert result == {"ok": True}
    assert fake.calls == 2


def test_get_json_retries_on_429(monkeypatch):
    fake = FakeSession([FakeResponse(429), FakeResponse(200, {"ok": True})])
    monkeypatch.setattr(http, "session", lambda: fake)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)

    result = http.get_json("http://example.test/book")

    assert result == {"ok": True}
    assert fake.calls == 2


def test_get_json_retries_on_connection_error(monkeypatch):
    fake = FakeSession([requests.ConnectionError("boom"), FakeResponse(200, {"ok": True})])
    monkeypatch.setattr(http, "session", lambda: fake)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)

    result = http.get_json("http://example.test/book")

    assert result == {"ok": True}
    assert fake.calls == 2


def test_get_json_exhausts_attempts_on_persistent_500(monkeypatch):
    fake = FakeSession([FakeResponse(500)] * http.MAX_ATTEMPTS)
    monkeypatch.setattr(http, "session", lambda: fake)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)

    with pytest.raises(RuntimeError):
        http.get_json("http://example.test/book")

    assert fake.calls == http.MAX_ATTEMPTS
