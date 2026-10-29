"""ElevenLabs is not OpenAI-compatible: a small adapter for text-to-speech only.

Auth is the `xi-api-key` header (the key is sent in no other header). Endpoints, from the
official API reference (see README, Sources):
  POST /v1/text-to-speech/{voice_id}?output_format=...   body {text, model_id, voice_settings}
  GET  /v2/voices                                        voices (paged)
  GET  /v1/models                                        models (can_do_text_to_speech)
"""

import json
from urllib.parse import quote

import aiohttp

from .upstream import (
    CONNECT_TIMEOUT_S,
    MAX_ERROR_BODY,
    MAX_MODELS_BODY,
    UpstreamError,
    connection_error,
    map_status,
    read_limited,
    upstream_url,
)

SPEECH_TIMEOUT_S = 120
MAX_AUDIO_BYTES = 64 * 1024 * 1024
# The contract asks for mp3 or wav; these ElevenLabs output formats match.
OUTPUT_FORMATS = {"mp3": ("mp3_44100_128", "audio/mpeg"), "wav": ("wav_24000", "audio/wav")}
VOICE_PAGES = 5


def headers(key: str) -> dict:
    return {"xi-api-key": key}


async def list_models(session, provider, key: str) -> list:
    data = await _get_json(session, provider, key, "v1/models", MAX_MODELS_BODY)
    models = {}
    for entry in data if isinstance(data, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("model_id"), str) and entry.get("can_do_text_to_speech", True):
            models[entry["model_id"]] = True
    return [{"id": model_id, "free": False} for model_id in sorted(models)]


async def list_voices(session, provider, key: str) -> list:
    voices, token = [], None
    for _ in range(VOICE_PAGES):
        path = "v2/voices?page_size=100" + (f"&next_page_token={quote(token)}" if token else "")
        data = await _get_json(session, provider, key, path, MAX_MODELS_BODY)
        if not isinstance(data, dict):
            break
        for voice in data.get("voices") or []:
            if isinstance(voice, dict) and isinstance(voice.get("voice_id"), str):
                voices.append(_voice(voice))
        token = data.get("next_page_token")
        if not data.get("has_more") or not token:
            break
    return voices


def _voice(voice: dict) -> dict:
    labels = voice.get("labels") if isinstance(voice.get("labels"), dict) else {}
    language = labels.get("language")
    if not language:
        verified = voice.get("verified_languages")
        if isinstance(verified, list) and verified and isinstance(verified[0], dict):
            language = verified[0].get("language")
    item = {"id": voice["voice_id"], "name": str(voice.get("name") or voice["voice_id"])}
    for field, value in (("language", language), ("gender", labels.get("gender")),
                         ("preview_url", voice.get("preview_url"))):
        if isinstance(value, str) and value:
            item[field] = value
    return item


async def speech(session, provider, key: str, model: str, text: str, voice: str, fmt: str, speed=None):
    """(audio bytes, content type). Raises UpstreamError."""
    output_format, content_type = OUTPUT_FORMATS.get(fmt or "mp3", OUTPUT_FORMATS["mp3"])
    body = {"text": text, "model_id": model}
    if isinstance(speed, (int, float)) and not isinstance(speed, bool):
        body["voice_settings"] = {"speed": speed}
    url = upstream_url(provider, f"v1/text-to-speech/{quote(voice, safe='')}") + f"?output_format={output_format}"
    try:
        async with session.post(url, data=json.dumps(body), headers={**headers(key), "Content-Type": "application/json"},
                                timeout=aiohttp.ClientTimeout(total=SPEECH_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)) as response:
            if response.status != 200:
                raise map_status(provider, response.status, await read_limited(response, MAX_ERROR_BODY), [key])
            audio = await read_limited(response, MAX_AUDIO_BYTES)
    except UpstreamError:
        raise
    except (aiohttp.ClientError, TimeoutError) as error:
        raise connection_error(provider, error) from None
    return audio, content_type


async def _get_json(session, provider, key: str, path: str, limit: int):
    try:
        async with session.get(upstream_url(provider, path), headers=headers(key),
                               timeout=aiohttp.ClientTimeout(total=30, connect=CONNECT_TIMEOUT_S)) as response:
            raw = await read_limited(response, limit)
            if response.status != 200:
                raise map_status(provider, response.status, raw[:MAX_ERROR_BODY], [key])
    except UpstreamError:
        raise
    except (aiohttp.ClientError, TimeoutError) as error:
        raise connection_error(provider, error) from None
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise UpstreamError(502, f"{provider.name} sent an answer that is not JSON.", "upstream_bad_response") from None
