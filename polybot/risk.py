from __future__ import annotations

from datetime import datetime, timedelta

from .config import Settings
from .portfolio import Portfolio, Position


class RiskManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def position_size_usd(self, portfolio: Portfolio, equity: float) -> float:
        budget = min(
            self.settings.max_position_usd,
            self.settings.position_pct_of_equity * equity,
            portfolio.cash,
        )
        return max(budget, 0.0)

    def can_open_new_position(self, portfolio: Portfolio, equity: float) -> bool:
        if len(portfolio.positions) >= self.settings.max_concurrent_positions:
            return False
        exposure_budget = self.settings.max_total_exposure_pct * equity
        if portfolio.exposure_usd() >= exposure_budget:
            return False
        return True

    def check_exit(self, position: Position, mark_price: float, now: datetime) -> str | None:
        pct_change = (mark_price - position.entry_price) / position.entry_price
        if pct_change >= self.settings.take_profit_pct:
            return "take_profit"
        if pct_change <= -self.settings.stop_loss_pct:
            return "stop_loss"
        opened_at = datetime.fromisoformat(position.opened_at)
        if now - opened_at >= timedelta(minutes=self.settings.max_holding_minutes):
            return "max_holding_time"
        return None
