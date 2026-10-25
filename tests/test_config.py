import json
import os

import pytest

from lm_provider.config import (
    ConfigError,
    MemoryVault,
    Provider,
    ProviderStore,
    key_hint,
    load_providers,
    normalize_base_url,
    save_providers,
    unique_id,
)
from lm_provider.upstream import auth_headers, redact

SECRET = "sk-test-ABCDEFGHIJKLMNOPQRSTUVWX1234"


def test_base_url_needs_https_unless_local():
    assert normalize_base_url("https://api.openai.com/v1/") == "https://api.openai.com/v1"
    assert normalize_base_url("http://127.0.0.1:1234/v1") == "http://127.0.0.1:1234/v1"
    assert normalize_base_url("http://localhost:8080/v1") == "http://localhost:8080/v1"
    for bad in ("http://api.example.com/v1", "ftp://x", "api.openai.com", "", "https://u:p@x.com/v1",
                "https://x.com/v1?k=1"):
        with pytest.raises(ConfigError):
            normalize_base_url(bad)


def test_ids_are_unique_slugs():
    assert unique_id("OpenAI", set()) == "openai"
    assert unique_id("OpenAI", {"openai"}) == "openai-2"
    assert unique_id("My Work / Key!", set()) == "my-work-key"
    assert "/" not in unique_id("a/b", set())


def test_providers_file_holds_no_keys(tmp_path):
    path = tmp_path / "providers.json"
    save_providers([Provider(id="openai", name="OpenAI", base_url="https://api.openai.com/v1", preset="openai")], path)
    text = path.read_text(encoding="utf-8")
    assert "key" not in json.loads(text)["providers"][0]
    assert [p.id for p in load_providers(path)] == ["openai"]
    # No temp files left behind by the atomic write.
    assert os.listdir(tmp_path) == ["providers.json"]


def test_bad_entries_are_skipped(tmp_path):
    path = tmp_path / "providers.json"
    path.write_text(json.dumps({"providers": [
        {"id": "ok", "name": "OK", "base_url": "https://ok.example/v1"},
        {"id": "ok", "name": "Duplicate", "base_url": "https://ok.example/v1"},
        {"id": "Bad/Id", "name": "Bad", "base_url": "https://x.example/v1"},
        {"id": "plain", "name": "Plain", "base_url": "http://remote.example/v1"},
        "nonsense",
    ]}), encoding="utf-8")
    assert [p.name for p in load_providers(path)] == ["OK"]


def test_store_reloads_when_the_file_changes(tmp_path):
    path = tmp_path / "providers.json"
    vault = MemoryVault({"a": SECRET})
    store = ProviderStore(vault, path)
    assert store.providers() == []
    save_providers([Provider(id="a", name="A", base_url="https://a.example/v1")], path)
    os.utime(path, ns=(1, 1_000_000_000))
    assert [p.id for p in store.providers()] == ["a"]
    assert store.key("a") == SECRET


def test_key_hint_shows_last_four_only():
    assert key_hint(SECRET) == "••••1234"
    assert SECRET[:-4] not in key_hint(SECRET)
    assert key_hint("") == ""
    assert "abc" not in key_hint("abc")


def test_anthropic_preset_sends_its_headers():
    provider = Provider(id="anthropic", name="Anthropic", base_url="https://api.anthropic.com/v1", preset="anthropic")
    headers = auth_headers(provider, SECRET)
    assert headers["x-api-key"] == SECRET
    assert headers["anthropic-version"]
    openai = Provider(id="openai", name="OpenAI", base_url="https://api.openai.com/v1", preset="openai")
    assert "x-api-key" not in auth_headers(openai, SECRET)


def test_redact():
    assert SECRET not in redact(f"bad key {SECRET}", [SECRET])
    assert "sk-proj-abcdefghijk" not in redact("Incorrect API key provided: sk-proj-abcdefghijk*****1234")
    assert "AIzaSyA1234567890abcdefghijk" not in redact("key AIzaSyA1234567890abcdefghijk")
