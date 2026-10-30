import asyncio
import base64

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from lm_provider.config import (MemoryVault, Provider, ProviderStore, load_providers, normalize_models,
                                save_providers, validate_provider)
from lm_provider.presets import PRESETS, PRESETS_BY_ID
from lm_provider.server import create_app
from lm_provider.services import guess_services

TOKEN = "t" * 43
SECRET = "sk-test-ABCDEFGHIJKLMNOPQRSTUVWX1234"
XI_SECRET = "xi-secret-0123456789abcdef0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PNG = b"\x89PNG fake image bytes"
MP3 = b"ID3 fake mp3 bytes"
KEYS = {"di": SECRET, "eleven": XI_SECRET}


def run(coro):
    return asyncio.run(coro)


class Mock:
    """One fake host serving an OpenAI-style provider, ElevenLabs and an image CDN."""

    def __init__(self):
        self.calls = []
        self.image_reply = "b64"
        self.image_status = 200
        app = web.Application()
        app.router.add_get("/v1/models", self.models)
        app.router.add_post("/v1/images/generations", self.images)
        app.router.add_post("/v1/audio/speech", self.speech)
        app.router.add_post("/v1/text-to-speech/{voice}", self.eleven_tts)
        app.router.add_get("/v2/voices", self.eleven_voices)
        self.server = TestServer(app, host="127.0.0.1")

    def url(self, path=""):
        return str(self.server.make_url(path))

    def call(self, kind):
        return next(c for c in self.calls if c[0] == kind)

    async def models(self, request):
        self.calls.append(("models", dict(request.headers), None))
        if "xi-api-key" in request.headers:
            return web.json_response([
                {"model_id": "eleven_multilingual_v2", "can_do_text_to_speech": True},
                {"model_id": "scribe", "can_do_text_to_speech": False}])
        return web.json_response({"data": [{"id": "chat-1"}, {"id": "flux-1"}, {"id": "tts-1"}]})

    async def images(self, request):
        body = await request.json()
        self.calls.append(("images", dict(request.headers), body))
        if self.image_status != 200:
            return web.json_response({"error": {"message": f"bad {SECRET}"}}, status=self.image_status)
        if self.image_reply == "url":
            return web.json_response({"data": [{"url": "http://insecure.test/pic.png"}]})
        return web.json_response({"data": [{"b64_json": base64.b64encode(PNG).decode()}]})

    async def speech(self, request):
        body = await request.json()
        self.calls.append(("speech", dict(request.headers), body))
        return web.Response(body=MP3, content_type="audio/mpeg")

    async def eleven_tts(self, request):
        body = await request.json()
        self.calls.append(("eleven_tts", dict(request.headers),
                           {"voice": request.match_info["voice"], "query": dict(request.query), **body}))
        return web.Response(body=MP3, content_type="audio/mpeg")

    async def eleven_voices(self, request):
        self.calls.append(("eleven_voices", dict(request.headers), None))
        return web.json_response({"voices": [
            {"voice_id": "v1", "name": "Rachel", "labels": {"gender": "female", "language": "en"},
             "preview_url": "https://x/p.mp3"},
            {"voice_id": "v2", "name": "Bob", "verified_languages": [{"language": "bg"}]}], "has_more": False})


async def gateway(tmp_path, providers, keys):
    path = tmp_path / "providers.json"
    save_providers(providers, path)
    store = ProviderStore(MemoryVault(keys), path)
    client = TestClient(TestServer(create_app(store, TOKEN), host="127.0.0.1"))
    await client.start_server()
    return client


def providers_for(mock, more=()):
    models = [{"id": "chat-1", "services": ["chat", "vision"]}, {"id": "flux-1", "services": ["images"]},
              {"id": "tts-1", "services": ["tts"]}]
    return [Provider(id="di", name="Deep Infra", base_url=mock.url("/v1"), preset="deepinfra", models=models),
            Provider(id="eleven", name="Eleven Labs", base_url=mock.url(), preset="elevenlabs",
                     models=[{"id": "eleven_multilingual_v2", "services": ["tts"]}]), *more]


def with_gateway(tmp_path, scenario, mock=None, providers=None, keys=None):
    async def go():
        m = mock or Mock()
        async with m.server:
            provs = providers(m) if callable(providers) else providers
            client = await gateway(tmp_path, provs or providers_for(m), keys or KEYS)
            async with client:
                await scenario(client, m)
    run(go())


def test_guess_services():
    assert guess_services("black-forest-labs/FLUX.1-schnell") == ["images"]
    assert guess_services("stabilityai/sdxl-turbo") == ["images"]
    assert guess_services("hexgrad/Kokoro-82M") == ["tts"]
    assert guess_services("eleven_multilingual_v2") == ["tts"]
    assert guess_services("Qwen2.5-VL-72B") == ["chat", "vision"]
    assert guess_services("gpt-4o") == ["chat", "vision"]
    assert guess_services("deepseek-chat") == ["chat"]
    assert guess_services("flux-1", allowed=("chat", "vision")) == ["chat"]


