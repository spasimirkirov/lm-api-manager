"""Talking to the user's provider: request headers, the model list, error mapping, redaction."""

import json
import re

import aiohttp

CONNECT_TIMEOUT_S = 15
# Non-streaming completions can take minutes on reasoning models.
TOTAL_TIMEOUT_S = 300
# Streaming: the whole answer may take long, but a silent socket is a dead one.
STREAM_READ_TIMEOUT_S = 120
MAX_ERROR_BODY = 64 * 1024
MAX_MODELS_BODY = 16 * 1024 * 1024

# Strings that look like API keys of the common providers, scrubbed from anything we pass on.
KEY_LIKE = re.compile(r"\b(?:sk|gsk|xai|sk-ant|sk-or)-[A-Za-z0-9_\-*.]{8,}|\bAIza[0-9A-Za-z_\-]{20,}")


class UpstreamError(Exception):
    """A provider failure already turned into a safe, OpenAI-shaped error."""

    def __init__(self, status: int, message: str, code: str, err_type: str = "api_error"):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.err_type = err_type

    def body(self) -> dict:
        return {"error": {"message": self.message, "type": self.err_type, "code": self.code}}


async def read_limited(response, limit: int) -> bytes:
    """The whole body up to `limit` bytes (StreamReader.read(n) returns only what has arrived)."""
    chunks, size = [], 0
    async for chunk in response.content.iter_any():
        chunks.append(chunk)
        size += len(chunk)
        if size >= limit:
            break
    return b"".join(chunks)[:limit]


def redact(text: str, keys=()) -> str:
    text = str(text or "")
    for key in keys:
        if key and len(key) >= 4:
            text = text.replace(key, "[redacted]")
    return KEY_LIKE.sub("[redacted]", text)


def auth_headers(provider, key: str) -> dict:
    headers = {"Authorization": f"Bearer {key}", **provider.extra_headers}
    if provider.key_header:
        headers[provider.key_header] = key
    return headers


def upstream_url(provider, path: str) -> str:
    return f"{provider.base_url}/{path.lstrip('/')}"


def _upstream_message(raw: bytes, keys) -> str:
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return ""
    error = data.get("error") if isinstance(data, dict) else None
    message = error.get("message") if isinstance(error, dict) else (error if isinstance(error, str) else "")
    if not message and isinstance(data, dict):
        message = data.get("message") or ""
    return redact(str(message)[:500], keys)


def map_status(provider, status: int, raw: bytes, keys) -> UpstreamError:
    """Upstream status to a clear error. Auth failures never quote the upstream text: some
    providers echo part of the key in it."""
    name = provider.name
    if status == 401:
        return UpstreamError(401, f"{name} rejected the API key. Check it in the LM OpenAI Provider settings.",
                             "invalid_api_key", "authentication_error")
    if status == 402:
        return UpstreamError(402, f"{name} needs payment: check the balance or billing of your account.",
                             "insufficient_quota", "billing_error")
    if status == 403:
        return UpstreamError(403, f"{name} refused the request: the key has no access to this model or endpoint.",
                             "permission_denied", "permission_error")
    if status == 429:
        detail = _upstream_message(raw, keys)
        message = f"Rate limited by {name}." + (f" {detail}" if detail else "")
        return UpstreamError(429, message, "rate_limited", "rate_limit_error")
    if status >= 500:
        return UpstreamError(502, f"{name} is unavailable right now (HTTP {status}). Try again later.",
                             "upstream_unavailable")
    detail = _upstream_message(raw, keys) or f"HTTP {status}"
    return UpstreamError(status, f"{name}: {detail}", "upstream_error", "invalid_request_error")


def connection_error(provider, error: Exception) -> UpstreamError:
    if isinstance(error, TimeoutError):
        return UpstreamError(504, f"{provider.name} did not answer in time.", "upstream_timeout")
    return UpstreamError(502, f"Could not reach {provider.name}. Check the base URL and the connection.",
                         "upstream_unreachable")


def new_session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=None, connect=CONNECT_TIMEOUT_S),
        # No cookies kept between providers or requests.
        cookie_jar=aiohttp.DummyCookieJar(),
        trust_env=True,
    )


def is_free(entry) -> bool:
    """True only when the provider itself says so: a ":free" model id, or a price of zero
    for both input and output (OpenRouter's "pricing" field). Nothing is guessed."""
    if not isinstance(entry, dict):
        return False
    if str(entry.get("id", "")).endswith(":free"):
        return True
    pricing = entry.get("pricing")
    if not isinstance(pricing, dict) or "prompt" not in pricing or "completion" not in pricing:
        return False
    try:
        return float(pricing["prompt"]) == 0 and float(pricing["completion"]) == 0
    except (TypeError, ValueError):
        return False


async def list_models(session: aiohttp.ClientSession, provider, key: str) -> list:
    """Models the provider offers (ids without the prefix): [{"id", "free"}], sorted."""
    if not key:
        raise UpstreamError(401, f"{provider.name} has no API key yet.", "missing_api_key", "authentication_error")
    try:
        async with session.get(
            upstream_url(provider, "models"),
            headers=auth_headers(provider, key),
            timeout=aiohttp.ClientTimeout(total=30, connect=CONNECT_TIMEOUT_S),
        ) as response:
            raw = await read_limited(response, MAX_MODELS_BODY)
            if response.status != 200:
                raise map_status(provider, response.status, raw[:MAX_ERROR_BODY], [key])
    except UpstreamError:
        raise
    except (aiohttp.ClientError, TimeoutError) as error:
        raise connection_error(provider, error) from None
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise UpstreamError(502, f"{provider.name} sent a model list that is not JSON.", "upstream_bad_response") from None
    entries = data.get("data") if isinstance(data, dict) else data
    models = {}
    for entry in entries if isinstance(entries, list) else []:
        model_id = entry.get("id") if isinstance(entry, dict) else entry
        if isinstance(model_id, str) and model_id:
            # Gemini lists "models/gemini-2.5-flash"; the chat endpoint takes either form.
            models[model_id] = models.get(model_id, False) or is_free(entry)
    return [{"id": model_id, "free": models[model_id]} for model_id in sorted(models)]


async def provider_models(session: aiohttp.ClientSession, provider, key: str) -> list:
    """The models Pal Engine may use: the provider's list, cut to the allow-list when one is
    set. If the provider cannot be asked, the allow-list alone still works."""
    if not provider.models:
        return await list_models(session, provider, key)
    allowed = set(provider.models)
    try:
        offered = {m["id"]: m for m in await list_models(session, provider, key)}
    except UpstreamError:
        offered = {}
    return [offered.get(model_id) or {"id": model_id, "free": model_id.endswith(":free")}
            for model_id in sorted(allowed)]
