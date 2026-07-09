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
    """GET with exponential-backoff retry on transient failures (429/5xx/timeouts)."""
    last_exc: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = session().get(url, params=params, timeout=timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"retryable status {resp.status_code}", response=resp)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
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
