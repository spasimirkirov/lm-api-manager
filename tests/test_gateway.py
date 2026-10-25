import asyncio
import json
import logging

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from lm_provider.config import MemoryVault, Provider, ProviderStore, save_providers
from lm_provider.server import create_app, split_model

TOKEN = "t" * 43
SECRET = "sk-test-ABCDEFGHIJKLMNOPQRSTUVWX1234"
OTHER_SECRET = "sk-other-ZYXWVUTSRQPONMLKJIHGFEDC9876"


def run(coro):
    return asyncio.run(coro)


class Upstream:
    """A fake OpenAI-compatible provider that records what it receives."""

    def __init__(self):
        self.requests = []
        self.release = None
        self.status = 200
        self.error_body = None
        app = web.Application()
        app.router.add_get("/v1/models", self.models)
        app.router.add_post("/v1/chat/completions", self.chat)
        self.server = TestServer(app, host="127.0.0.1")

    @property
    def base_url(self):
        return str(self.server.make_url("/v1"))

    async def models(self, request):
        self.requests.append(("models", dict(request.headers), None))
        if self.status != 200:
            return web.json_response(self.error_body or {}, status=self.status)
        return web.json_response({"object": "list", "data": [{"id": "model-a"}, {"id": "vendor/model-b"}]})

    async def chat(self, request):
        body = await request.json()
        self.requests.append(("chat", dict(request.headers), body))
        if self.status != 200:
            return web.json_response(self.error_body or {}, status=self.status)
        if not body.get("stream"):
            return web.json_response({"id": "x", "choices": [{"message": {"role": "assistant", "content": "hi"}}]})
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n')
        await self.release.wait()
        await response.write(b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n')
        await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response


async def gateway(tmp_path, upstream, models=None, keys=None):
    path = tmp_path / "providers.json"
    providers = [
        Provider(id="mock", name="Mock AI", base_url=upstream.base_url, models=models or []),
        Provider(id="other", name="Other AI", base_url=upstream.base_url),
    ]
    save_providers(providers, path)
    vault = MemoryVault(keys if keys is not None else {"mock": SECRET, "other": OTHER_SECRET})
    store = ProviderStore(vault, path)
    client = TestClient(TestServer(create_app(store, TOKEN), host="127.0.0.1"))
    await client.start_server()
    return client


AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_split_model():
    assert split_model("deepseek/deepseek-chat") == ("deepseek", "deepseek-chat")
    assert split_model("openrouter/openai/gpt-4o") == ("openrouter", "openai/gpt-4o")
    assert split_model("no-prefix") == (None, None)
    assert split_model("/x") == (None, None)
    assert split_model("x/") == (None, None)
    assert split_model(None) == (None, None)


def test_token_is_required(tmp_path):
    async def scenario():
        upstream = Upstream()
        await upstream.server.start_server()
        client = await gateway(tmp_path, upstream)
        try:
            assert (await client.get("/health")).status == 401
            assert (await client.get("/health", headers={"Authorization": "Bearer wrong"})).status == 401
            assert (await client.get("/health", headers={"Authorization": TOKEN})).status == 401
            response = await client.get("/health", headers=AUTH)
            assert response.status == 200
            assert (await response.json())["providers"] == 2
            # Nothing reached the provider without the token.
            assert (await client.post("/v1/chat/completions", json={"model": "mock/model-a"})).status == 401
            assert upstream.requests == []
        finally:
            await client.close()
            await upstream.server.close()

    run(scenario())


def test_browser_origin_is_refused_without_cors(tmp_path):
    async def scenario():
        upstream = Upstream()
        await upstream.server.start_server()
        client = await gateway(tmp_path, upstream)
        try:
            for origin in ("https://evil.example", "null", "http://127.0.0.1"):
                response = await client.post("/v1/chat/completions", json={"model": "mock/model-a"},
                                             headers={**AUTH, "Origin": origin})
                assert response.status == 403
                assert not any(h.lower().startswith("access-control-") for h in response.headers)
            assert upstream.requests == []
        finally:
            await client.close()
            await upstream.server.close()

    run(scenario())


def test_models_are_prefixed_per_provider(tmp_path):
    async def scenario():
        upstream = Upstream()
        await upstream.server.start_server()
        client = await gateway(tmp_path, upstream)
        try:
            data = await (await client.get("/v1/models", headers=AUTH)).json()
            ids = sorted(m["id"] for m in data["data"])
            assert ids == ["mock/model-a", "mock/vendor/model-b", "other/model-a", "other/vendor/model-b"]
            assert data["errors"] == []
            sent_keys = sorted(headers["Authorization"] for _, headers, _ in upstream.requests)
            assert sent_keys == sorted([f"Bearer {SECRET}", f"Bearer {OTHER_SECRET}"])
        finally:
            await client.close()
            await upstream.server.close()

    run(scenario())


def test_allow_list_limits_models(tmp_path):
    async def scenario():
        upstream = Upstream()
        await upstream.server.start_server()
        client = await gateway(tmp_path, upstream, models=["model-a"])
        try:
            data = await (await client.get("/v1/models", headers=AUTH)).json()
            assert "mock/model-a" in [m["id"] for m in data["data"]]
            assert "mock/vendor/model-b" not in [m["id"] for m in data["data"]]
            response = await client.post("/v1/chat/completions", json={"model": "mock/vendor/model-b"}, headers=AUTH)
            assert response.status == 404
        finally:
            await client.close()
            await upstream.server.close()

    run(scenario())


def test_chat_routes_by_prefix_and_adds_only_that_key(tmp_path):
    async def scenario():
        upstream = Upstream()
        await upstream.server.start_server()
        client = await gateway(tmp_path, upstream)
        try:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "mock/vendor/model-b", "messages": [{"role": "user", "content": "hi"}]},
                headers=AUTH,
            )
            assert response.status == 200
            assert (await response.json())["choices"][0]["message"]["content"] == "hi"
            kind, headers, body = upstream.requests[-1]
            assert kind == "chat"
            assert body["model"] == "vendor/model-b"
            assert headers["Authorization"] == f"Bearer {SECRET}"
            # The session token never travels upstream.
            assert TOKEN not in json.dumps(headers)

            unknown = await client.post("/v1/chat/completions", json={"model": "nope/x"}, headers=AUTH)
            assert unknown.status == 404
            bad = await client.post("/v1/chat/completions", json={"model": "plain"}, headers=AUTH)
            assert bad.status == 400
            not_json = await client.post("/v1/chat/completions", data=b"{", headers=AUTH)
            assert not_json.status == 400
        finally:
            await client.close()
            await upstream.server.close()

    run(scenario())


