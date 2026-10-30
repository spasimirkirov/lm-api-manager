"""The local gateway (--serve): an OpenAI-compatible HTTP server on 127.0.0.1.

Security model:
- binds to 127.0.0.1 on a port the OS picks;
- every request needs "Authorization: Bearer <session token>" (the token comes from the
  PAL_ENGINE_SESSION_TOKEN environment variable, new on every start);
- any request with an Origin header is refused and no CORS headers are sent, so a web page
  open in a browser cannot use the gateway;
- keys are added only to the upstream request of the provider the model prefix names.
"""

import asyncio
import base64
import binascii
import hmac
import json
import logging
import time

import aiohttp
from aiohttp import web

from . import __version__, elevenlabs
from .config import migrate_legacy_ticks
from .presets import preset_for
from .upstream import (
    MAX_ERROR_BODY,
    STREAM_READ_TIMEOUT_S,
    TOTAL_TIMEOUT_S,
    UpstreamError,
    auth_headers,
    connection_error,
    provider_models,
    read_limited,
    map_status,
    new_session,
    upstream_url,
)

log = logging.getLogger("lm_provider")

MAX_REQUEST_BYTES = 20 * 1024 * 1024  # chat requests can carry base64 images
MODELS_CACHE_S = 600
IMAGES_TIMEOUT_S = 120
SPEECH_TIMEOUT_S = 120
MAX_IMAGE_BYTES = 40 * 1024 * 1024
MAX_AUDIO_BYTES = 64 * 1024 * 1024
AUDIO_TYPES = {"mp3": "audio/mpeg", "wav": "audio/wav"}
STORE_KEY = web.AppKey("store", object)
TOKEN_KEY = web.AppKey("token", str)
SESSION_KEY = web.AppKey("session", aiohttp.ClientSession)
MODELS_CACHE_KEY = web.AppKey("models_cache", dict)
# Upstream response headers worth passing on; the rest (cookies, server ids) stay behind.
PASS_HEADERS = ("Content-Type", "Cache-Control", "x-request-id", "openai-processing-ms")


def error_response(status: int, message: str, code: str, err_type: str = "invalid_request_error"):
    return web.json_response({"error": {"message": message, "type": err_type, "code": code}}, status=status)


def upstream_error_response(error: UpstreamError):
    return web.json_response(error.body(), status=error.status)


def split_model(model: str):
    """"deepseek/deepseek-chat" -> ("deepseek", "deepseek-chat"). Only the first slash splits:
    OpenRouter models have their own ("openrouter/openai/gpt-4o")."""
    provider_id, sep, upstream_model = (model or "").partition("/")
    if not sep or not provider_id or not upstream_model:
        return None, None
    return provider_id, upstream_model


@web.middleware
async def guard(request: web.Request, handler):
    if "Origin" in request.headers:
        return error_response(403, "Browser requests are not allowed.", "origin_forbidden", "permission_error")
    expected = f"Bearer {request.app[TOKEN_KEY]}".encode()
    given = request.headers.get("Authorization", "").encode()
    if not hmac.compare_digest(given, expected):
        return error_response(401, "Missing or wrong session token.", "unauthorized", "authentication_error")
    return await handler(request)


async def health(request: web.Request):
    store = request.app[STORE_KEY]
    return web.json_response({"status": "ok", "version": __version__, "providers": len(store.providers())})


def check_model(provider, upstream_model: str, service: str):
    """An error response unless this model is registered for `service`. Nothing registered
    means no models, except a provider saved before 0.3.1 that is not migrated yet."""
    services = provider.model_services(upstream_model)
    if services is None:
        if not provider.models and service == "chat" and provider.legacy_all_chat:
            return None
        return error_response(404, f'{provider.name} has no model "{upstream_model}" in its list.', "model_not_found")
    if service not in services:
        return error_response(404, f'"{upstream_model}" is not set up for {service} in {provider.name}.',
                              "model_not_found")
    return None


async def read_json_body(request: web.Request):
    """(body, None) or (None, error response)."""
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return None, error_response(400, "The request body is not valid JSON.", "invalid_json")
    if not isinstance(body, dict):
        return None, error_response(400, "The request body must be a JSON object.", "invalid_json")
    return body, None