def test_old_plain_list_becomes_chat_models(tmp_path):
    assert normalize_models(["a", " b ", "a", 3]) == [{"id": "a", "services": ["chat"]},
                                                      {"id": "b", "services": ["chat"]}]
    path = tmp_path / "providers.json"
    path.write_text('{"version":1,"providers":[{"id":"x","name":"X","base_url":"https://x.test/v1",'
                    '"models":["m1","m2"]}]}', encoding="utf-8")
    assert load_providers(path)[0].models == [{"id": "m1", "services": ["chat"]}, {"id": "m2", "services": ["chat"]}]


def test_services_are_normalized():
    p = validate_provider(Provider(id="x", name="X", base_url="https://x.test/v1", models=[
        {"id": "m", "services": ["vision", "bogus"]}, {"id": "n", "services": []}]))
    assert p.models == [{"id": "m", "services": ["chat", "vision"]}, {"id": "n", "services": ["chat"]}]


def test_presets_services_and_catalog():
    assert PRESETS_BY_ID["elevenlabs"].services == ("tts",)
    assert PRESETS_BY_ID["elevenlabs"].adapter == "elevenlabs"
    assert "images" not in PRESETS_BY_ID["fireworks"].services
    for pid in ("deepinfra", "together", "fireworks", "elevenlabs"):
        assert pid in PRESETS_BY_ID
    for preset in PRESETS:
        assert preset.name and preset.description and preset.services, preset.id
        assert preset.key_url.startswith("https://"), preset.id
        assert not preset.pricing_url or preset.pricing_url.startswith("https://"), preset.id


def test_models_list_has_services_and_provider_name(tmp_path):
    async def scenario(client, mock):
        data = (await (await client.get("/v1/models", headers=AUTH)).json())["data"]
        by_id = {m["id"]: m for m in data}
        assert by_id["di/chat-1"]["services"] == ["chat", "vision"]
        assert by_id["di/flux-1"]["services"] == ["images"]
        assert by_id["di/chat-1"]["owned_by"] == "Deep Infra"
        assert set(by_id) == {"di/chat-1", "di/flux-1", "di/tts-1", "eleven/eleven_multilingual_v2"}
    with_gateway(tmp_path, scenario)


def test_nothing_ticked_means_no_models(tmp_path):
    async def scenario(client, mock):
        data = (await (await client.get("/v1/models", headers=AUTH)).json())["data"]
        assert data == []
        r = await client.post("/v1/chat/completions", headers=AUTH, json={"model": "di/chat-1", "messages": []})
        assert r.status == 404
    with_gateway(tmp_path, scenario, providers=lambda mock: [
        Provider(id="di", name="DI", base_url=mock.url("/v1"), preset="deepinfra"),
        Provider(id="eleven", name="EL", base_url=mock.url(), preset="elevenlabs")])


def test_chat_refuses_a_model_registered_for_images_only(tmp_path):
    async def scenario(client, mock):
        r = await client.post("/v1/chat/completions", headers=AUTH, json={"model": "di/flux-1", "messages": []})
        assert r.status == 404
    with_gateway(tmp_path, scenario)


def test_images_b64_passthrough_and_request_shape(tmp_path):
    async def scenario(client, mock):
        r = await client.post("/v1/images/generations", headers=AUTH, json={
            "model": "di/flux-1", "prompt": "a cat", "size": "512x512", "n": 1, "response_format": "b64_json"})
        body = await r.json()
        assert r.status == 200
        assert base64.b64decode(body["data"][0]["b64_json"]) == PNG
        _, headers, sent = mock.call("images")
        assert sent["model"] == "flux-1" and sent["size"] == "512x512" and sent["response_format"] == "b64_json"
        assert headers["Authorization"] == f"Bearer {SECRET}"
    with_gateway(tmp_path, scenario)


def test_images_url_result_must_be_https(tmp_path):
    mock = Mock()
    mock.image_reply = "url"

    async def scenario(client, mock):
        r = await client.post("/v1/images/generations", headers=AUTH, json={"model": "di/flux-1", "prompt": "a cat"})
        assert r.status == 502
        assert (await r.json())["error"]["code"] == "upstream_bad_response"
    with_gateway(tmp_path, scenario, mock=mock)


def test_download_image_fetches_https_and_sends_no_key(monkeypatch):
    from lm_provider import server

    class Resp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    async def fake_read(response, limit):
        return PNG

    class Session:
        def get(self, url, **kwargs):
            Session.kwargs = kwargs
            return Resp()

    monkeypatch.setattr(server, "read_limited", fake_read)
    provider = Provider(id="x", name="X", base_url="https://x.test")
    result = run(server.download_image(Session(), provider, "https://cdn.test/a.png"))
    assert base64.b64decode(result) == PNG
    assert "headers" not in Session.kwargs


