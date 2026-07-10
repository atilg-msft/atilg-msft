from __future__ import annotations

import logging
import os
import threading
import time

from ..config import Settings, load_settings

logger = logging.getLogger(__name__)

DYNAMIC_PREFIX = "POLYBOT_"

# Fields intentionally excluded from App Configuration refresh. Credentials
# (private key/funder address/signature type) are excluded here because
# they're sourced from Key Vault instead (see cloud/keyvault.py's
# KeyVaultSecretsProvider) -- App Configuration never sees their values.
# POLYBOT_MODE is deliberately *not* excluded: it's meant to flip
# paper<->live from App Configuration without a redeploy (see
# BotService._loop()'s executor-rebuild logic, and note in the README that
# App Configuration write access is therefore the real access-control
# boundary for going live, not a deploy gate).
STATIC_KEYS = {
    "POLYBOT_DATA_DIR",
    "POLYBOT_PRIVATE_KEY",
    "POLYBOT_SIGNATURE_TYPE",
    "POLYBOT_FUNDER_ADDRESS",
    "POLYBOT_CLOB_API_URL",
    "POLYBOT_GAMMA_API_URL",
    "POLYBOT_DATA_API_URL",
    "POLYBOT_LEADERBOARD_API_URL",
    "POLYBOT_CHAIN_ID",
}


class AppConfigSettingsProvider:
    """Refreshes strategy/risk/scan parameters from Azure App Configuration.

    `get_settings()` is cheap to call every cycle: it only hits App
    Configuration once every `refresh_seconds`, and simply rebuilds Settings
    from the current environment in between. If no endpoint is configured
    (local/paper dev without Azure), it degrades to plain `load_settings()`.
    """

    def __init__(
        self,
        endpoint: str | None,
        label: str | None = None,
        refresh_seconds: int = 300,
    ) -> None:
        self.endpoint = endpoint
        self.label = label
        self.refresh_seconds = refresh_seconds
        self._lock = threading.Lock()
        self._last_refresh = 0.0
        self._client = None
        if endpoint:
            self._client = self._build_client(endpoint)

    @staticmethod
    def _build_client(endpoint: str):
        try:
            from azure.appconfiguration import AzureAppConfigurationClient
            from azure.identity import DefaultAzureCredential
        except ImportError:
            logger.warning(
                "AZURE_APPCONFIG_ENDPOINT is set but azure-appconfiguration/"
                "azure-identity are not installed; install the 'azure' extra. "
                "Falling back to static environment variables."
            )
            return None
        return AzureAppConfigurationClient(base_url=endpoint, credential=DefaultAzureCredential())

    def _refresh(self) -> None:
        if self._client is None:
            return
        try:
            items = self._client.list_configuration_settings(
                key_filter=f"{DYNAMIC_PREFIX}*", label_filter=self.label
            )
            applied = 0
            for item in items:
                if item.key in STATIC_KEYS:
                    continue
                os.environ[item.key] = item.value
                applied += 1
            logger.info("refreshed %d setting(s) from App Configuration", applied)
        except Exception:
            logger.exception("failed to refresh settings from App Configuration; keeping last-known values")

    def get_settings(self) -> Settings:
        with self._lock:
            now = time.monotonic()
            if self._client is not None and now - self._last_refresh >= self.refresh_seconds:
                self._refresh()
                self._last_refresh = now
        return load_settings()
