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
        traded_markets: dict[str, str] | None = None,
    ) -> None:
        self.cash = cash
        self.positions: dict[str, Position] = positions or {}
        self.cooldown_until: dict[str, str] = cooldown_until or {}
        self.realized_pnl = realized_pnl
        # condition_id -> the one token_id (outcome) ever opened for that
        # market. Permanent, not time-limited like cooldown_until: once a
        # market has been traded on one side, the opposite side is locked
        # out for good -- even after the original position closes -- so the
        # bot never ends up paying entry/exit costs on both legs of what's
        # roughly a self-cancelling YES+NO≈1 hedge (see is_market_locked_out).
        self.traded_markets: dict[str, str] = traded_markets or {}

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

    def is_market_locked_out(self, condition_id: str, token_id: str) -> bool:
        """True if some *other* outcome of this market was already traded --
        permanently blocking entry into this token_id regardless of whether
        that other position is still open or has long since closed."""
        locked_token_id = self.traded_markets.get(condition_id)
        return locked_token_id is not None and locked_token_id != token_id

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
        self.traded_markets.setdefault(condition_id, token_id)
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
            "traded_markets": self.traded_markets,
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
            traded_markets=data.get("traded_markets", {}),
        )

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load_or_create(
        cls, path: Path, starting_cash: float, trade_log_path: Path | None = None
    ) -> "Portfolio":
        portfolio = (
            cls.from_dict(json.loads(path.read_text())) if path.exists() else cls(cash=starting_cash)
        )
        # Backfill traded_markets from trade history so the one-direction-
        # per-market lock also covers markets whose opposite side was
        # already traded and closed before this lock existed -- not only
        # positions opened from now on.
        for condition_id, token_id in _earliest_outcome_per_market(
            trade_log_path, portfolio.positions
        ).items():
            portfolio.traded_markets.setdefault(condition_id, token_id)
        return portfolio


def _earliest_outcome_per_market(
    trade_log_path: Path | None, open_positions: dict[str, Position]
) -> dict[str, str]:
    """Which token_id was opened first, per condition_id, across both closed
    trades (trades.csv) and any currently open positions -- combined and
    ordered by opened_at so a position still open today doesn't wrongly
    "win" the lock over an earlier, already-closed trade on the other side."""
    entries: list[tuple[str, str, str]] = []  # (opened_at, condition_id, token_id)
    if trade_log_path is not None and trade_log_path.exists():
        with trade_log_path.open(newline="") as f:
            for row in csv.DictReader(f):
                opened_at = row.get("opened_at")
                condition_id = row.get("condition_id")
                token_id = row.get("token_id")
                if opened_at and condition_id and token_id:
                    entries.append((opened_at, condition_id, token_id))
    for pos in open_positions.values():
        entries.append((pos.opened_at, pos.condition_id, pos.token_id))

    entries.sort(key=lambda e: e[0])
    first_outcome: dict[str, str] = {}
    for _, condition_id, token_id in entries:
        first_outcome.setdefault(condition_id, token_id)
    return first_outcome


def _migrate_trade_log_if_needed(path: Path) -> None:
    """Bring an existing trades.csv up to the current TRADE_LOG_FIELDS.

    Columns added to TRADE_LOG_FIELDS after a file already existed (e.g.
    entry_reason/exit_reason_detail) don't retroactively appear in that
    file's header row, but new rows are still written with the extra
    values -- csv.DictWriter writes whatever fieldnames it's given
    regardless of what the file's own header says. Reading such a file
    back with DictReader then dumps the un-headed extra values into a
    `None`-keyed overflow list (its `restkey`) instead of the right column.
    Rewriting the file once, recovering that overflow into the columns it
    was always meant to be, is simpler and safer than trying to keep every
    historical header variant readable forever.
    """
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        original_fieldnames = reader.fieldnames or []
        if original_fieldnames == TRADE_LOG_FIELDS:
            return
        rows = list(reader)

    missing_columns = [c for c in TRADE_LOG_FIELDS if c not in original_fieldnames]
    migrated_rows = []
    for row in rows:
        overflow = row.pop(None, None) or []
        for column, value in zip(missing_columns, overflow):
            row[column] = value
        migrated_rows.append({column: row.get(column, "") for column in TRADE_LOG_FIELDS})

    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_LOG_FIELDS)
        writer.writeheader()
        writer.writerows(migrated_rows)
    logger.info("migrated %s to current trade-log schema (%d rows)", path, len(migrated_rows))


def _append_trade_row(path: Path, row: dict) -> None:
    is_new = not path.exists()
    if not is_new:
        _migrate_trade_log_if_needed(path)
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
