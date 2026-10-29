# LM API Manager

A small local gateway for Pal Engine that lets you chat through **your own** AI provider accounts
(OpenAI, DeepSeek, OpenRouter, Groq, Mistral, Ollama Cloud, Google Gemini, Anthropic, or any other
OpenAI-compatible endpoint). You keep your API keys in this add-on and Pal Engine never sees
them. Pal Engine sends its chat requests to this add-on, which adds your key and forwards them
to the provider you chose.

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

The settings window offers these presets (they fill the base URL) and **Custom** for anything else:

| Preset | Base URL |
|---|---|
| OpenAI | `https://api.openai.com/v1` |
| DeepSeek | `https://api.deepseek.com/v1` |
| OpenRouter | `https://openrouter.ai/api/v1` |
| Groq | `https://api.groq.com/openai/v1` |
| Mistral | `https://api.mistral.ai/v1` |
| Ollama Cloud | `https://ollama.com/v1` |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` |
| Anthropic | `https://api.anthropic.com/v1` |

Models appear in Pal Engine as `<provider id>/<model>`, e.g. `deepseek/deepseek-chat` or
`openrouter/openai/gpt-4o`. In the settings, **Test connection** loads the provider's models; tick
the ones Pal Engine may use (none ticked means all). A model is marked **(free)** only when the
provider itself reports it, with a `:free` id or a zero price in its model list (OpenRouter does);
providers whose list carries no prices (for example Ollama Cloud) show no mark.

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
{"event": "ready", "port": 50123, "version": "0.2.0", "protocol": 1}
```

| Endpoint | Purpose |
|---|---|
| `GET /health` | `{"status": "ok", "version": "...", "providers": 2}` |
| `GET /v1/models` | Models of every provider, ids prefixed with the provider id, `free` when the provider reports a zero price; `errors` lists providers whose list failed |
| `POST /v1/chat/completions` | Routed by the model prefix; streaming (SSE) is passed through chunk by chunk |

Errors use the OpenAI shape `{"error": {"message", "type", "code"}}`, for example
`invalid_api_key`, `insufficient_quota`, `rate_limited`, `upstream_unavailable`,
`upstream_unreachable`, `missing_api_key`, `provider_not_found`.

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
matching tag (`git tag v0.2.0 && git push origin v0.2.0`). The `Release` workflow runs the
tests, builds the exe, smoke-tests it and publishes the release with the plugin zip. Pushes
without a tag build nothing.

## License

MIT (`LICENSE`). Bundled libraries: aiohttp (Apache-2.0), keyring (MIT) and their dependencies.
