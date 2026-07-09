from datetime import datetime, timedelta, timezone

from polybot.config import Settings
from polybot.portfolio import Portfolio, Position
from polybot.risk import RiskManager


def make_settings(tmp_path, **overrides) -> Settings:
    return Settings(data_dir=tmp_path, **overrides)


def make_position(entry_price=0.40, opened_minutes_ago=0) -> Position:
    opened_at = datetime.now(timezone.utc) - timedelta(minutes=opened_minutes_ago)
    return Position(
        token_id="tok-yes",
        condition_id="0xabc",
        market_question="Will it happen?",
        outcome="Yes",
        entry_price=entry_price,
        size_tokens=25.0,
        cost_usd=10.0,
        opened_at=opened_at.isoformat(),
    )


def test_position_size_respects_max_position_usd(tmp_path):
    settings = make_settings(tmp_path, max_position_usd=20, position_pct_of_equity=1.0)
    risk = RiskManager(settings)
    portfolio = Portfolio(cash=1000)
    assert risk.position_size_usd(portfolio, equity=1000) == 20


def test_position_size_respects_available_cash(tmp_path):
    settings = make_settings(tmp_path, max_position_usd=100, position_pct_of_equity=1.0)
    risk = RiskManager(settings)
    portfolio = Portfolio(cash=5)
    assert risk.position_size_usd(portfolio, equity=1000) == 5


def test_can_open_new_position_blocked_by_max_positions(tmp_path):
    settings = make_settings(tmp_path, max_concurrent_positions=1, max_total_exposure_pct=1.0)
    risk = RiskManager(settings)
    portfolio = Portfolio(cash=990, positions={"tok-yes": make_position()})
    assert risk.can_open_new_position(portfolio, equity=1000) is False


def test_can_open_new_position_blocked_by_exposure_budget(tmp_path):
    settings = make_settings(tmp_path, max_concurrent_positions=10, max_total_exposure_pct=0.005)
    risk = RiskManager(settings)
    portfolio = Portfolio(cash=990, positions={"tok-yes": make_position()})
    assert risk.can_open_new_position(portfolio, equity=1000) is False


def test_check_exit_take_profit(tmp_path):
    settings = make_settings(tmp_path, take_profit_pct=0.15, stop_loss_pct=0.10)
    risk = RiskManager(settings)
    position = make_position(entry_price=0.40)
    assert risk.check_exit(position, mark_price=0.47, now=datetime.now(timezone.utc)) == "take_profit"


def test_check_exit_stop_loss(tmp_path):
    settings = make_settings(tmp_path, take_profit_pct=0.15, stop_loss_pct=0.10)
    risk = RiskManager(settings)
    position = make_position(entry_price=0.40)
    assert risk.check_exit(position, mark_price=0.35, now=datetime.now(timezone.utc)) == "stop_loss"


def test_check_exit_max_holding_time(tmp_path):
    settings = make_settings(
        tmp_path, take_profit_pct=0.50, stop_loss_pct=0.50, max_holding_minutes=60
    )
    risk = RiskManager(settings)
    position = make_position(entry_price=0.40, opened_minutes_ago=120)
    assert risk.check_exit(position, mark_price=0.41, now=datetime.now(timezone.utc)) == "max_holding_time"


def test_check_exit_none_when_within_bounds(tmp_path):
    settings = make_settings(
        tmp_path, take_profit_pct=0.15, stop_loss_pct=0.10, max_holding_minutes=240
    )
    risk = RiskManager(settings)
    position = make_position(entry_price=0.40, opened_minutes_ago=5)
    assert risk.check_exit(position, mark_price=0.42, now=datetime.now(timezone.utc)) is None