def test_together_images_use_width_height(tmp_path):
    async def scenario(client, mock):
        r = await client.post("/v1/images/generations", headers=AUTH,
                              json={"model": "tg/flux-1", "prompt": "x", "size": "768x512"})
        assert r.status == 200
        sent = mock.call("images")[2]
        assert (sent["width"], sent["height"]) == (768, 512) and "size" not in sent
        assert sent["response_format"] == "base64"
    with_gateway(tmp_path, scenario, keys={"tg": SECRET}, providers=lambda mock: [Provider(
        id="tg", name="Together", base_url=mock.url("/v1"), preset="together",
        models=[{"id": "flux-1", "services": ["images"]}])])


def test_gpt_image_gets_no_response_format(tmp_path):
    async def scenario(client, mock):
        r = await client.post("/v1/images/generations", headers=AUTH, json={"model": "oa/gpt-image-1", "prompt": "x"})
        assert r.status == 200
        assert "response_format" not in mock.call("images")[2]
    with_gateway(tmp_path, scenario, keys={"oa": SECRET}, providers=lambda mock: [Provider(
        id="oa", name="OpenAI", base_url=mock.url("/v1"), preset="openai",
        models=[{"id": "gpt-image-1", "services": ["images"]}])])


def test_image_errors_never_leak_the_key(tmp_path):
    mock = Mock()
    mock.image_status = 400

    async def scenario(client, mock):
        r = await client.post("/v1/images/generations", headers=AUTH, json={"model": "di/flux-1", "prompt": "x"})
        text = await r.text()
        assert r.status == 400 and SECRET not in text and "error" in text
    with_gateway(tmp_path, scenario, mock=mock)


def test_speech_passes_audio_through(tmp_path):
    async def scenario(client, mock):
        r = await client.post("/v1/audio/speech", headers=AUTH, json={
            "model": "di/tts-1", "input": "hello", "voice": "af", "response_format": "wav", "speed": 1.1})
        assert r.status == 200 and await r.read() == MP3
        assert r.headers["Content-Type"].startswith("audio/wav")
        assert mock.call("speech")[2] == {"model": "tts-1", "input": "hello", "response_format": "wav",
                                          "voice": "af", "speed": 1.1}
    with_gateway(tmp_path, scenario)


def test_speech_needs_a_tts_registration(tmp_path):
    async def scenario(client, mock):
        r = await client.post("/v1/audio/speech", headers=AUTH, json={"model": "di/chat-1", "input": "hello"})
        assert r.status == 404
    with_gateway(tmp_path, scenario)


def test_elevenlabs_speech_uses_its_own_header_only(tmp_path):
    async def scenario(client, mock):
        r = await client.post("/v1/audio/speech", headers=AUTH, json={
            "model": "eleven/eleven_multilingual_v2", "input": "hi", "voice": "v1", "speed": 1.2})
        assert r.status == 200 and await r.read() == MP3 and r.headers["Content-Type"].startswith("audio/mpeg")
        _, headers, sent = mock.call("eleven_tts")
        assert headers["xi-api-key"] == XI_SECRET and "Authorization" not in headers
        assert sent["voice"] == "v1" and sent["text"] == "hi" and sent["model_id"] == "eleven_multilingual_v2"
        assert sent["voice_settings"] == {"speed": 1.2} and sent["query"] == {"output_format": "mp3_44100_128"}
    with_gateway(tmp_path, scenario)


def test_voices_elevenlabs_and_fixed_list(tmp_path):
    async def scenario(client, mock):
        el = await (await client.get("/v1/audio/voices?model=eleven/eleven_multilingual_v2", headers=AUTH)).json()
        oa = await (await client.get("/v1/audio/voices?model=oa/tts-1", headers=AUTH)).json()
        assert (await client.get("/v1/audio/voices", headers=AUTH)).status == 400
        assert el["data"][0] == {"id": "v1", "name": "Rachel", "language": "en", "gender": "female",
                                 "preview_url": "https://x/p.mp3"}
        assert el["data"][1]["language"] == "bg"
        assert "alloy" in [v["id"] for v in oa["data"]]
    with_gateway(tmp_path, scenario, keys={**KEYS, "oa": SECRET}, providers=lambda mock: providers_for(mock, [Provider(
        id="oa", name="OpenAI", base_url=mock.url("/v1"), preset="openai",
        models=[{"id": "tts-1", "services": ["tts"]}])]))


def test_elevenlabs_model_test_lists_only_tts_models():
    from lm_provider.upstream import list_models, new_session

    async def go():
        mock = Mock()
        async with mock.server:
            provider = Provider(id="eleven", name="EL", base_url=mock.url(), preset="elevenlabs")
            async with new_session() as session:
                return await list_models(session, provider, XI_SECRET)

    assert [m["id"] for m in run(go())] == ["eleven_multilingual_v2"]


def test_new_endpoints_need_the_token(tmp_path):
    async def scenario(client, mock):
        for method, path in (("post", "/v1/images/generations"), ("post", "/v1/audio/speech"),
                             ("get", "/v1/audio/voices")):
            assert (await getattr(client, method)(path)).status == 401
    with_gateway(tmp_path, scenario)
