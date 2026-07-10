from polybot.portfolio import Portfolio, read_recent_trades, utcnow


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
