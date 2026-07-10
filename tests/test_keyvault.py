import os

import pytest

from polybot.cloud.keyvault import SECRET_ENV_MAP, KeyVaultSecretsProvider


class FakeSecret:
    def __init__(self, value):
        self.value = value


class FakeSecretClient:
    def __init__(self, secrets):
        self.secrets = secrets
        self.calls = 0

    def get_secret(self, name):
        self.calls += 1
        if name not in self.secrets:
            raise Exception(f"not found: {name}")
        return FakeSecret(self.secrets[name])


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for env_var in SECRET_ENV_MAP.values():
        monkeypatch.delenv(env_var, raising=False)


def test_noop_when_vault_url_unset():
    provider = KeyVaultSecretsProvider(vault_url=None)
    provider.refresh(force=True)
    for env_var in SECRET_ENV_MAP.values():
        assert env_var not in os.environ


def test_refresh_force_populates_env(monkeypatch):
    fake_client = FakeSecretClient(
        {
            "polybot-private-key": "0xsecret",
            "polybot-funder-address": "0xfunder",
            "polybot-signature-type": "proxy",
        }
    )
    provider = KeyVaultSecretsProvider(vault_url="https://fake.vault.azure.net/")
    monkeypatch.setattr(provider, "_client", fake_client)

    provider.refresh(force=True)

    assert os.environ["POLYBOT_PRIVATE_KEY"] == "0xsecret"
    assert os.environ["POLYBOT_FUNDER_ADDRESS"] == "0xfunder"
    assert os.environ["POLYBOT_SIGNATURE_TYPE"] == "proxy"


def test_refresh_respects_interval(monkeypatch):
    fake_client = FakeSecretClient({"polybot-private-key": "0xfirst"})
    provider = KeyVaultSecretsProvider(vault_url="https://fake.vault.azure.net/", refresh_seconds=300)
    monkeypatch.setattr(provider, "_client", fake_client)

    provider.refresh(force=True)
    assert fake_client.calls == len(SECRET_ENV_MAP)

    fake_client.secrets["polybot-private-key"] = "0xrotated"
    provider.refresh()  # too soon, within refresh_seconds -- should be a no-op
    assert os.environ["POLYBOT_PRIVATE_KEY"] == "0xfirst"


def test_missing_secret_does_not_crash(monkeypatch):
    fake_client = FakeSecretClient({})  # none of the secrets exist
    provider = KeyVaultSecretsProvider(vault_url="https://fake.vault.azure.net/")
    monkeypatch.setattr(provider, "_client", fake_client)

    provider.refresh(force=True)  # should not raise

    for env_var in SECRET_ENV_MAP.values():
        assert env_var not in os.environ