def resolve(request: web.Request, model, service: str):
    """(provider, upstream model, key, None) or (None, None, None, error response)."""
    store = request.app[STORE_KEY]
    provider_id, upstream_model = split_model(model)
    if not provider_id:
        return None, None, None, error_response(400, 'The model must look like "<provider>/<model>".', "invalid_model")
    provider = store.get(provider_id)
    if not provider:
        return None, None, None, error_response(404, f'No provider "{provider_id}" is set up.', "provider_not_found")
    problem = check_model(provider, upstream_model, service)
    if problem:
        return None, None, None, problem
    key = store.key(provider.id)
    if not key:
        return None, None, None, upstream_error_response(UpstreamError(
            401, f"{provider.name} has no API key yet.", "missing_api_key", "authentication_error"))
    return provider, upstream_model, key, None


def gateway_error(provider, error, what: str):
    if isinstance(error, UpstreamError):
        log.warning("%s via %s failed: %s", what, provider.id, error.code)
        return upstream_error_response(error)
    log.warning("%s via %s: %s", what, provider.id, type(error).__name__)
    return upstream_error_response(connection_error(provider, error))


def parse_size(size):
    try:
        width, height = str(size or "1024x1024").lower().split("x")
        return int(width), int(height)
    except ValueError:
        return 1024, 1024


async def download_image(session, provider, url) -> str:
    """A provider that answers with a URL: fetch it (https only, no key sent) -> base64."""
    if not isinstance(url, str) or not url.startswith("https://"):
        raise UpstreamError(502, f"{provider.name} sent an image address that is not https.", "upstream_bad_response")
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=60, connect=15)) as response:
            if response.status != 200:
                raise UpstreamError(502, f"Could not download the image from {provider.name}.", "upstream_bad_response")
            raw = await read_limited(response, MAX_IMAGE_BYTES)
    except UpstreamError:
        raise
    except (aiohttp.ClientError, TimeoutError) as error:
        raise connection_error(provider, error) from None
    return base64.b64encode(raw).decode("ascii")


async def images_generations(request: web.Request):
    store = request.app[STORE_KEY]
    body, problem = await read_json_body(request)
    if problem:
        return problem
    provider, upstream_model, key, problem = resolve(request, body.get("model"), "images")
    if problem:
        return problem
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return error_response(400, "The prompt is missing.", "invalid_prompt")
    if provider.adapter:
        return error_response(400, f"{provider.name} does not make pictures.", "unsupported_service")
    n = body.get("n") if isinstance(body.get("n"), int) and 1 <= body.get("n") <= 4 else 1
    size = body.get("size") or "1024x1024"
    upstream = {"model": upstream_model, "prompt": prompt, "n": n}
    preset = preset_for(provider.preset)
    if preset and preset.images_style == "together":
        upstream["width"], upstream["height"] = parse_size(size)
        upstream["response_format"] = "base64"
    else:
        upstream["size"] = str(size)
        # gpt-image models always answer b64_json and refuse the parameter.
        if not upstream_model.lower().startswith("gpt-image"):
            upstream["response_format"] = "b64_json"
    session = request.app[SESSION_KEY]
    started = time.monotonic()
    try:
        async with session.post(upstream_url(provider, "images/generations"), data=json.dumps(upstream),
                                headers={**auth_headers(provider, key), "Content-Type": "application/json"},
                                timeout=aiohttp.ClientTimeout(total=IMAGES_TIMEOUT_S, connect=15)) as response:
            raw = await read_limited(response, MAX_IMAGE_BYTES * 2)
            if response.status != 200:
                return upstream_error_response(map_status(provider, response.status, raw[:MAX_ERROR_BODY],
                                                          store.all_keys()))
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise UpstreamError(502, f"{provider.name} sent an answer that is not JSON.", "upstream_bad_response") from None
        items = data.get("data") if isinstance(data, dict) else None
        pictures = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("b64_json"), str) and item["b64_json"]:
                pictures.append({"b64_json": item["b64_json"]})
            elif item.get("url"):
                pictures.append({"b64_json": await download_image(session, provider, item["url"])})
        if not pictures:
            raise UpstreamError(502, f"{provider.name} sent no picture.", "upstream_bad_response")
    except (UpstreamError, aiohttp.ClientError, TimeoutError) as error:
        return gateway_error(provider, error, "Image")
    log.info("Image via %s done in %.1f s", provider.id, time.monotonic() - started)
    return web.json_response({"created": int(time.time()), "data": pictures})


def audio_from_json(provider, raw: bytes) -> bytes:
    """Some providers wrap the audio in JSON as base64: take it, or fail clearly."""
    try:
        data = json.loads(raw.decode("utf-8"))
        value = (data.get("audio") or data.get("b64_json")) if isinstance(data, dict) else None
        if isinstance(value, str) and value:
            return base64.b64decode(value.split(",", 1)[-1])
    except (ValueError, binascii.Error):
        pass
    raise UpstreamError(502, f"{provider.name} did not send audio.", "upstream_bad_response")


