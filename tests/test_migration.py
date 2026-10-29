"""Taking over the data of "LM OpenAI Provider" (the name before 0.2.0)."""

import json
import sys
import types

import pytest

from lm_provider.config import (
    LEGACY_KEYRING_SERVICE,
    KEYRING_SERVICE,
    KeyVault,
    load_providers,
    migrate_legacy_providers,
    providers_path,
)

SECRET = "sk-test-ABCDEFGHIJKLMNOPQRSTUVWX1234"
LIST = {"version": 1, "providers": [{"id": "openai", "name": "OpenAI", "base_url": "https://api.openai.com/v1"}]}


class FakeKeyring(types.ModuleType):
    """Stands in for the `keyring` module: an in-memory credential store."""

    class _Errors:
        class PasswordDeleteError(Exception):
            pass

    def __init__(self):
        super().__init__("keyring")
        self.errors = self._Errors
        self.store = {}

    def get_password(self, service, user):
        return self.store.get((service, user))

    def set_password(self, service, user, password):
        self.store[(service, user)] = password

    def delete_password(self, service, user):
        if (service, user) not in self.store:
            raise self.errors.PasswordDeleteError(user)
        del self.store[(service, user)]


@pytest.fixture
def fake_keyring(monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setitem(sys.modules, "keyring", fake)
    return fake


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    monkeypatch.delenv("LM_PROVIDER_HOME", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path / "PalEngine"


def write_legacy(appdata):
    old = appdata / "lm-open-ai-provider" / "providers.json"
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps(LIST), encoding="utf-8")
    return old


def test_old_provider_list_is_copied_on_first_use(appdata):
    old = write_legacy(appdata)
    path = providers_path()
    assert path == appdata / "lm-api-manager" / "providers.json"
    assert [p.id for p in load_providers(path)] == ["openai"]
    assert old.exists()  # the old file stays


def test_existing_new_list_is_never_overwritten(appdata):
    write_legacy(appdata)
    new = appdata / "lm-api-manager" / "providers.json"
    new.parent.mkdir(parents=True)
    new.write_text(json.dumps({"version": 1, "providers": []}), encoding="utf-8")
    assert load_providers(providers_path()) == []


def test_nothing_to_migrate(appdata):
    assert not migrate_legacy_providers(appdata / "lm-api-manager", appdata / "lm-open-ai-provider")
    assert not (appdata / "lm-api-manager").exists()


def test_override_home_skips_migration(appdata, tmp_path, monkeypatch):
    write_legacy(appdata)
    home = tmp_path / "custom"
    monkeypatch.setenv("LM_PROVIDER_HOME", str(home))
    assert providers_path() == home / "providers.json"
    assert not (home / "providers.json").exists()


def test_key_under_old_name_moves_on_first_read(fake_keyring):
    fake_keyring.store[(LEGACY_KEYRING_SERVICE, "openai")] = SECRET
    vault = KeyVault()
    assert vault.get("openai") == SECRET
    assert fake_keyring.store == {(KEYRING_SERVICE, "openai"): SECRET}
    assert vault.get("openai") == SECRET


def test_new_key_wins_over_old(fake_keyring):
    fake_keyring.store[(KEYRING_SERVICE, "openai")] = "sk-new-key-0000"
    fake_keyring.store[(LEGACY_KEYRING_SERVICE, "openai")] = SECRET
    assert KeyVault().get("openai") == "sk-new-key-0000"


def test_missing_key_is_none(fake_keyring):
    assert KeyVault().get("nobody") is None
    assert fake_keyring.store == {}


def test_delete_removes_both_names(fake_keyring):
    fake_keyring.store[(KEYRING_SERVICE, "openai")] = SECRET
    fake_keyring.store[(LEGACY_KEYRING_SERVICE, "openai")] = SECRET
    KeyVault().delete("openai")
    assert fake_keyring.store == {}
    KeyVault().delete("openai")  # nothing left: no error
