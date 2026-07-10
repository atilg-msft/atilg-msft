from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

# Key Vault secret name -> env var consumed by polybot.config.Settings.
# All three are treated as "security fields" here (not App Configuration):
# rotating a wallet or switching signature type is still a credentials
# change, even though signature_type itself isn't secret data.
SECRET_ENV_MAP = {
    "polybot-private-key": "POLYBOT_PRIVATE_KEY",
    "polybot-funder-address": "POLYBOT_FUNDER_ADDRESS",
    "polybot-signature-type": "POLYBOT_SIGNATURE_TYPE",
}


class KeyVaultSecretsProvider:
    """Keeps POLYBOT_PRIVATE_KEY/POLYBOT_FUNDER_ADDRESS/POLYBOT_SIGNATURE_TYPE
    in the process environment in sync with Key Vault, so rotating a wallet
    (or switching proxy/eoa/gnosis-safe) takes effect on the next refresh
    instead of requiring a redeploy.

    The secret *value* never leaves Key Vault's own storage/RBAC/audit trail
    for this -- App Configuration only ever sees the fact that these fields
    exist, never their contents (see cloud/appconfig.py's STATIC_KEYS).

    No-op throughout if `vault_url` is unset, or if the optional
    azure-identity/azure-keyvault-secrets packages aren't installed, so
    plain paper-trading keeps working without the 'azure' extra.
    """

    def __init__(self, vault_url: str | None, refresh_seconds: int = 300) -> None:
        self.vault_url = vault_url
        self.refresh_seconds = refresh_seconds
        self._lock = threading.Lock()
        self._last_refresh = 0.0
        self._client = None
        if vault_url:
            self._client = self._build_client(vault_url)

    @staticmethod
    def _build_client(vault_url: str):
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
        except ImportError:
            logger.warning(
                "AZURE_KEY_VAULT_URL is set but azure-identity/azure-keyvault-secrets "
                "are not installed; install the 'azure' extra to use Key Vault. Skipping."
            )
            return None
        return SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())

    def refresh(self, force: bool = False) -> None:
        if self._client is None:
            return
        now = time.monotonic()
        with self._lock:
            if not force and now - self._last_refresh < self.refresh_seconds:
                return
            for secret_name, env_var in SECRET_ENV_MAP.items():
                try:
                    secret = self._client.get_secret(secret_name)
                except Exception:
                    logger.info(
                        "Key Vault secret %s not found (fine if unused, e.g. paper mode)",
                        secret_name,
                    )
                    continue
                if os.environ.get(env_var) != secret.value:
                    logger.info("refreshed %s from Key Vault secret %s", env_var, secret_name)
                os.environ[env_var] = secret.value
            self._last_refresh = now
