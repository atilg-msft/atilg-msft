from __future__ import annotations

import logging
import os

from .cloud.appconfig import AppConfigSettingsProvider
from .cloud.keyvault import KeyVaultSecretsProvider
from .config import load_settings
from .logging_conf import setup_logging
from .service import BotService
from .webapp.app import create_app

logger = logging.getLogger(__name__)


def build_service() -> BotService:
    keyvault_provider = KeyVaultSecretsProvider(
        vault_url=os.environ.get("AZURE_KEY_VAULT_URL"),
        refresh_seconds=int(os.environ.get("AZURE_KEY_VAULT_REFRESH_SECONDS", "300")),
    )
    # Force a first fetch so the initial Settings() build already has them,
    # regardless of the refresh interval above.
    keyvault_provider.refresh(force=True)
    settings = load_settings()
    setup_logging(settings.data_dir)

    logger.info("starting polybot server in %s mode", settings.mode)
    if settings.mode == "live":
        logger.warning("LIVE MODE: real orders, real funds.")

    appconfig_provider = AppConfigSettingsProvider(
        endpoint=os.environ.get("AZURE_APPCONFIG_ENDPOINT"),
        label=os.environ.get("AZURE_APPCONFIG_LABEL") or None,
        refresh_seconds=int(os.environ.get("AZURE_APPCONFIG_REFRESH_SECONDS", "300")),
    )
    return BotService(
        settings,
        settings_provider=appconfig_provider.get_settings,
        keyvault_provider=keyvault_provider,
    )


def main() -> None:
    import uvicorn

    service = build_service()
    app = create_app(service)
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
