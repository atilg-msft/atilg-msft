from __future__ import annotations

import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)

_thread_local = threading.local()

DEFAULT_TIMEOUT = 15
MAX_ATTEMPTS = 8
BASE_BACKOFF = 0.5


def session() -> requests.Session:
    """Thread-local session so worker threads reuse connections (mirrors poly_data's _session())."""
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": "polybot/0.1"})
        _thread_local.session = s
    return s


def get_json(url: str, params: dict | None = None, timeout: int = DEFAULT_TIMEOUT) -> dict | list:
    """GET with exponential-backoff retry on transient failures (429/5xx/timeouts).

    A 4xx other than 429 (404 in practice: no order book for a delisted/
    inactive token) is not transient -- it will not succeed on retry, so it
    fails immediately instead of burning through all `MAX_ATTEMPTS` (up to
    ~2 minutes of backoff) for a request that can never succeed.
    """
    last_exc: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = session().get(url, params=params, timeout=timeout)
        except requests.RequestException as exc:
            last_exc = exc
            sleep_for = BASE_BACKOFF * (2**attempt)
            logger.warning(
                "GET %s failed (attempt %d/%d): %s — retrying in %.1fs",
                url,
                attempt + 1,
                MAX_ATTEMPTS,
                exc,
                sleep_for,
            )
            time.sleep(sleep_for)
            continue

        if resp.status_code == 429 or resp.status_code >= 500:
            last_exc = requests.HTTPError(f"retryable status {resp.status_code}", response=resp)
            sleep_for = BASE_BACKOFF * (2**attempt)
            logger.warning(
                "GET %s failed (attempt %d/%d): retryable status %d — retrying in %.1fs",
                url,
                attempt + 1,
                MAX_ATTEMPTS,
                resp.status_code,
                sleep_for,
            )
            time.sleep(sleep_for)
            continue

        if 400 <= resp.status_code < 500:
            logger.warning(
                "GET %s failed with non-retryable status %d; giving up immediately",
                url,
                resp.status_code,
            )
            raise RuntimeError(f"GET {url} failed with non-retryable status {resp.status_code}")

        try:
            return resp.json()
        except ValueError as exc:
            last_exc = exc
            sleep_for = BASE_BACKOFF * (2**attempt)
            logger.warning(
                "GET %s failed (attempt %d/%d): %s — retrying in %.1fs",
                url,
                attempt + 1,
                MAX_ATTEMPTS,
                exc,
                sleep_for,
            )
            time.sleep(sleep_for)
    raise RuntimeError(f"GET {url} failed after {MAX_ATTEMPTS} attempts") from last_exc
