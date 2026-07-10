from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, replace
from enum import Enum
from typing import Callable

from .bot import run_cycle
from .config import SIGNAL_FILTER_CHOICES, STRATEGY_CHOICES, Settings
from .execution.factory import get_executor
from .portfolio import Portfolio, read_recent_trades, utcnow
from .risk import RiskManager
from .strategy.smart_money import SmartMoneyTracker

logger = logging.getLogger(__name__)

JOIN_TIMEOUT_SECONDS = 120


class BotState(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    LIQUIDATING = "liquidating"


class BotService:
    """Wraps the trading loop so it can be started, stopped, and force-flattened
    from outside (e.g. a web control panel), while staying safe for a single
    long-lived container process:

    - A background thread runs `run_cycle` on `poll_interval_seconds`, guarded
      by `threading.Event.wait` so `stop()` interrupts the sleep immediately
      instead of waiting out the full interval.
    - `self.lock` serializes access to the shared portfolio between the loop
      thread and whichever thread calls the control API (start/stop/liquidate/
      status), so a status read never observes a half-updated portfolio.
    - The desired run state is persisted to `control_state.json` so a
      container restart resumes what the operator last asked for, rather than
      silently starting to trade (or silently staying idle).
    """

    def __init__(
        self,
        settings: Settings,
        settings_provider: Callable[[], Settings] | None = None,
    ) -> None:
        self.settings = settings
        self.settings_provider = settings_provider

        self.portfolio_path = settings.data_dir / "portfolio.json"
        self.trade_log_path = settings.data_dir / "trades.csv"
        self.control_state_path = settings.data_dir / "control_state.json"
        self.strategy_override_path = settings.data_dir / "strategy_override.json"

        self.lock = threading.RLock()
        self.portfolio = Portfolio.load_or_create(self.portfolio_path, settings.starting_cash)

        # A strategy/signal_filter chosen from the control panel is persisted
        # here and takes precedence over whatever env vars/App Configuration
        # say, so a container restart resumes the operator's explicit choice
        # instead of reverting to the deployed default.
        self._manual_overrides: dict = self._load_strategy_override()
        if self._manual_overrides:
            self.settings = replace(self.settings, **self._manual_overrides)

        self.risk = RiskManager(self.settings)
        self.executor = get_executor(settings)
        # Always constructed (cheap, inert unless settings.signal_filter ==
        # "smart_money") so flipping it on via config or the control panel
        # works without a restart.
        self.smart_money = SmartMoneyTracker()

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = BotState.STOPPED
        self.last_cycle_at = None
        self.last_error: str | None = None
        self.last_equity: float = self.portfolio.equity({})

        if self._load_desired_state() == BotState.RUNNING:
            self.start(persist=False)

    # -- desired-state persistence -----------------------------------------

    def _load_desired_state(self) -> BotState:
        if not self.control_state_path.exists():
            return BotState.STOPPED
        try:
            data = json.loads(self.control_state_path.read_text())
            return BotState(data.get("state", "stopped"))
        except Exception:
            logger.warning("could not read control_state.json, defaulting to stopped")
            return BotState.STOPPED

    def _save_desired_state(self, state: BotState) -> None:
        self.control_state_path.write_text(json.dumps({"state": state.value}))

    def _load_strategy_override(self) -> dict:
        if not self.strategy_override_path.exists():
            return {}
        try:
            data = json.loads(self.strategy_override_path.read_text())
        except Exception:
            logger.warning("could not read strategy_override.json, ignoring")
            return {}
        return {k: v for k, v in data.items() if k in ("strategy", "signal_filter")}

    def _save_strategy_override(self) -> None:
        self.strategy_override_path.write_text(json.dumps(self._manual_overrides))

    # -- strategy selection -----------------------------------------------------

    def get_strategy_info(self) -> dict:
        with self.lock:
            return {
                "strategy": self.settings.strategy,
                "signal_filter": self.settings.signal_filter,
                "strategy_options": STRATEGY_CHOICES,
                "signal_filter_options": SIGNAL_FILTER_CHOICES,
                "overridden": bool(self._manual_overrides),
            }

    def set_strategy(self, strategy: str | None, signal_filter: str | None) -> dict:
        if strategy is not None and strategy not in STRATEGY_CHOICES:
            raise ValueError(f"Invalid strategy: {strategy!r} (choices: {STRATEGY_CHOICES})")
        if signal_filter is not None and signal_filter not in SIGNAL_FILTER_CHOICES:
            raise ValueError(
                f"Invalid signal_filter: {signal_filter!r} (choices: {SIGNAL_FILTER_CHOICES})"
            )
        with self.lock:
            if strategy is not None:
                self._manual_overrides["strategy"] = strategy
            if signal_filter is not None:
                self._manual_overrides["signal_filter"] = signal_filter
            self._save_strategy_override()
            self.settings = replace(self.settings, **self._manual_overrides)
            self.risk = RiskManager(self.settings)
            logger.info(
                "strategy selection updated: strategy=%s signal_filter=%s",
                self.settings.strategy,
                self.settings.signal_filter,
            )
            return self.get_strategy_info()

    # -- control API ----------------------------------------------------------

    def start(self, persist: bool = True) -> None:
        with self.lock:
            if self.state == BotState.RUNNING:
                return
            self._stop_event.clear()
            self.state = BotState.RUNNING
            self._thread = threading.Thread(target=self._loop, daemon=True, name="polybot-loop")
            self._thread.start()
            if persist:
                self._save_desired_state(BotState.RUNNING)
        logger.info("bot started")

    def stop(self, persist: bool = True) -> None:
        with self.lock:
            self._stop_event.set()
            if self.state == BotState.RUNNING:
                self.state = BotState.STOPPED
            thread = self._thread
            if persist:
                self._save_desired_state(BotState.STOPPED)
        # Join outside the lock: the loop thread needs the lock to finish its
        # current cycle and notice the stop signal.
        if thread is not None and thread.is_alive():
            thread.join(timeout=JOIN_TIMEOUT_SECONDS)
            if thread.is_alive():
                logger.warning("bot loop did not stop within %ss (likely mid-cycle)", JOIN_TIMEOUT_SECONDS)
        logger.info("bot stopped")

    def liquidate(self) -> dict:
        """Force-close every open position at market and halt the loop.

        Used as an emergency flatten button: it does not resume trading
        afterwards, since re-entering positions right after a manual flatten
        is almost never what the operator wants.
        """
        with self.lock:
            self.state = BotState.LIQUIDATING
        self.stop(persist=False)

        closed = []
        now = utcnow()
        with self.lock:
            for token_id, position in list(self.portfolio.positions.items()):
                fill_price = self.executor.sell(token_id, position.size_tokens, position.entry_price)
                if fill_price is None:
                    logger.warning("liquidation sell failed for %s; position left open", token_id)
                    continue
                pnl = self.portfolio.close_position(
                    token_id,
                    fill_price,
                    now,
                    "manual_liquidation",
                    self.settings.cooldown_minutes,
                    self.trade_log_path,
                    exit_reason_detail="Requested from the control panel",
                )
                closed.append({"token_id": token_id, "exit_price": fill_price, "pnl_usd": pnl})
            self.portfolio.save(self.portfolio_path)
            self.last_equity = self.portfolio.equity({})
            self.state = BotState.STOPPED
            self._save_desired_state(BotState.STOPPED)

        logger.warning("liquidation complete: closed %d position(s)", len(closed))
        return {"closed_positions": closed, "cash": self.portfolio.cash}

    def status(self) -> dict:
        with self.lock:
            return {
                "state": self.state.value,
                "mode": self.settings.mode,
                "strategy": self.settings.strategy,
                "signal_filter": self.settings.signal_filter,
                "cash": self.portfolio.cash,
                "equity": self.last_equity,
                "realized_pnl": self.portfolio.realized_pnl,
                "exposure_usd": self.portfolio.exposure_usd(),
                "open_positions": [asdict(p) for p in self.portfolio.positions.values()],
                "last_cycle_at": self.last_cycle_at.isoformat() if self.last_cycle_at else None,
                "last_error": self.last_error,
                "poll_interval_seconds": self.settings.poll_interval_seconds,
            }

    def recent_trades(self, limit: int = 20) -> list[dict]:
        return read_recent_trades(self.trade_log_path, limit=limit)

    # -- background loop ------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self.settings_provider is not None:
                    refreshed = self.settings_provider()
                    with self.lock:
                        if self._manual_overrides:
                            refreshed = replace(refreshed, **self._manual_overrides)
                        self.settings = refreshed
                        self.risk = RiskManager(refreshed)
                with self.lock:
                    equity = run_cycle(
                        self.settings,
                        self.portfolio,
                        self.risk,
                        self.executor,
                        self.trade_log_path,
                        smart_money=self.smart_money,
                    )
                    self.portfolio.save(self.portfolio_path)
                    self.last_equity = equity
                    self.last_cycle_at = utcnow()
                    self.last_error = None
            except Exception as exc:
                logger.exception("cycle failed")
                self.last_error = str(exc)
            self._stop_event.wait(self.settings.poll_interval_seconds)
