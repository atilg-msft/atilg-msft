from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # Runtime mode
    mode: str = field(default_factory=lambda: os.environ.get("POLYBOT_MODE", "paper"))
    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("POLYBOT_DATA_DIR", "data"))
    )
    poll_interval_seconds: int = field(
        default_factory=lambda: _env_int("POLYBOT_POLL_INTERVAL_SECONDS", 60)
    )

    # API endpoints
    clob_api_url: str = field(
        default_factory=lambda: os.environ.get(
            "POLYBOT_CLOB_API_URL", "https://clob.polymarket.com"
        )
    )
    gamma_api_url: str = field(
        default_factory=lambda: os.environ.get(
            "POLYBOT_GAMMA_API_URL", "https://gamma-api.polymarket.com"
        )
    )
    chain_id: int = field(default_factory=lambda: _env_int("POLYBOT_CHAIN_ID", 137))

    # Market scanning / filters
    max_markets_scanned: int = field(
        default_factory=lambda: _env_int("POLYBOT_MAX_MARKETS_SCANNED", 200)
    )
    min_volume_24h: float = field(
        default_factory=lambda: _env_float("POLYBOT_MIN_VOLUME_24H", 1000.0)
    )
    min_liquidity: float = field(
        default_factory=lambda: _env_float("POLYBOT_MIN_LIQUIDITY", 500.0)
    )
    min_price: float = field(default_factory=lambda: _env_float("POLYBOT_MIN_PRICE", 0.05))
    max_price: float = field(default_factory=lambda: _env_float("POLYBOT_MAX_PRICE", 0.95))

    # Momentum strategy
    lookback_minutes: int = field(
        default_factory=lambda: _env_int("POLYBOT_LOOKBACK_MINUTES", 15)
    )
    momentum_threshold: float = field(
        default_factory=lambda: _env_float("POLYBOT_MOMENTUM_THRESHOLD", 0.08)
    )

    # Exit rules
    take_profit_pct: float = field(
        default_factory=lambda: _env_float("POLYBOT_TAKE_PROFIT_PCT", 0.15)
    )
    stop_loss_pct: float = field(
        default_factory=lambda: _env_float("POLYBOT_STOP_LOSS_PCT", 0.10)
    )
    max_holding_minutes: int = field(
        default_factory=lambda: _env_int("POLYBOT_MAX_HOLDING_MINUTES", 240)
    )
    cooldown_minutes: int = field(
        default_factory=lambda: _env_int("POLYBOT_COOLDOWN_MINUTES", 30)
    )

    # Risk / position sizing
    max_concurrent_positions: int = field(
        default_factory=lambda: _env_int("POLYBOT_MAX_CONCURRENT_POSITIONS", 8)
    )
    max_position_usd: float = field(
        default_factory=lambda: _env_float("POLYBOT_MAX_POSITION_USD", 50.0)
    )
    position_pct_of_equity: float = field(
        default_factory=lambda: _env_float("POLYBOT_POSITION_PCT_OF_EQUITY", 0.02)
    )
    max_total_exposure_pct: float = field(
        default_factory=lambda: _env_float("POLYBOT_MAX_TOTAL_EXPOSURE_PCT", 0.6)
    )

    # Paper trading
    starting_cash: float = field(
        default_factory=lambda: _env_float("POLYBOT_STARTING_CASH", 1000.0)
    )
    slippage_bps: float = field(
        default_factory=lambda: _env_float("POLYBOT_SLIPPAGE_BPS", 50.0)
    )
    fee_bps: float = field(default_factory=lambda: _env_float("POLYBOT_FEE_BPS", 0.0))

    # Live trading (only used when mode == "live")
    private_key: str | None = field(
        default_factory=lambda: os.environ.get("POLYBOT_PRIVATE_KEY")
    )
    signature_type: str = field(
        default_factory=lambda: os.environ.get("POLYBOT_SIGNATURE_TYPE", "proxy")
    )
    funder_address: str | None = field(
        default_factory=lambda: os.environ.get("POLYBOT_FUNDER_ADDRESS")
    )

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.mode not in ("paper", "live"):
            raise ValueError(f"Invalid POLYBOT_MODE: {self.mode!r} (expected 'paper' or 'live')")
        if self.signature_type not in ("proxy", "eoa", "gnosis-safe"):
            raise ValueError(f"Invalid POLYBOT_SIGNATURE_TYPE: {self.signature_type!r}")
        if self.mode == "live" and not self.private_key:
            raise ValueError(
                "POLYBOT_MODE=live requires POLYBOT_PRIVATE_KEY to be set"
            )


def load_settings() -> Settings:
    return Settings()
