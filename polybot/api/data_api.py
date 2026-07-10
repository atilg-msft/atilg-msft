from __future__ import annotations

import logging

from ..config import Settings
from .http import get_json

logger = logging.getLogger(__name__)

_WALLET_KEYS = ("proxyWallet", "wallet", "user", "address", "account")
_TOKEN_KEYS = ("asset", "tokenId", "token_id", "clobTokenId")
_SIDE_KEYS = ("side", "type")
_TIMESTAMP_KEYS = ("timestamp", "time", "createdAt")
_SIZE_KEYS = ("usdcSize", "size", "amount")

_logged_sample_keys = {"leaderboard": False, "activity": False}


def _first_present(entry: dict, keys: tuple[str, ...]):
    for key in keys:
        if key in entry and entry[key] is not None:
            return entry[key]
    return None


def _log_sample_keys_once(kind: str, entry: dict) -> None:
    """One-time diagnostic in case Polymarket ever renames these fields:
    if wallet/token/side parsing below starts silently dropping everything,
    this line in the log shows the real keys to fix
    _WALLET_KEYS/_TOKEN_KEYS/etc against.
    """
    if _logged_sample_keys.get(kind):
        return
    _logged_sample_keys[kind] = True
    logger.info("data-api %s sample fields: %s", kind, sorted(entry.keys()))


_LEADERBOARD_PATHS = {"pnl": "profit", "volume": "volume"}


def get_leaderboard(
    settings: Settings, period: str, order_by: str, limit: int
) -> list[dict]:
    """Top wallets by pnl or volume over `period` ('day'/'week'/'month'/'all').

    Lives on a separate host (lb-api.polymarket.com, not data-api.polymarket.com)
    with one path per ranking (/profit, /volume) rather than a single
    endpoint with an orderBy param -- confirmed via a live call.
    """
    path = _LEADERBOARD_PATHS.get(order_by, "profit")
    url = f"{settings.leaderboard_api_url}/{path}"
    try:
        raw = get_json(url, params={"period": period, "limit": limit})
    except RuntimeError:
        logger.warning("failed to fetch leaderboard (period=%s, order_by=%s)", period, order_by)
        return []

    entries = raw if isinstance(raw, list) else raw.get("data", []) if isinstance(raw, dict) else []
    if entries:
        _log_sample_keys_once("leaderboard", entries[0])

    wallets = []
    for entry in entries:
        wallet = _first_present(entry, _WALLET_KEYS)
        if wallet:
            wallets.append({"wallet": str(wallet), "raw": entry})
    return wallets


def get_wallet_activity(settings: Settings, wallet: str, limit: int) -> list[dict]:
    """Recent on-chain trade activity for a wallet, normalized to
    {side, token_id, usd_size, timestamp} (timestamp = unix seconds).

    `type=TRADE` filters out non-trade activity (REDEEM/MERGE/SPLIT/etc,
    which come back with empty side/asset fields) server-side. Endpoint,
    params, and field names (proxyWallet/asset/side/usdcSize/timestamp)
    confirmed via a live call.
    """
    url = f"{settings.data_api_url}/activity"
    try:
        raw = get_json(url, params={"user": wallet, "limit": limit, "type": "TRADE"})
    except RuntimeError:
        logger.warning("failed to fetch activity for wallet %s", wallet)
        return []

    entries = raw if isinstance(raw, list) else raw.get("data", []) if isinstance(raw, dict) else []
    if entries:
        _log_sample_keys_once("activity", entries[0])

    normalized = []
    for entry in entries:
        side = _first_present(entry, _SIDE_KEYS)
        token_id = _first_present(entry, _TOKEN_KEYS)
        timestamp = _first_present(entry, _TIMESTAMP_KEYS)
        size = _first_present(entry, _SIZE_KEYS)
        if side is None or token_id is None or timestamp is None:
            continue
        try:
            normalized.append(
                {
                    "side": str(side).upper(),
                    "token_id": str(token_id),
                    "usd_size": float(size) if size is not None else 0.0,
                    "timestamp": float(timestamp),
                }
            )
        except (TypeError, ValueError):
            continue
    return normalized
