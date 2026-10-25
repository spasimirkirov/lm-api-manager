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
import hmac
import json
import logging
import time

import aiohttp
from aiohttp import web

from . import __version__
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


async def models(request: web.Request):
    store = request.app[STORE_KEY]
    cache = request.app[MODELS_CACHE_KEY]
    session = request.app[SESSION_KEY]
    providers = store.providers()

    async def one(provider):
        cached = cache.get(provider.id)
        signature = (provider.base_url, tuple(provider.models), bool(store.key(provider.id)))
        if cached and cached[0] == signature and time.monotonic() - cached[1] < MODELS_CACHE_S:
            return provider, cached[2], None
        try:
            entries = await provider_models(session, provider, store.key(provider.id))
        except UpstreamError as error:
            return provider, [], error
        cache[provider.id] = (signature, time.monotonic(), entries)
        return provider, entries, None

    results = await asyncio.gather(*(one(p) for p in providers))
    data, errors = [], []
    for provider, entries, error in results:
        if error:
            log.warning("Model list of %s failed: %s", provider.id, error.code)
            errors.append({"provider": provider.id, "message": error.message, "code": error.code})
        # "free" (an extension) is set only when the provider itself reports a zero price.
        data.extend({"id": f"{provider.id}/{entry['id']}", "object": "model", "owned_by": provider.id,
                     "provider_name": provider.name, "free": entry["free"]} for entry in entries)
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
    if provider.models and upstream_model not in provider.models:
        return error_response(404, f'{provider.name} has no model "{upstream_model}" in its list.', "model_not_found")
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
