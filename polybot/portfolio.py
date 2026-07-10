from __future__ import annotations

import csv
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

TRADE_LOG_FIELDS = [
    "closed_at",
    "opened_at",
    "token_id",
    "condition_id",
    "market_question",
    "outcome",
    "entry_price",
    "exit_price",
    "size_tokens",
    "cost_usd",
    "proceeds_usd",
    "pnl_usd",
    "pnl_pct",
    "reason",
    "entry_reason",
    "exit_reason_detail",
]


@dataclass
class Position:
    token_id: str
    condition_id: str
    market_question: str
    outcome: str
    entry_price: float
    size_tokens: float
    cost_usd: float
    opened_at: str  # ISO 8601
    entry_reason: str = ""  # human-readable rationale, shown in the control panel


class Portfolio:
    def __init__(
        self,
        cash: float,
        positions: dict[str, Position] | None = None,
        cooldown_until: dict[str, str] | None = None,
        realized_pnl: float = 0.0,
    ) -> None:
        self.cash = cash
        self.positions: dict[str, Position] = positions or {}
        self.cooldown_until: dict[str, str] = cooldown_until or {}
        self.realized_pnl = realized_pnl

    def equity(self, mark_prices: dict[str, float]) -> float:
        value = self.cash
        for token_id, pos in self.positions.items():
            mark = mark_prices.get(token_id, pos.entry_price)
            value += pos.size_tokens * mark
        return value

    def exposure_usd(self) -> float:
        return sum(pos.cost_usd for pos in self.positions.values())

    def is_in_cooldown(self, token_id: str, now: datetime) -> bool:
        until = self.cooldown_until.get(token_id)
        if until is None:
            return False
        return datetime.fromisoformat(until) > now

    def open_position(
        self,
        token_id: str,
        condition_id: str,
        market_question: str,
        outcome: str,
        fill_price: float,
        cost_usd: float,
        opened_at: datetime,
        entry_reason: str = "",
    ) -> None:
        size_tokens = cost_usd / fill_price
        self.positions[token_id] = Position(
            token_id=token_id,
            condition_id=condition_id,
            market_question=market_question,
            outcome=outcome,
            entry_price=fill_price,
            size_tokens=size_tokens,
            cost_usd=cost_usd,
            opened_at=opened_at.isoformat(),
            entry_reason=entry_reason,
        )
        self.cash -= cost_usd
        logger.info(
            "OPEN %s (%s) %.4f tokens @ %.4f = $%.2f -- %s",
            outcome,
            market_question[:60],
            size_tokens,
            fill_price,
            cost_usd,
            entry_reason,
        )

    def close_position(
        self,
        token_id: str,
        exit_price: float,
        closed_at: datetime,
        reason: str,
        cooldown_minutes: int,
        trade_log_path: Path,
        exit_reason_detail: str = "",
    ) -> float:
        pos = self.positions.pop(token_id)
        proceeds_usd = pos.size_tokens * exit_price
        pnl_usd = proceeds_usd - pos.cost_usd
        pnl_pct = pnl_usd / pos.cost_usd if pos.cost_usd else 0.0
        self.cash += proceeds_usd
        self.realized_pnl += pnl_usd
        self.cooldown_until[token_id] = (
            closed_at + timedelta(minutes=cooldown_minutes)
        ).isoformat()

        _append_trade_row(
            trade_log_path,
            {
                "closed_at": closed_at.isoformat(),
                "opened_at": pos.opened_at,
                "token_id": pos.token_id,
                "condition_id": pos.condition_id,
                "market_question": pos.market_question,
                "outcome": pos.outcome,
                "entry_price": pos.entry_price,
                "exit_price": exit_price,
                "size_tokens": pos.size_tokens,
                "cost_usd": pos.cost_usd,
                "proceeds_usd": proceeds_usd,
                "pnl_usd": pnl_usd,
                "pnl_pct": pnl_pct,
                "reason": reason,
                "entry_reason": pos.entry_reason,
                "exit_reason_detail": exit_reason_detail,
            },
        )
        logger.info(
            "CLOSE %s (%s) @ %.4f reason=%s (%s) pnl=$%.2f (%.1f%%)",
            pos.outcome,
            pos.market_question[:60],
            exit_price,
            reason,
            exit_reason_detail,
            pnl_usd,
            pnl_pct * 100,
        )
        return pnl_usd

    def to_dict(self) -> dict:
        return {
            "cash": self.cash,
            "realized_pnl": self.realized_pnl,
            "positions": {tid: asdict(p) for tid, p in self.positions.items()},
            "cooldown_until": self.cooldown_until,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Portfolio":
        positions = {
            tid: Position(**p) for tid, p in data.get("positions", {}).items()
        }
        return cls(
            cash=data["cash"],
            positions=positions,
            cooldown_until=data.get("cooldown_until", {}),
            realized_pnl=data.get("realized_pnl", 0.0),
        )

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load_or_create(cls, path: Path, starting_cash: float) -> "Portfolio":
        if path.exists():
            return cls.from_dict(json.loads(path.read_text()))
        return cls(cash=starting_cash)


def _append_trade_row(path: Path, row: dict) -> None:
    is_new = not path.exists()
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_LOG_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def read_recent_trades(path: Path, limit: int = 20) -> list[dict]:
    """Most-recent-first closed trades for the control panel's activity log."""
    if not path.exists():
        return []
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    rows.reverse()
    return rows[:limit]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
