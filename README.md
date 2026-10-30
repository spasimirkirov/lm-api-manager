# LM API Manager

A small local gateway for Pal Engine that lets you use **your own** AI provider accounts for
chat, vision, pictures and voices (OpenAI, DeepSeek, OpenRouter, Groq, Mistral, Ollama Cloud,
Google Gemini, Anthropic, DeepInfra, Together AI, Fireworks AI, ElevenLabs, or any other
OpenAI-compatible endpoint). You keep your API keys in this add-on and Pal Engine never sees
them. Pal Engine sends its requests to this add-on, which adds your key and forwards them to
the provider you chose.

This is a separate add-on (an Integration), not part of Pal Engine, and is released from this
repository. Usage is billed by your provider to your account, not by Pal Engine.

## Install in Pal Engine

The easy way: in Pal Engine, open **Settings › Integrations** and click **Download** next to
**LM API Manager**. Pal Engine downloads the latest release from this repository and installs it.

By hand (Add Zip):

1. Download `LmApiManager-<version>.zip` from the
   [latest release](https://github.com/spasimirkirov/lm-api-manager/releases/latest).
2. In Pal Engine, open **Settings › Integrations**, click **Add Zip**, choose the zip and click
   **Save**.

Then:

1. Click **Open settings** on the integration. The **LM API Manager** window opens (maximized):
   your providers on the left, the selected one on the right. Click **Add provider**, paste its API
   key and click **Test connection**.
2. In **Settings › AI Models**, pick **LM API Manager** as the chat provider and choose a model.

Updates: Pal Engine checks this repository for new releases. When one is out, **Settings ›
Integrations** shows an **Update** button next to the integration. Needs an internet connection.

Until 0.2.0 this add-on was called **LM OpenAI Provider** (repository `lm-open-ai-provider`). An
installed LM OpenAI Provider updates to LM API Manager in place: the first start copies the
provider list from `%APPDATA%\PalEngine\lm-open-ai-provider` (the old file stays) and each saved
key moves to the new Credential Manager entry (`PalEngine.LmApiManager`) when it is first read.

## Usage

```
LmApiManager.exe --settings      Open the LM API Manager window (also: no arguments)
LmApiManager.exe --serve [--parent-pid PID]
                                 Run the local server (started by Pal Engine)
LmApiManager.exe --version       Print the version (to a pipe: the exe has no console window)
```

### Providers

The settings window offers these presets (they fill the base URL; each shows a short description,
its services and a link to get an API key) and **Custom** for anything else. Services are only
enabled where the provider's official documentation confirms them (sources below).

| Preset | Base URL | Services |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | chat, vision, voice (TTS), pictures |
| DeepSeek | `https://api.deepseek.com/v1` | chat |
| OpenRouter | `https://openrouter.ai/api/v1` | chat, vision |
| Groq | `https://api.groq.com/openai/v1` | chat, vision, voice (TTS) |
| Mistral | `https://api.mistral.ai/v1` | chat, vision |
| Ollama Cloud | `https://ollama.com/v1` | chat, vision |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | chat, vision, pictures |
| Anthropic | `https://api.anthropic.com/v1` | chat, vision |
| DeepInfra | `https://api.deepinfra.com/v1/openai` | chat, vision, voice (TTS), pictures |
| Together AI | `https://api.together.ai/v1` | chat, vision, voice (TTS), pictures |
| Fireworks AI | `https://api.fireworks.ai/inference/v1` | chat, vision |
| ElevenLabs | `https://api.elevenlabs.io` | voice (TTS) only (own adapter, not OpenAI-compatible) |

Models appear in Pal Engine as `<provider id>/<model>`, e.g. `deepseek/deepseek-chat` or
`openrouter/openai/gpt-4o`.

### Registering models

In the settings, **Test connection** loads the provider's models into one table: a row per
model, with the columns **Model | Chat | Vision | Voice (TTS) | Pictures | Price**. Click a
checkbox to tick that service for the model; vision always comes with chat. Clicking the model
name ticks the services guessed from it (`flux`, `sdxl`, `stable-diffusion` = pictures; `tts`,
`kokoro`, `eleven` = voice; `-vl`, `vision`, `gpt-4o`, `gemini` = chat + vision; anything else =
chat) and clicks again to untick it. Nothing is ticked on its own. **Filter**, **Show ticked
only** and **Clear ticks** work on the table, and the count under it reads "N of M ticked".

- A model counts as ticked when at least one service is checked, and **only ticked models are
  given to Pal Engine** (`/v1/models` and routing). Nothing ticked means no models. (Before 0.3.1
  a chat provider with nothing ticked gave all its models; that rule is gone.)
- Update from 0.3.0 or older: a saved provider with no ticks keeps working. The first time the
  model list is requested (or Test connection is used in its window) all its known models are
  ticked for chat once, and that is saved, so nothing breaks.
- A model is marked **(free)** only when the provider itself reports it, with a `:free` id or a
  zero price in its model list (OpenRouter does).
- An older `providers.json` with a plain list of model ids is read as chat models; it is rewritten
  in the new `models: [{"id", "services"}]` form on the next save.

The provider list (no keys) is saved in `%APPDATA%\PalEngine\lm-api-manager\providers.json`.

## Security model

- **Keys** live only in Windows Credential Manager (through `keyring`, protected by DPAPI). They
  are never written to a file, a log, stdout or stderr, and are never sent back to Pal Engine.
  The settings window shows only the last four characters of a saved key.
- A key is sent only in the request to its own provider. The base URL must use `https://`
  (plain `http://` is accepted only for this computer).
- **The server** listens on `127.0.0.1` only, on a port picked by the system.
- Every request must carry `Authorization: Bearer <session token>`. Pal Engine creates a new
  random token on every start and passes it in the `PAL_ENGINE_SESSION_TOKEN` environment
  variable (not on the command line, where other programs could read it). The server removes
  it from its environment after reading it.
- Requests with an `Origin` header are refused and no CORS headers are sent, so a web page in
  your browser cannot use the gateway to spend your credit.
- Provider error messages are rewritten into short, clear ones. Anything that looks like an
  API key is removed before an error reaches Pal Engine or a log.
- The server exits when Pal Engine does (its stdin closes or the parent process ends).

## Local server protocol

On start the server prints one line to stdout:

```json
{"event": "ready", "port": 50123, "version": "0.3.1", "protocol": 1}
```

| Endpoint | Purpose |
|---|---|
| `GET /health` | `{"status": "ok", "version": "...", "providers": 2}` |
| `GET /v1/models` | Registered models of every provider: `{id: "<provider id>/<model>", owned_by: "<provider name>", services: ["chat"\|"vision"\|"tts"\|"images"]}`, `free` when the provider reports a zero price; `errors` lists providers whose list failed |
| `POST /v1/chat/completions` | Routed by the model prefix (model must have `chat`); streaming (SSE) is passed through chunk by chunk |
| `POST /v1/images/generations` | `{model, prompt, size: "WxH", n, response_format}`; model must have `images`; always answers `{data: [{b64_json}]}` (a URL result is downloaded, https only, no key sent). Timeout 120 s |
| `POST /v1/audio/speech` | `{model, input, voice, response_format: "mp3"\|"wav", speed?}`; model must have `tts`; the body is the audio (`audio/mpeg` or `audio/wav`). Timeout 120 s |
| `GET /v1/audio/voices?model=<id>` | `{data: [{id, name, language?, gender?, preview_url?}]}`: ElevenLabs from its API, OpenAI/Groq/Together from a fixed list, others empty |

Errors use the OpenAI shape `{"error": {"message", "type", "code"}}`, for example
`invalid_api_key`, `insufficient_quota`, `rate_limited`, `upstream_unavailable`,
`upstream_unreachable`, `missing_api_key`, `provider_not_found`, `model_not_found`,
`upstream_bad_response`. Keys never appear in an error message.

### Sources (official documentation)

- OpenAI: [text to speech](https://developers.openai.com/api/docs/guides/text-to-speech) (`/v1/audio/speech`, voices, formats), [image generation](https://developers.openai.com/api/docs/guides/image-generation) (Image API, `b64_json`). The API reference pages returned 403 here, so `response_format` handling for DALL-E vs `gpt-image` is from the API's known behaviour, not re-checked.
- DeepSeek: [API docs](https://api-docs.deepseek.com/) (chat only).
- Groq: [text to speech](https://console.groq.com/docs/text-to-speech) (`/openai/v1/audio/speech`, Orpheus models; voice list is only the docs' examples).
- Google Gemini: [OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai) (chat, `images/generations` with `b64_json`, models; no speech).
- OpenRouter: [image generation](https://openrouter.ai/docs/features/multimodal/image-generation) (its own `/api/v1/images`, not the OpenAI path: pictures not enabled).
- DeepInfra: [llms.txt index](https://docs.deepinfra.com/llms.txt), [OpenAI Images Generations](https://docs.deepinfra.com/api-reference/image-generation/openai-images-generations.md), [OpenAI Audio Speech](https://docs.deepinfra.com/api-reference/audio/openai-audio-speech.md), [OpenAI Models](https://docs.deepinfra.com/api-reference/models/openai-models.md).
- Together AI: [OpenAI compatibility](https://docs.together.ai/docs/openai-api-compatibility), [audio speech](https://docs.together.ai/reference/audio-speech), [image generation](https://docs.together.ai/reference/post-images-generations) (`width`/`height`, `response_format: base64`).
- Fireworks AI: [OpenAI compatibility](https://docs.fireworks.ai/tools-sdks/openai-compatibility) (completions and chat only). Its image generation is a separate non-OpenAI API and is not enabled.
- ElevenLabs: [text to speech](https://elevenlabs.io/docs/api-reference/text-to-speech/convert) (`POST /v1/text-to-speech/{voice_id}`, `xi-api-key`, `model_id`, `voice_settings.speed`, `output_format`), [voices](https://elevenlabs.io/docs/api-reference/voices/search) (`GET /v2/voices`), [models](https://elevenlabs.io/docs/api-reference/models/list) (`GET /v1/models`).
- Anthropic, Mistral, Ollama Cloud: chat and vision only (no image or speech endpoint is used).
- API key pages: from each provider's quickstart where it names one (DeepSeek, Groq, Ollama, Gemini, OpenRouter, Together, Fireworks); OpenAI, Anthropic, Mistral, DeepInfra and ElevenLabs links are the providers' usual console pages, not quoted in the docs fetched.

Not verified against a live account: DeepInfra's audio response body (if it is JSON with a base64
`audio` field the gateway decodes it, otherwise it passes audio bytes through), the exact
`/v1/openai` prefix of DeepInfra's image and speech paths, Fireworks' `GET /models` list, and the
vision input of each provider's individual models (vision is per model, set by you).

## Development

```
py -3.13 -m venv venv
venv\Scripts\python -m pip install -r requirements-dev.txt
venv\Scripts\python -m pytest -q
./build_exe.ps1            # builds dist\LmApiManager.exe and the plugin zip
./scripts/smoke_test.ps1   # checks the built exe's handshake
```

## Releases

Bump `version` in `manifest.json` and in `lm_provider/__init__.py`, merge to `main`, then push a
matching tag (`git tag v0.3.1 && git push origin v0.3.1`). The `Release` workflow runs the
tests, builds the exe, smoke-tests it and publishes the release with the plugin zip. Pushes
without a tag build nothing.

## License

MIT (`LICENSE`). Bundled libraries: aiohttp (Apache-2.0), keyring (MIT) and their dependencies.
