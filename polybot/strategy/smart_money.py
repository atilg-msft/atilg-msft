from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from ..api import data_api
from ..config import Settings
from ..models import WalletBuy

logger = logging.getLogger(__name__)


class SmartMoneyTracker:
    """Tracks a rotating set of high-PnL wallets (from Polymarket's public
    leaderboard) and surfaces their recent buys, keyed by token, so momentum
    signals can be filtered down to ones a tracked wallet also just bought.

    This is a *confirmation filter*, not an independent signal source: on
    its own, a wallet's raw all-time PnL is easy to mistake for skill when
    it's really one lucky long-shot bet, and high-volume wallets are often
    market makers with no directional view at all. Requiring momentum AND a
    tracked wallet buying the same token cuts down on both kinds of false
    positives, at the cost of fewer trades overall.
    """

    def __init__(self) -> None:
        self._wallets: list[str] = []
        self._wallets_refreshed_at: float = 0.0
        self._recent_buys: dict[str, WalletBuy] = {}

    def _refresh_wallets(self, settings: Settings) -> None:
        now = time.monotonic()
        if self._wallets and now - self._wallets_refreshed_at < settings.smart_wallet_refresh_minutes * 60:
            return
        try:
            entries = data_api.get_leaderboard(
                settings,
                period=settings.smart_wallet_leaderboard_period,
                order_by="pnl",
                limit=settings.smart_wallet_count,
            )
            wallets = [e["wallet"] for e in entries]
        except Exception:
            logger.exception("failed to refresh leaderboard wallets; keeping previous list")
            wallets = list(self._wallets)

        overrides = [w.strip() for w in settings.smart_wallet_overrides.split(",") if w.strip()]
        merged = list(dict.fromkeys(wallets + overrides))  # de-dupe, preserve order

        if merged:
            self._wallets = merged
            self._wallets_refreshed_at = now
            logger.info("tracking %d smart-money wallet(s)", len(self._wallets))
        elif not self._wallets:
            logger.warning("smart-wallet tracking enabled but no wallets resolved (leaderboard empty/unreachable and no overrides configured)")

    def refresh_recent_buys(self, settings: Settings) -> None:
        self._refresh_wallets(settings)
        if not self._wallets:
            self._recent_buys = {}
            return

        cutoff = time.time() - settings.smart_wallet_lookback_minutes * 60
        buys: dict[str, WalletBuy] = {}
        for wallet in self._wallets:
            for entry in data_api.get_wallet_activity(settings, wallet, limit=20):
                if entry["side"] != "BUY" or entry["timestamp"] < cutoff:
                    continue
                token_id = entry["token_id"]
                candidate = WalletBuy(
                    wallet=wallet,
                    token_id=token_id,
                    usd_size=entry["usd_size"],
                    timestamp=datetime.fromtimestamp(entry["timestamp"], tz=timezone.utc),
                )
                existing = buys.get(token_id)
                if existing is None or candidate.timestamp > existing.timestamp:
                    buys[token_id] = candidate
        self._recent_buys = buys
        logger.info("smart-money: %d token(s) with a recent tracked-wallet buy", len(buys))

    def confirms(self, token_id: str) -> WalletBuy | None:
        return self._recent_buys.get(token_id)