def test_streaming_is_passed_through_unbuffered(tmp_path):
    async def scenario():
        upstream = Upstream()
        upstream.release = asyncio.Event()
        await upstream.server.start_server()
        client = await gateway(tmp_path, upstream)
        try:
            response = await client.post("/v1/chat/completions",
                                         json={"model": "mock/model-a", "stream": True}, headers=AUTH)
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("text/event-stream")
            # The first chunk arrives while the provider is still holding the rest back.
            first = await asyncio.wait_for(response.content.readuntil(b"\n\n"), timeout=3)
            assert b"Hel" in first
            upstream.release.set()
            rest = await asyncio.wait_for(response.content.read(), timeout=3)
            assert b"lo" in rest and b"[DONE]" in rest
        finally:
            await client.close()
            await upstream.server.close()

    run(scenario())


@pytest.mark.parametrize("status,expected_status,code", [
    (401, 401, "invalid_api_key"),
    (402, 402, "insufficient_quota"),
    (403, 403, "permission_denied"),
    (429, 429, "rate_limited"),
    (500, 502, "upstream_unavailable"),
    (503, 502, "upstream_unavailable"),
    (400, 400, "upstream_error"),
])
def test_upstream_errors_are_mapped_and_never_leak_the_key(tmp_path, caplog, status, expected_status, code):
    async def scenario():
        upstream = Upstream()
        upstream.status = status
        upstream.error_body = {"error": {"message": f"Bad thing for key {SECRET} (also sk-proj-abcdefghijklmnop)"}}
        await upstream.server.start_server()
        client = await gateway(tmp_path, upstream)
        try:
            response = await client.post("/v1/chat/completions", json={"model": "mock/model-a"}, headers=AUTH)
            text = await response.text()
            assert response.status == expected_status
            assert json.loads(text)["error"]["code"] == code
            assert "Mock AI" in json.loads(text)["error"]["message"]
            assert SECRET not in text and SECRET[-12:] not in text
            assert "sk-proj-abcdefghijklmnop" not in text
            models = await (await client.get("/v1/models", headers=AUTH)).text()
            assert SECRET not in models
        finally:
            await client.close()
            await upstream.server.close()

    with caplog.at_level(logging.DEBUG):
        run(scenario())
    assert SECRET not in caplog.text
    assert OTHER_SECRET not in caplog.text


def test_unreachable_provider(tmp_path):
    async def scenario():
        upstream = Upstream()
        await upstream.server.start_server()
        base_url = upstream.base_url
        await upstream.server.close()  # nothing listens there any more
        path = tmp_path / "providers.json"
        save_providers([Provider(id="gone", name="Gone AI", base_url=base_url)], path)
        store = ProviderStore(MemoryVault({"gone": SECRET}), path)
        client = TestClient(TestServer(create_app(store, TOKEN), host="127.0.0.1"))
        await client.start_server()
        try:
            response = await client.post("/v1/chat/completions", json={"model": "gone/x"}, headers=AUTH)
            body = await response.json()
            assert response.status == 502
            assert body["error"]["code"] == "upstream_unreachable"
        finally:
            await client.close()

    run(scenario())


def test_missing_key(tmp_path):
    async def scenario():
        upstream = Upstream()
        await upstream.server.start_server()
        client = await gateway(tmp_path, upstream, keys={})
        try:
            response = await client.post("/v1/chat/completions", json={"model": "mock/model-a"}, headers=AUTH)
            assert response.status == 401
            assert (await response.json())["error"]["code"] == "missing_api_key"
            assert upstream.requests == []
        finally:
            await client.close()
            await upstream.server.close()

    run(scenario())


def test_short_token_is_rejected():
    with pytest.raises(ValueError):
        create_app(ProviderStore(MemoryVault()), "short")
