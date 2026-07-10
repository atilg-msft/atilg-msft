from polybot.portfolio import TRADE_LOG_FIELDS, Portfolio, read_recent_trades, utcnow


def test_open_and_close_position_roundtrip(tmp_path):
    portfolio = Portfolio(cash=1000)
    now = utcnow()

    portfolio.open_position(
        token_id="tok-yes",
        condition_id="0xabc",
        market_question="Will it happen?",
        outcome="Yes",
        fill_price=0.40,
        cost_usd=20.0,
        opened_at=now,
        entry_reason="Momentum +12.0% over 15min (threshold 8.0%)",
    )
    assert portfolio.cash == 980.0
    assert "tok-yes" in portfolio.positions
    assert portfolio.positions["tok-yes"].size_tokens == 50.0
    assert portfolio.positions["tok-yes"].entry_reason.startswith("Momentum")

    trade_log_path = tmp_path / "trades.csv"
    pnl = portfolio.close_position(
        token_id="tok-yes",
        exit_price=0.50,
        closed_at=now,
        reason="take_profit",
        cooldown_minutes=30,
        trade_log_path=trade_log_path,
        exit_reason_detail="Price 0.4000 -> 0.5000 (+25.0%), threshold +15%",
    )

    assert pnl == 5.0  # 50 tokens * (0.50 - 0.40)
    assert portfolio.cash == 1005.0
    assert "tok-yes" not in portfolio.positions
    assert portfolio.realized_pnl == 5.0
    assert portfolio.is_in_cooldown("tok-yes", now)
    assert trade_log_path.exists()
    assert "take_profit" in trade_log_path.read_text()

    trades = read_recent_trades(trade_log_path)
    assert len(trades) == 1
    assert trades[0]["entry_reason"].startswith("Momentum")
    assert "threshold +15%" in trades[0]["exit_reason_detail"]


def test_read_recent_trades_missing_file_returns_empty(tmp_path):
    assert read_recent_trades(tmp_path / "no-such-file.csv") == []


def test_read_recent_trades_most_recent_first(tmp_path):
    portfolio = Portfolio(cash=1000)
    trade_log_path = tmp_path / "trades.csv"
    for i, token_id in enumerate(["tok-a", "tok-b"]):
        now = utcnow()
        portfolio.open_position(
            token_id=token_id,
            condition_id="0xabc",
            market_question=f"Q{i}",
            outcome="Yes",
            fill_price=0.40,
            cost_usd=10.0,
            opened_at=now,
        )
        portfolio.close_position(
            token_id=token_id,
            exit_price=0.45,
            closed_at=now,
            reason="take_profit",
            cooldown_minutes=1,
            trade_log_path=trade_log_path,
        )

    trades = read_recent_trades(trade_log_path)
    assert [t["market_question"] for t in trades] == ["Q1", "Q0"]


def test_appending_migrates_a_file_written_with_an_older_schema(tmp_path):
    """Simulates trades.csv from before entry_reason/exit_reason_detail
    existed: an older header, but a data row that (like production, once
    the code moved on) already carries the newer columns' values past the
    end of what the header names -- exactly what csv.DictWriter produces
    when you keep writing to a file whose header predates a fieldname
    change. Appending a new trade should heal the whole file in place."""
    trade_log_path = tmp_path / "trades.csv"
    old_header = [c for c in TRADE_LOG_FIELDS if c not in ("entry_reason", "exit_reason_detail")]
    old_row = [
        "2026-01-01T00:00:00+00:00", "2025-12-31T00:00:00+00:00", "tok-old",
        "0xold", "Old market?", "Yes", "0.4", "0.5", "50", "20.0", "25.0", "5.0",
        "0.25", "take_profit", "Old entry reason", "Old exit detail",
    ]
    with trade_log_path.open("w", newline="") as f:
        f.write(",".join(old_header) + "\n")
        f.write(",".join(old_row) + "\n")

    portfolio = Portfolio(cash=1000)
    now = utcnow()
    portfolio.open_position(
        token_id="tok-new",
        condition_id="0xnew",
        market_question="New market?",
        outcome="Yes",
        fill_price=0.4,
        cost_usd=10.0,
        opened_at=now,
        entry_reason="New entry reason",
    )
    portfolio.close_position(
        token_id="tok-new",
        exit_price=0.45,
        closed_at=now,
        reason="take_profit",
        cooldown_minutes=1,
        trade_log_path=trade_log_path,
        exit_reason_detail="New exit detail",
    )

    trades = read_recent_trades(trade_log_path)
    assert len(trades) == 2
    assert trades[0]["token_id"] == "tok-new"
    assert trades[0]["entry_reason"] == "New entry reason"
    assert trades[0]["exit_reason_detail"] == "New exit detail"
    # The old row's overflow values were recovered into the right columns,
    # not dropped or left keyed under None.
    assert trades[1]["token_id"] == "tok-old"
    assert trades[1]["entry_reason"] == "Old entry reason"
    assert trades[1]["exit_reason_detail"] == "Old exit detail"
    assert None not in trades[1]
    assert trade_log_path.read_text().splitlines()[0] == ",".join(TRADE_LOG_FIELDS)


def test_json_roundtrip(tmp_path):
    portfolio = Portfolio(cash=1000)
    now = utcnow()
    portfolio.open_position(
        token_id="tok-yes",
        condition_id="0xabc",
        market_question="Will it happen?",
        outcome="Yes",
        fill_price=0.40,
        cost_usd=20.0,
        opened_at=now,
    )

    path = tmp_path / "portfolio.json"
    portfolio.save(path)
    loaded = Portfolio.load_or_create(path, starting_cash=999)

    assert loaded.cash == portfolio.cash
    assert loaded.positions["tok-yes"].entry_price == 0.40


def test_equity_uses_mark_prices_with_entry_fallback():
    portfolio = Portfolio(cash=1000)
    now = utcnow()
    portfolio.open_position(
        token_id="tok-yes",
        condition_id="0xabc",
        market_question="Q",
        outcome="Yes",
        fill_price=0.40,
        cost_usd=20.0,
        opened_at=now,
    )
    # No mark price supplied -> falls back to entry_price (cash=980 + 50*0.40).
    assert portfolio.equity({}) == 980 + 50 * 0.40
    # Mark price supplied -> reflects unrealized P&L.
    assert portfolio.equity({"tok-yes": 0.50}) == 980 + 50 * 0.50