async def audio_speech(request: web.Request):
    store = request.app[STORE_KEY]
    body, problem = await read_json_body(request)
    if problem:
        return problem
    provider, upstream_model, key, problem = resolve(request, body.get("model"), "tts")
    if problem:
        return problem
    text = body.get("input")
    if not isinstance(text, str) or not text.strip():
        return error_response(400, "The input text is missing.", "invalid_input")
    voice = body.get("voice") if isinstance(body.get("voice"), str) else ""
    fmt = body.get("response_format") if body.get("response_format") in AUDIO_TYPES else "mp3"
    speed = body.get("speed")
    session = request.app[SESSION_KEY]
    started = time.monotonic()
    try:
        if provider.adapter == "elevenlabs":
            if not voice:
                return error_response(400, "ElevenLabs needs a voice id.", "invalid_voice")
            audio, content_type = await elevenlabs.speech(session, provider, key, upstream_model, text, voice, fmt, speed)
        else:
            upstream = {"model": upstream_model, "input": text, "response_format": fmt}
            if voice:
                upstream["voice"] = voice
            if isinstance(speed, (int, float)) and not isinstance(speed, bool):
                upstream["speed"] = speed
            async with session.post(upstream_url(provider, "audio/speech"), data=json.dumps(upstream),
                                    headers={**auth_headers(provider, key), "Content-Type": "application/json"},
                                    timeout=aiohttp.ClientTimeout(total=SPEECH_TIMEOUT_S, connect=15)) as response:
                raw = await read_limited(response, MAX_AUDIO_BYTES)
                if response.status != 200:
                    return upstream_error_response(map_status(provider, response.status, raw[:MAX_ERROR_BODY],
                                                              store.all_keys()))
                upstream_type = response.headers.get("Content-Type", "").lower()
            if "json" in upstream_type:
                raw = audio_from_json(provider, raw)
            if not raw:
                raise UpstreamError(502, f"{provider.name} did not send audio.", "upstream_bad_response")
            audio, content_type = raw, AUDIO_TYPES[fmt]
    except (UpstreamError, aiohttp.ClientError, TimeoutError) as error:
        return gateway_error(provider, error, "Speech")
    log.info("Speech via %s done in %.1f s", provider.id, time.monotonic() - started)
    return web.Response(body=audio, content_type=content_type)


async def audio_voices(request: web.Request):
    provider, _model, key, problem = resolve(request, request.query.get("model", ""), "tts")
    if problem:
        return problem
    if provider.adapter == "elevenlabs":
        try:
            voices = await elevenlabs.list_voices(request.app[SESSION_KEY], provider, key)
        except (UpstreamError, aiohttp.ClientError, TimeoutError) as error:
            return gateway_error(provider, error, "Voices")
    else:
        preset = preset_for(provider.preset)
        voices = [{"id": name, "name": name} for name in (preset.voices if preset else ())]
    return web.json_response({"data": voices})


async def models(request: web.Request):
    store = request.app[STORE_KEY]
    cache = request.app[MODELS_CACHE_KEY]
    session = request.app[SESSION_KEY]
    providers = store.providers()

    async def one(provider):
        cached = cache.get(provider.id)
        signature = (provider.base_url, tuple((m["id"], tuple(m["services"])) for m in provider.models),
                     bool(store.key(provider.id)))
        if cached and cached[0] == signature and time.monotonic() - cached[1] < MODELS_CACHE_S:
            return provider, cached[2], None
        try:
            entries = await provider_models(session, provider, store.key(provider.id))
        except UpstreamError as error:
            return provider, [], error
        if provider.legacy_all_chat and entries:
            try:  # once: the old "no ticks = all as chat" becomes real ticks
                migrate_legacy_ticks(store.path, provider.id, [e["id"] for e in entries])
            except OSError:
                log.warning("Could not save the migrated models of %s", provider.id)
        cache[provider.id] = (signature, time.monotonic(), entries)
        return provider, entries, None

    results = await asyncio.gather(*(one(p) for p in providers))
    data, errors = [], []
    for provider, entries, error in results:
        if error:
            log.warning("Model list of %s failed: %s", provider.id, error.code)
            errors.append({"provider": provider.id, "message": error.message, "code": error.code})
        # "free" (an extension) is set only when the provider itself reports a zero price.
        data.extend({"id": f"{provider.id}/{entry['id']}", "object": "model", "owned_by": provider.name,
                     "provider_id": provider.id, "provider_name": provider.name, "free": entry["free"],
                     "services": entry["services"]} for entry in entries)
    # "errors" is an extension: Pal Engine shows which provider failed instead of an empty list.
    return web.json_response({"object": "list", "data": data, "errors": errors})


