import json

from aiohttp.test_utils import TestClient, TestServer

from lm_provider.config import MemoryVault, Provider, ProviderStore, load_providers, save_providers
from lm_provider.server import create_app
from lm_provider.ticks import (CHECKED, DISABLED, UNCHECKED, cell_glyph, count_text, ticked_count, toggle_model,
                               toggle_service, visible_models)
from test_gateway import AUTH, SECRET, TOKEN, Upstream, run

ALL = ("chat", "vision", "tts", "images")


def test_toggle_service_keeps_vision_with_chat():
    s = {}
    assert toggle_service(s, "m", "vision") and s == {"m": ["chat", "vision"]}
    toggle_service(s, "m", "chat")  # clearing chat clears vision, so nothing is left
    assert s == {}
    toggle_service(s, "m", "tts")
    toggle_service(s, "m", "images")
    assert s == {"m": ["tts", "images"]}
    toggle_service(s, "m", "tts")
    toggle_service(s, "m", "images")
    assert s == {} and ticked_count(s) == 0


def test_toggle_service_refuses_services_the_provider_lacks():
    s = {}
    assert toggle_service(s, "m", "chat", allowed=("tts",)) is False
    assert toggle_service(s, "m", "bogus") is False
    assert s == {}


def test_nothing_is_ticked_on_its_own_and_name_click_uses_the_guess():
    s = {}
    assert visible_models(["gpt-4o", "flux-1"], s) == ["flux-1", "gpt-4o"] and ticked_count(s) == 0
    toggle_model(s, "gpt-4o", ALL)
    assert s == {"gpt-4o": ["chat", "vision"]}
    toggle_model(s, "gpt-4o", ALL)
    assert s == {}


def test_cells_filter_and_count():
    s = {"a": ["chat"], "z-old": ["chat"]}
    assert cell_glyph(s, "a", "chat") == CHECKED and cell_glyph(s, "a", "tts") == UNCHECKED
    assert cell_glyph(s, "a", "tts", allowed=("chat",)) == DISABLED
    assert visible_models(["b", "a"], s, "") == ["a", "b", "z-old"]  # ticked but not offered stays
    assert visible_models(["b", "a"], s, "B") == ["b"]
    assert visible_models(["b", "a"], s, "", ticked_only=True) == ["a", "z-old"]
    assert count_text(s, 3) == "2 of 3 ticked" and count_text(s) == "2 ticked"
    assert "Nothing ticked" in count_text({})


def test_old_file_without_ticks_is_flagged_and_migrated_on_first_model_list(tmp_path):
    path = tmp_path / "providers.json"
    path.write_text(json.dumps({"version": 1, "providers": [
        {"id": "mock", "name": "Mock", "base_url": "https://x.test/v1", "preset": "custom"},
        {"id": "kept", "name": "Kept", "base_url": "https://y.test/v1", "models": ["m"]}]}), encoding="utf-8")
    old = load_providers(path)
    assert [p.legacy_all_chat for p in old] == [True, False]

    async def scenario():
        upstream = Upstream()
        await upstream.server.start_server()
        providers = load_providers(path)
        providers[0].base_url = upstream.base_url
        save_providers(providers[:1], path)  # keeps the flag
        assert load_providers(path)[0].legacy_all_chat is True
        store = ProviderStore(MemoryVault({"mock": SECRET}), path)
        client = TestClient(TestServer(create_app(store, TOKEN), host="127.0.0.1"))
        await client.start_server()
        try:
            # Works before the migration: chat to an unticked-but-known model is allowed.
            ok = await client.post("/v1/chat/completions", json={"model": "mock/model-a"}, headers=AUTH)
            assert ok.status == 200
            data = (await (await client.get("/v1/models", headers=AUTH)).json())["data"]
            assert sorted(m["id"] for m in data) == ["mock/model-a", "mock/vendor/model-b"]
        finally:
            await client.close()
            await upstream.server.close()

    run(scenario())
    saved = load_providers(path)[0]
    assert saved.legacy_all_chat is False
    assert saved.models == [{"id": "model-a", "services": ["chat"]}, {"id": "vendor/model-b", "services": ["chat"]}]
    # A later save with the user unticking everything stays empty: no second migration.
    saved.models = []
    save_providers([saved], path)
    assert load_providers(path)[0].legacy_all_chat is False
