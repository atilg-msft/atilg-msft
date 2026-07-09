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
    """One-time diagnostic: the exact field names data-api.polymarket.com
    returns were not verified against a live call while building this (the
    sandbox this was developed in had no outbound access to polymarket.com).
    If wallet/token/side parsing below silently drops everything, this line
    in the log shows you the real keys to fix _WALLET_KEYS/_TOKEN_KEYS/etc.
    """
    if _logged_sample_keys.get(kind):
        return
    _logged_sample_keys[kind] = True
    logger.info("data-api %s sample fields: %s", kind, sorted(entry.keys()))


def get_leaderboard(
    settings: Settings, period: str, order_by: str, limit: int
) -> list[dict]:
    """Top wallets by pnl or volume over `period` (e.g. 'day'/'week'/'month').

    NOTE: endpoint path/params are based on polymarket-cli's documented
    `data leaderboard --period --order-by` command, not a live-verified call
    -- see README's "Smart-money confirmation filter" section before relying
    on this in production.
    """
    url = f"{settings.data_api_url}/leaderboard"
    try:
        raw = get_json(url, params={"period": period, "orderBy": order_by, "limit": limit})
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

    Same live-verification caveat as get_leaderboard applies.
    """
    url = f"{settings.data_api_url}/activity"
    try:
        raw = get_json(url, params={"user": wallet, "limit": limit})
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
