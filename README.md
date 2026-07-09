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
   - If `POLYBOT_SMART_WALLET_ENABLED=true`, candidates are then filtered
     down to ones a tracked high-PnL wallet also just bought (see
     [Smart-money confirmation filter](#smart-money-confirmation-filter)).
   - Signals are opened in order, sized by `RiskManager` (percent-of-equity,
     capped by a max USD per position, total position count, and total
     exposure as a percent of equity), until the budget runs out.
3. Portfolio state is saved to `data/portfolio.json` after every cycle;
   closed trades are appended to `data/trades.csv`.

There are two ways to run it:

- **`polybot`** (`polybot/bot.py`) — a plain headless loop, Ctrl+C to stop.
  Good for local experimentation.
- **`polybot-server`** (`polybot/main.py`) — the same loop wrapped in a
  `BotService` (`polybot/service.py`) with a FastAPI control panel
  (start/stop/liquidate) on top, plus optional Azure Key Vault / App
  Configuration integration. This is what the Docker image runs, and what
  you'd deploy to Azure.

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

To try the web control panel locally instead:

```bash
pip install -e ".[dev,web]"
python -m polybot.main         # or: polybot-server
# open http://localhost:8000
```

Without `AZURE_KEY_VAULT_URL` / `AZURE_APPCONFIG_ENDPOINT` set, this just
reads from `.env`/the process environment like the plain `polybot` command —
Azure is entirely optional for local use.

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
equity per trade, max total exposure). Deployed on Azure, these same
variables are what Key Vault and App Configuration end up populating —
see [Deploying to Azure](#deploying-to-azure).

## Web control panel

`polybot-server` exposes both a JSON API and a small page at `/`:

| Button / endpoint | Effect |
| --- | --- |
| **Başlat** — `POST /api/start` | Starts the loop if it isn't already running. |
| **Durdur** — `POST /api/stop` | Stops opening new positions after the current cycle finishes. Existing open positions are left alone (still monitored for take-profit/stop-loss if you start it again). |
| **Likidasyona Dön** — `POST /api/liquidate` | Emergency flatten: sells every open position at the current market price immediately, then stops the loop. Doesn't resume trading afterward — that's a deliberate choice, since auto-reopening right after a manual flatten is rarely what you want. |
| `GET /api/status` | Current state, mode, cash, equity, realized P&L, exposure, open positions, last cycle time, last error. Polled by the page every 3s. |

The desired run state (`running`/`stopped`) is persisted to
`data/control_state.json`, so a container restart resumes whatever you last
asked for rather than silently starting to trade or silently staying idle.
Because state lives in-process (the portfolio, the loop thread), this is a
**singleton service** — don't scale it beyond one replica.

## Smart-money confirmation filter

Set `POLYBOT_SMART_WALLET_ENABLED=true` to require momentum candidates to
also line up with what Polymarket's own highest-PnL traders are doing
(`polybot/strategy/smart_money.py`, `polybot/api/data_api.py`):

1. Every `POLYBOT_SMART_WALLET_REFRESH_MINUTES` (default 6h), it pulls the
   top `POLYBOT_SMART_WALLET_COUNT` wallets from Polymarket's public
   leaderboard, ranked by PnL over `POLYBOT_SMART_WALLET_PERIOD`
   (`day`/`week`/`month`), plus anything listed in
   `POLYBOT_SMART_WALLET_OVERRIDES` (comma-separated addresses you add
   yourself — useful if you've found specific wallets worth following, or
   as a fallback if the leaderboard call ever breaks).
2. Every cycle, it pulls each tracked wallet's recent trade activity and
   keeps the latest BUY per token within `POLYBOT_SMART_WALLET_LOOKBACK_MINUTES`.
3. A momentum candidate only survives if a tracked wallet bought that exact
   token within that window. If the confirmation check itself fails (API
   unreachable), **all candidates are dropped for that cycle** — fail
   closed, since the whole point is not trading without confirmation.

This exists because raw PnL and raw volume are both misleading on their
own: a wallet's all-time profit can come from one lucky long-shot bet
rather than skill, and Polymarket's highest-*volume* wallets are often
market makers with no directional view at all. Requiring momentum *and* a
tracked wallet buying the same outcome cuts down on both false-positive
types, at the cost of fewer trades.

> **Verify before relying on this.** `data-api.polymarket.com`'s
> leaderboard/activity endpoints and field names were not reachable from
> the sandbox this was built in (see the network note above), so
> `get_leaderboard`/`get_wallet_activity` in `polybot/api/data_api.py` are
> based on `polymarket-cli`'s documented `data leaderboard`/`data activity`
> commands rather than a live-verified response shape. Parsing is
> defensive (it tries several likely field names and skips what it can't
> parse) and logs the real field names it sees the first time it gets a
> response (`data-api leaderboard sample fields: [...]` in the log) — check
> that log line against `_WALLET_KEYS`/`_TOKEN_KEYS`/etc. at the top of
> `data_api.py` after your first run, and adjust if they don't match.
> Until you've confirmed it's actually returning wallets, this stays
> disabled (`POLYBOT_SMART_WALLET_ENABLED=false` is the default) so it
> can't silently zero out every trade.

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

## Deploying to Azure

The bot ships as a container for **Azure Container Apps**, with:

- **Azure Key Vault** holding the two real secrets (`polybot-private-key`,
  `polybot-funder-address`), read at startup via the container's managed
  identity (`polybot/cloud/keyvault.py`, `DefaultAzureCredential` — no
  connection string or key ever touches the app config).
- **Azure App Configuration** holding every strategy/risk/scan parameter
  (momentum threshold, lookback window, position sizing, exit rules, poll
  interval, ...) as plain `POLYBOT_*` key-values, re-read every 5 minutes
  by default (`polybot/cloud/appconfig.py`) so you can retune the bot
  without a redeploy. `POLYBOT_MODE`, `POLYBOT_PRIVATE_KEY`,
  `POLYBOT_SIGNATURE_TYPE`, `POLYBOT_FUNDER_ADDRESS` and the API endpoints
  are deliberately excluded from that refresh — flipping paper→live or
  changing wallets always requires a real deploy, never just a config edit.
- A single-replica **Container App** (`minReplicas`/`maxReplicas` pinned to
  1 — the portfolio and control state are in-process/on-disk, not
  horizontally scalable) with an **Azure Files** volume mounted at
  `/app/data` so `portfolio.json`, `trades.csv`, and `control_state.json`
  survive restarts and redeploys.
- An **Azure Container Registry** the app pulls from.

All of it is in `infra/main.bicep`. To deploy:

```bash
az login
./infra/deploy.sh polybot-rg westeurope        # resource group, region
```

`infra/deploy.sh` creates the resource group, runs the Bicep deployment,
builds the image remotely with `az acr build` (no local Docker needed —
handy since this was developed in a sandbox without Docker access), and
points the Container App at the freshly built image. It prints the
control-panel URL, Key Vault name, and App Configuration name at the end.

Then, for live trading:

```bash
az keyvault secret set --vault-name <keyVaultName> --name polybot-private-key --value 0x...
az keyvault secret set --vault-name <keyVaultName> --name polybot-funder-address --value 0x...
az deployment group create -g polybot-rg -f infra/main.bicep -p polybotMode=live
```

And to retune the strategy at any time, no redeploy needed:

```bash
az appconfig kv set --name <appConfigName> --key POLYBOT_MOMENTUM_THRESHOLD --value 0.1 --auth-mode login --yes
```

> **This infra was not deployed or validated against a real Azure
> subscription from this sandbox** (no `az`/`bicep` CLI or Docker daemon
> available here — outbound access is restricted to a small allowlist that
> doesn't include Azure or Docker Hub). The Bicep was written and reviewed
> carefully against the documented resource schemas, but run
> `az deployment group validate` (or `--what-if`) before applying it for
> real, and treat the first deploy as a dry run you watch closely.

## Project layout

```
polybot/
  config.py             settings (env-var driven)
  models.py              Market / PricePoint / OrderBook / Signal dataclasses
  api/
    http.py              retrying HTTP GET with thread-local sessions
    gamma.py             market discovery via Gamma API
    clob.py              order book + price history via CLOB API
    data_api.py           leaderboard + wallet activity via the Data API
  strategy/
    momentum.py           momentum signal calculation
    smart_money.py          leaderboard-wallet confirmation filter
  scanner.py             concurrent market/token scanning
  portfolio.py           paper portfolio, JSON state, CSV trade log
  risk.py                position sizing + exit rules
  execution/
    base.py              executor interface
    paper.py              simulated fills (default)
    live.py                real orders via py-clob-client
    factory.py             picks paper vs live from settings
  service.py             BotService: start/stop/liquidate, thread-safe loop
  cloud/
    keyvault.py            Key Vault secrets -> env vars
    appconfig.py            App Configuration -> refreshed Settings
  webapp/
    app.py                 FastAPI control panel (start/stop/liquidate/status)
    static/index.html        the panel itself
  bot.py                 headless CLI loop + run_cycle()
  main.py                polybot-server entrypoint (Azure-aware, web UI)
tests/                   unit tests for momentum, risk, portfolio, service, webapp, smart_money, data_api
infra/
  main.bicep             Container Apps env, ACR, Key Vault, App Config, storage
  deploy.sh               end-to-end deploy script (az CLI, no Docker needed)
Dockerfile               multi-stage build for the Container App image
```
