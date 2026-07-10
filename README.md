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
   - If `POLYBOT_SIGNAL_FILTER=smart_money` (env var, App Configuration, or
     the control panel's **Sinyal** dropdown), candidates are then filtered
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
| **Kapat** (per row) — `POST /api/positions/{token_id}/close` | Force-closes just that one position at market. Unlike Likidasyona Dön, every other position and the run/stop state are untouched — for stepping in on a single position you disagree with, not an emergency stop. 404s if that position is no longer open. |
| `GET /api/status` | Current state, mode, cash, equity, realized P&L, exposure, open positions (with entry *and* current price/value/unrealized P&L per position), last cycle time, last error. Polled by the page every 3s. |

The desired run state (`running`/`stopped`) is persisted to
`data/control_state.json`, so a container restart resumes whatever you last
asked for rather than silently starting to trade or silently staying idle.
Because state lives in-process (the portfolio, the loop thread), this is a
**singleton service** — don't scale it beyond one replica.

The **Açık Pozisyonlar** table shows entry price/cost next to the current
mark price/value and unrealized P&L for each open position, computed from
the same order-book mid-price fetched during the last trading cycle
(`BotService.last_mark_prices`, refreshed every `poll_interval_seconds` —
not on every page load, to avoid extra CLOB API calls just from someone
having the panel open).

There's also a **Strateji** card with two dropdowns:

- **Strateji** — which strategy generates candidate signals. Only
  `Momentum` exists today; the dropdown is there so adding a second
  strategy later doesn't need new UI.
- **Sinyal** — `Yok` (momentum trades on its own) or `Akıllı Cüzdan Onayı`
  (momentum candidates are also required to match a
  [tracked wallet's recent buy](#smart-money-confirmation-filter)).

Saving calls `POST /api/strategy` (`GET /api/strategy` reads the current
selection), and the choice is persisted to `data/strategy_override.json`.
Once you've picked something from the panel, it **wins over
`POLYBOT_STRATEGY`/`POLYBOT_SIGNAL_FILTER` from env vars or App
Configuration** and survives restarts — otherwise a routine App
Configuration refresh could silently revert an operator's explicit choice
back to the deployed default, which would be more confusing than useful.

### Why did it do that?

Every open position and every closed trade carries a plain-text rationale,
visible in the panel (hover a truncated cell for the full text) and in
`GET /api/trades`:

- **Entries** (`entry_reason`, shown in the **Açık Pozisyonlar** table's
  **Neden** column): the momentum figure that triggered it — e.g.
  `Momentum +12.3% over 15min (threshold 8.0%), price 0.512` — with
  `; confirmed by wallet 0xabc123456… buying $500` appended when the
  smart-money filter was involved.
- **Exits** (`reason` + `exit_reason_detail`, shown as a colored badge with
  the detail on hover in the new **Son İşlemler** table): a short code
  (`take_profit` / `stop_loss` / `max_holding_time` / `manual_liquidation`)
  plus the numbers behind it, e.g. `Price 0.4000 -> 0.4700 (+17.5%),
  threshold +15%` (`polybot/risk.py`'s `describe_exit`).

Both are computed once, at the moment the decision is made
(`polybot/strategy/momentum.py`, `polybot/scanner.py`,
`polybot/risk.py`), and stored alongside the trade — in `portfolio.json`
for open positions and appended to `trades.csv` for closed ones — so the
explanation always matches the state that was actually used to decide,
not a value recomputed later from possibly-changed settings.

## Smart-money confirmation filter

Set `POLYBOT_SIGNAL_FILTER=smart_money` (env var/App Configuration) or pick
**Akıllı Cüzdan Onayı** from the control panel's **Sinyal** dropdown to
require momentum candidates to also line up with what Polymarket's own
highest-PnL traders are doing (`polybot/strategy/smart_money.py`,
`polybot/api/data_api.py`). The other option, **Yok**
(`POLYBOT_SIGNAL_FILTER=none`, the default), runs momentum on its own with
no confirmation step:

1. Every `POLYBOT_SMART_WALLET_REFRESH_MINUTES` (default 6h), it pulls the
   top `POLYBOT_SMART_WALLET_COUNT` wallets from Polymarket's public
   leaderboard (`lb-api.polymarket.com/profit`), ranked by PnL over
   `POLYBOT_SMART_WALLET_PERIOD` (`day`/`week`/`month`/`all`), plus anything
   listed in `POLYBOT_SMART_WALLET_OVERRIDES` (comma-separated addresses you
   add yourself — useful if you've found specific wallets worth following,
   or as a fallback if the leaderboard call ever breaks).
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

> Endpoints and field names are confirmed against live calls: the
> leaderboard lives on a separate host, `lb-api.polymarket.com` (paths
> `/profit` and `/volume`, not a single endpoint with an `orderBy` param
> as first guessed), and `data-api.polymarket.com/activity` returns
> `proxyWallet`/`asset`/`side`/`usdcSize`/`timestamp` — filtered
> server-side to `type=TRADE` to skip REDEEM/MERGE/SPLIT noise. Parsing in
> `polybot/api/data_api.py` still tries a couple of fallback field names
> defensively and logs the real ones it sees the first time
> (`data-api leaderboard sample fields: [...]`) in case Polymarket ever
> renames something. Still off by default (`POLYBOT_SIGNAL_FILTER=none`)
> since it's a stricter, lower-throughput mode you opt into.

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
  `/app/data` so `portfolio.json`, `trades.csv`, `control_state.json`, and
  `strategy_override.json` survive restarts and redeploys.
- An **Azure Container Registry** the app pulls from.

All of it is in `infra/main.bicep`. To deploy:

```bash
az login
./infra/deploy.sh polybot-rg                   # resource group; defaults to centralus
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

### Deploying via GitHub Actions

`.github/workflows/deploy-azure.yml` runs the same steps as `deploy.sh`
from CI. It's **manual-only** (`workflow_dispatch`, never on push) since it
provisions real, billable Azure resources and can enable live trading.

One-time setup — this creates an Azure AD app registration and grants it
access, so it needs to be run once by someone with rights on the target
subscription (not something a CI job can bootstrap for itself):

```bash
# 1. App registration + service principal for GitHub OIDC (no password/secret ever stored)
az ad app create --display-name "polybot-github-deploy"
APP_ID=$(az ad app list --display-name "polybot-github-deploy" --query "[0].appId" -o tsv)
az ad sp create --id "$APP_ID"

# 2. Trust GitHub's OIDC token, scoped to this repo's "prod" environment
#    (matches the `environment: prod` gate in the workflow, so it works
#    regardless of which branch triggers the manual run)
az ad app federated-credential create --id "$APP_ID" --parameters '{
  "name": "github-polybot-deploy",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:atilg-msft/atilg-msft:environment:prod",
  "audiences": ["api://AzureADTokenExchange"]
}'

# 3. Grant it rights on the target resource group. User Access Administrator
#    (not just Contributor) is needed because the Bicep template creates
#    RBAC role assignments for the container app's managed identity.
RG=polybot-rg
SUB_ID=$(az account show --query id -o tsv)
az group create --name "$RG" --location centralus
az role assignment create --assignee "$APP_ID" --role Contributor --scope "/subscriptions/$SUB_ID/resourceGroups/$RG"
az role assignment create --assignee "$APP_ID" --role "User Access Administrator" --scope "/subscriptions/$SUB_ID/resourceGroups/$RG"

# 4. Values for the GitHub secrets below
echo "AZURE_CLIENT_ID=$APP_ID"
echo "AZURE_TENANT_ID=$(az account show --query tenantId -o tsv)"
echo "AZURE_SUBSCRIPTION_ID=$SUB_ID"
```

Then in the GitHub repo:

1. **Settings → Secrets and variables → Actions** — add `AZURE_CLIENT_ID`,
   `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` from the output above.
2. **Settings → Environments → New environment → `prod`** — optionally
   add required reviewers here, so a real person has to approve before the
   workflow touches Azure.
3. **Actions tab → "Deploy to Azure" → Run workflow** — fill in the same
   resource group you granted the role assignment on above, plus region,
   name prefix, and `paper`/`live` mode.

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
.github/workflows/
  deploy-azure.yml       same deploy, from CI (manual trigger, OIDC login)
Dockerfile               multi-stage build for the Container App image
```
