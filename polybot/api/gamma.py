from __future__ import annotations

import json
import logging

from ..config import Settings
from ..models import Market
from .http import get_json

logger = logging.getLogger(__name__)

PAGE_LIMIT = 100


def _parse_json_field(value, default):
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return default


def _to_market(raw: dict) -> Market | None:
    token_ids = _parse_json_field(raw.get("clobTokenIds"), [])
    outcomes = _parse_json_field(raw.get("outcomes"), [])
    if not token_ids or not outcomes or len(token_ids) != len(outcomes):
        return None
    try:
        volume_24h = float(raw.get("volume24hr") or raw.get("volume24hrClob") or 0.0)
        liquidity = float(raw.get("liquidity") or raw.get("liquidityClob") or 0.0)
    except (TypeError, ValueError):
        return None
    return Market(
        condition_id=raw.get("conditionId", ""),
        question=raw.get("question", ""),
        slug=raw.get("slug", ""),
        token_ids=[str(t) for t in token_ids],
        outcomes=[str(o) for o in outcomes],
        volume_24h=volume_24h,
        liquidity=liquidity,
    )


def discover_markets(settings: Settings) -> list[Market]:
    """Scan active Polymarket markets via the Gamma API and filter by volume/liquidity.

    Mirrors the paginated-fetch-with-cursor pattern used in poly_data's
    update_markets.py, but stops once max_markets_scanned candidates worth
    reviewing have been collected (this bot re-scans every poll interval,
    unlike poly_data's one-shot full-catalog dump).
    """
    markets: list[Market] = []
    offset = 0
    url = f"{settings.gamma_api_url}/markets"

    while len(markets) < settings.max_markets_scanned:
        params = {
            "active": "true",
            "closed": "false",
            "limit": PAGE_LIMIT,
            "offset": offset,
            "order": "volume24hr",
            "ascending": "false",
        }
        page = get_json(url, params=params)
        if not page:
            break

        for raw in page:
            market = _to_market(raw)
            if market is None:
                continue
            if market.volume_24h < settings.min_volume_24h:
                continue
            if market.liquidity < settings.min_liquidity:
                continue
            markets.append(market)
            if len(markets) >= settings.max_markets_scanned:
                break

        if len(page) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT

    logger.info("discovered %d candidate markets (offset reached=%d)", len(markets), offset)
    return markets
