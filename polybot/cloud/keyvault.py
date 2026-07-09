from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Key Vault secret name -> env var consumed by polybot.config.Settings.
SECRET_ENV_MAP = {
    "polybot-private-key": "POLYBOT_PRIVATE_KEY",
    "polybot-funder-address": "POLYBOT_FUNDER_ADDRESS",
}


def load_secrets_from_key_vault(vault_url: str | None) -> None:
    """Fetch trading secrets from Azure Key Vault into the process environment.

    No-op if `vault_url` is unset, or if the optional azure-identity /
    azure-keyvault-secrets packages aren't installed (so plain paper-trading
    keeps working without the 'azure' extra). Authenticates via
    DefaultAzureCredential, which picks up the Container App's managed
    identity in Azure and falls back to `az login` locally.

    Values already present in the environment (e.g. a local .env override)
    are left untouched -- Key Vault only fills gaps.
    """
    if not vault_url:
        return
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError:
        logger.warning(
            "AZURE_KEY_VAULT_URL is set but azure-identity/azure-keyvault-secrets "
            "are not installed; install the 'azure' extra to use Key Vault. Skipping."
        )
        return

    client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())
    for secret_name, env_var in SECRET_ENV_MAP.items():
        if os.environ.get(env_var):
            continue
        try:
            secret = client.get_secret(secret_name)
        except Exception:
            logger.info(
                "Key Vault secret %s not found (fine if unused, e.g. paper mode)", secret_name
            )
            continue
        os.environ[env_var] = secret.value
        logger.info("loaded %s from Key Vault secret %s", env_var, secret_name)
