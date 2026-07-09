from polybot.portfolio import Portfolio, utcnow


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
    )
    assert portfolio.cash == 980.0
    assert "tok-yes" in portfolio.positions
    assert portfolio.positions["tok-yes"].size_tokens == 50.0

    trade_log_path = tmp_path / "trades.csv"
    pnl = portfolio.close_position(
        token_id="tok-yes",
        exit_price=0.50,
        closed_at=now,
        reason="take_profit",
        cooldown_minutes=30,
        trade_log_path=trade_log_path,
    )

    assert pnl == 5.0  # 50 tokens * (0.50 - 0.40)
    assert portfolio.cash == 1005.0
    assert "tok-yes" not in portfolio.positions
    assert portfolio.realized_pnl == 5.0
    assert portfolio.is_in_cooldown("tok-yes", now)
    assert trade_log_path.exists()
    assert "take_profit" in trade_log_path.read_text()


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
