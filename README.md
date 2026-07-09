# polybot

Autonomous **momentum** trading bot for [Polymarket](https://polymarket.com), in
**paper-trading mode by default** — no funds or real orders are touched unless
you explicitly opt into live mode.

## Where this came from

Two reference repos shaped the design:

- **[`warproxxx/poly_data`](https://github.com/warproxxx/poly_data)** (`update_utils/`) —
  a data pipeline that pulls the full CLOB market catalog and on-chain
  `OrderFilled` events into labeled trade data. It's not a trading bot itself,
  but it demonstrates the operational patterns this project reuses:
  paginated/cursor-based API fetching with a thread pool
  (`update_markets.py` → `polybot/api/gamma.py` + `polybot/scanner.py`),
  exponential-backoff retries (`_fetch_page` → `polybot/api/http.py`), and
  resumable, append-only state (`process_live.py`'s last-processed marker →
  `polybot/portfolio.py`'s JSON snapshot + trades CSV).
- **[`Polymarket/polymarket-cli`](https://github.com/Polymarket/polymarket-cli)** —
  a Rust CLI for manual trading. It defines the auth/execution model this bot's
  live executor follows: a private key plus a signature type
  (`proxy` / `eoa` / `gnosis-safe`), separate from ERC-20/ERC-1155 contract
  approvals (which the CLI's `approve` command handles and which **this bot
  does not manage itself** — do that once via the CLI or the Polymarket UI
  before ever enabling live mode).

Neither repo contains a trading strategy — the momentum logic, risk
management, and paper-trading simulation here are new.

## How it works

Every `POLYBOT_POLL_INTERVAL_SECONDS` (default 60s), one cycle runs:

1. **Manage open positions.** For each held token, fetch the current order
   book mid-price and check take-profit / stop-loss / max-holding-time exit
   rules (`polybot/risk.py`). Exits are simulated (or, in live mode, sent as
   real sell orders) before anything new is opened.
2. **Scan for entries**, only if there's room under the risk budget:
   - Discover active markets from the Gamma API, filtered by 24h volume and
     liquidity thresholds (`polybot/api/gamma.py`).
   - For every outcome token (YES *and* NO are scanned independently — a
     falling YES price shows up as rising NO momentum, so no short-selling
     is needed), fetch recent price history and compute momentum: the
     relative price change from the oldest to the newest point in the
     lookback window (`polybot/strategy/momentum.py`).
   - Tokens whose momentum clears `POLYBOT_MOMENTUM_THRESHOLD`, and whose
     price isn't already near 0 or 1 (no edge left near resolution), become
     candidate signals, ranked by strength.
   - Signals are opened in order, sized by `RiskManager` (percent-of-equity,
     capped by a max USD per position, total position count, and total
     exposure as a percent of equity), until the budget runs out.
3. Portfolio state is saved to `data/portfolio.json` after every cycle;
   closed trades are appended to `data/trades.csv`.

## Quickstart (paper trading — no keys needed)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # defaults are fine for paper mode
python -m polybot.bot         # or: polybot
```

Watch `data/polybot.log` and `data/trades.csv` for activity. Stop any time
with Ctrl+C — state is saved on every cycle and on shutdown, so restarting
resumes from the same portfolio.

Run tests with:

```bash
pip install -e ".[dev]"
pytest
```

> **Network note:** market discovery and pricing call Polymarket's public
> Gamma (`gamma-api.polymarket.com`) and CLOB (`clob.polymarket.com`) REST
> APIs directly. This was built and unit-tested in a sandboxed environment
> without outbound access to those hosts, so the API request/response
> shapes are based on Polymarket's documented endpoints and the `poly_data`
> reference code rather than a live call from this environment — run it
> somewhere with normal internet access first and check `data/polybot.log`
> before trusting it unattended.

## Configuration

All settings are environment variables (see `.env.example` for the full
list with defaults), covering: poll interval, market filters (min
volume/liquidity, price bounds), the momentum lookback window and
threshold, exit rules (take-profit/stop-loss/max-holding-time/cooldown),
and risk sizing (max concurrent positions, max USD per position, percent of
equity per trade, max total exposure).

## Going live

Live mode places real orders with real funds via
[`py-clob-client`](https://github.com/Polymarket/py-clob-client)
(`polybot/execution/live.py`). It is far less exercised than the paper
path. Before enabling it:

1. Fund the wallet and run the on-chain ERC-20/ERC-1155 approvals **once**,
   the same way `polymarket-cli`'s `approve set` does — this bot doesn't do
   that for you.
2. Install the extra: `pip install -e ".[live]"`.
3. Set `POLYBOT_MODE=live`, `POLYBOT_PRIVATE_KEY`, `POLYBOT_SIGNATURE_TYPE`
   (`proxy` if you trade through the standard Polymarket UI wallet, `eoa`
   for a raw wallet, `gnosis-safe` for a Safe), and `POLYBOT_FUNDER_ADDRESS`
   if your signature type needs one.
4. Start with a tiny `POLYBOT_MAX_POSITION_USD` and watch the first several
   fills manually before trusting it unattended.

This is real-money automation — review the strategy and risk limits
yourself before turning it on, and treat the live executor as a starting
point to test carefully, not a finished product.

## Project layout

```
polybot/
  config.py            settings (env-var driven)
  models.py             Market / PricePoint / OrderBook / Signal dataclasses
  api/
    http.py             retrying HTTP GET with thread-local sessions
    gamma.py             market discovery via Gamma API
    clob.py              order book + price history via CLOB API
  strategy/momentum.py  momentum signal calculation
  scanner.py            concurrent market/token scanning
  portfolio.py          paper portfolio, JSON state, CSV trade log
  risk.py               position sizing + exit rules
  execution/
    base.py             executor interface
    paper.py             simulated fills (default)
    live.py               real orders via py-clob-client
    factory.py            picks paper vs live from settings
  bot.py                main loop
tests/                  unit tests for momentum, risk, portfolio
```