async def chat_completions(request: web.Request):
    store = request.app[STORE_KEY]
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return error_response(400, "The request body is not valid JSON.", "invalid_json")
    if not isinstance(body, dict):
        return error_response(400, "The request body must be a JSON object.", "invalid_json")

    provider_id, upstream_model = split_model(body.get("model"))
    if not provider_id:
        return error_response(400, 'The model must look like "<provider>/<model>".', "invalid_model")
    provider = store.get(provider_id)
    if not provider:
        return error_response(404, f'No provider "{provider_id}" is set up.', "provider_not_found")
    problem = check_model(provider, upstream_model, "chat")
    if problem:
        return problem
    key = store.key(provider.id)
    if not key:
        return upstream_error_response(UpstreamError(401, f"{provider.name} has no API key yet.",
                                                     "missing_api_key", "authentication_error"))

    body["model"] = upstream_model
    stream = bool(body.get("stream"))
    headers = {**auth_headers(provider, key), "Content-Type": "application/json",
               "Accept": "text/event-stream" if stream else "application/json"}
    timeout = (aiohttp.ClientTimeout(total=None, connect=15, sock_read=STREAM_READ_TIMEOUT_S) if stream
               else aiohttp.ClientTimeout(total=TOTAL_TIMEOUT_S, connect=15))
    session = request.app[SESSION_KEY]
    started = time.monotonic()
    response = None
    try:
        async with session.post(upstream_url(provider, "chat/completions"), data=json.dumps(body),
                                headers=headers, timeout=timeout) as upstream:
            if upstream.status != 200:
                raw = await read_limited(upstream, MAX_ERROR_BODY)
                error = map_status(provider, upstream.status, raw, store.all_keys())
                log.warning("Chat via %s failed: HTTP %s (%s)", provider.id, upstream.status, error.code)
                return upstream_error_response(error)
            response = web.StreamResponse(status=200)
            for name in PASS_HEADERS:
                if name in upstream.headers:
                    response.headers[name] = upstream.headers[name]
            if stream:
                response.headers["Cache-Control"] = "no-cache"
            await response.prepare(request)
            # Chunk by chunk, as they arrive: no buffering of the stream.
            async for chunk in upstream.content.iter_any():
                await response.write(chunk)
            await response.write_eof()
            log.info("Chat via %s done in %.1f s", provider.id, time.monotonic() - started)
            return response
    except ConnectionResetError as error:
        if response is None:
            # Nothing written to Pal Engine yet: the provider dropped the connection.
            return upstream_error_response(connection_error(provider, error))
        # Pal Engine stopped reading (the user cancelled), or the provider cut the stream.
        log.info("Chat via %s: stream ended early", provider.id)
        return response
    except (aiohttp.ClientError, TimeoutError) as error:
        log.warning("Chat via %s: %s", provider.id, type(error).__name__)
        if response is not None:
            # The answer had started: the client sees a cut stream, the status is already sent.
            return response
        return upstream_error_response(connection_error(provider, error))


async def _open_session(app: web.Application):
    app[SESSION_KEY] = new_session()
    yield
    await app[SESSION_KEY].close()


def create_app(store, token: str) -> web.Application:
    if not token or len(token) < 32:
        raise ValueError("The session token must be at least 32 characters")
    app = web.Application(middlewares=[guard], client_max_size=MAX_REQUEST_BYTES)
    app[STORE_KEY] = store
    app[TOKEN_KEY] = token
    app[MODELS_CACHE_KEY] = {}
    app.cleanup_ctx.append(_open_session)
    app.router.add_get("/health", health)
    app.router.add_get("/v1/models", models)
    app.router.add_post("/v1/chat/completions", chat_completions)
    app.router.add_post("/v1/images/generations", images_generations)
    app.router.add_post("/v1/audio/speech", audio_speech)
    app.router.add_get("/v1/audio/voices", audio_voices)
    return app


async def serve(store, token: str, stop: asyncio.Event, host: str = "127.0.0.1") -> None:
    runner = web.AppRunner(create_app(store, token), access_log=None, handle_signals=False)
    await runner.setup()
    site = web.TCPSite(runner, host, 0)
    await site.start()
    port = runner.addresses[0][1]
    # The one line Pal Engine waits for.
    print(json.dumps({"event": "ready", "port": port, "version": __version__, "protocol": 1}), flush=True)
    log.info("Listening on %s:%s", host, port)
    try:
        await stop.wait()
    finally:
        await runner.cleanup()
