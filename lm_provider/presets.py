"""Known providers for the settings dropdown: they fill the base URL. "Custom" leaves it empty."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    base_url: str
    # Extra request headers some OpenAI-compatible endpoints want (never the key itself).
    headers: dict = field(default_factory=dict)
    # Header the key goes in besides "Authorization: Bearer" (Anthropic reads x-api-key).
    key_header: str = ""


PRESETS = (
    Preset("openai", "OpenAI", "https://api.openai.com/v1"),
    Preset("deepseek", "DeepSeek", "https://api.deepseek.com/v1"),
    Preset("openrouter", "OpenRouter", "https://openrouter.ai/api/v1"),
    Preset("groq", "Groq", "https://api.groq.com/openai/v1"),
    Preset("mistral", "Mistral", "https://api.mistral.ai/v1"),
    Preset("ollama", "Ollama Cloud", "https://ollama.com/v1"),
    Preset("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai"),
    Preset(
        "anthropic",
        "Anthropic",
        "https://api.anthropic.com/v1",
        headers={"anthropic-version": "2023-06-01"},
        key_header="x-api-key",
    ),
)

CUSTOM_PRESET_ID = "custom"

PRESETS_BY_ID = {preset.id: preset for preset in PRESETS}


def preset_for(preset_id: str):
    return PRESETS_BY_ID.get(preset_id or "")
