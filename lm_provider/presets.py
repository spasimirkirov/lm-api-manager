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
    # What the provider's own docs confirm (see README, Sources): "chat", "vision", "tts", "images".
    services: tuple = ("chat", "vision")
    # "" = OpenAI-compatible; "elevenlabs" = the small TTS adapter in elevenlabs.py.
    adapter: str = ""
    # How image requests are shaped: "openai" (size, response_format) or "together" (width/height).
    images_style: str = "openai"
    # Fixed voice lists where the docs give them (the provider has no voice-list endpoint).
    voices: tuple = ()
    # Catalog entry shown in the settings window: what it is good for, where to get a key.
    description: str = ""
    key_url: str = ""
    pricing_url: str = ""


OPENAI_VOICES = ("alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer",
                 "verse", "marin", "cedar")
ALL_SERVICES = ("chat", "vision", "tts", "images")

PRESETS = (
    Preset("openai", "OpenAI", "https://api.openai.com/v1", services=ALL_SERVICES, voices=OPENAI_VOICES,
           description="The original GPT models: strong chat and vision, DALL-E / GPT Image pictures and natural voices.",
           key_url="https://platform.openai.com/api-keys", pricing_url="https://openai.com/api/pricing/"),
    Preset("deepseek", "DeepSeek", "https://api.deepseek.com/v1", services=("chat",),
           description="Very cheap, strong chat and reasoning models (DeepSeek V3 and R1). Chat only.",
           key_url="https://platform.deepseek.com/api_keys", pricing_url="https://api-docs.deepseek.com/quick_start/pricing"),
    Preset("openrouter", "OpenRouter", "https://openrouter.ai/api/v1",
           description="One key for hundreds of models from many labs, including free ones. Chat and vision.",
           key_url="https://openrouter.ai/keys", pricing_url="https://openrouter.ai/models"),
    Preset("groq", "Groq", "https://api.groq.com/openai/v1", services=("chat", "vision", "tts"),
           voices=("troy", "hannah", "austin"),
           description="Very fast open models (Llama and others) on custom chips, plus Orpheus voices.",
           key_url="https://console.groq.com/keys", pricing_url="https://groq.com/pricing"),
    Preset("mistral", "Mistral", "https://api.mistral.ai/v1",
           description="European models: Mistral Large, Pixtral for vision, generous free tier for testing.",
           key_url="https://console.mistral.ai/api-keys", pricing_url="https://mistral.ai/pricing"),
    Preset("ollama", "Ollama Cloud", "https://ollama.com/v1",
           description="Big open models hosted by Ollama, the same names you use locally. Chat and vision.",
           key_url="https://ollama.com/settings/keys", pricing_url="https://ollama.com/pricing"),
    Preset("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
           services=("chat", "vision", "images"),
           description="Google Gemini: long context, vision and Gemini image generation, with a free tier.",
           key_url="https://aistudio.google.com/apikey", pricing_url="https://ai.google.dev/gemini-api/docs/pricing"),
    Preset(
        "anthropic",
        "Anthropic",
        "https://api.anthropic.com/v1",
        headers={"anthropic-version": "2023-06-01"},
        key_header="x-api-key",
        description="Claude models: careful chat and strong vision. Chat and vision only.",
        key_url="https://console.anthropic.com/settings/keys", pricing_url="https://www.anthropic.com/pricing#api",
    ),
    Preset("deepinfra", "DeepInfra", "https://api.deepinfra.com/v1/openai", services=ALL_SERVICES,
           description="Cheap open models: DeepSeek, Llama, FLUX images, Kokoro voices.",
           key_url="https://deepinfra.com/dash/api_keys", pricing_url="https://deepinfra.com/pricing"),
    Preset("together", "Together AI", "https://api.together.ai/v1", services=ALL_SERVICES,
           images_style="together", voices=("tara", "af_alloy", "friendly sidekick"),
           description="Open models at low prices: Llama, Qwen, FLUX images and Kokoro / Orpheus voices.",
           key_url="https://api.together.ai/settings/api-keys", pricing_url="https://www.together.ai/pricing"),
    Preset("fireworks", "Fireworks AI", "https://api.fireworks.ai/inference/v1",
           description="Fast open chat and vision models (Llama, Qwen, DeepSeek). Chat and vision only here.",
           key_url="https://app.fireworks.ai/settings/users/api-keys", pricing_url="https://fireworks.ai/pricing"),
    Preset("elevenlabs", "ElevenLabs", "https://api.elevenlabs.io", services=("tts",), adapter="elevenlabs",
           description="The most natural voices, many languages, your own cloned voices. Speech only.",
           key_url="https://elevenlabs.io/app/settings/api-keys", pricing_url="https://elevenlabs.io/pricing/api"),
)

CUSTOM_PRESET_ID = "custom"

PRESETS_BY_ID = {preset.id: preset for preset in PRESETS}


def preset_for(preset_id: str):
    return PRESETS_BY_ID.get(preset_id or "")


def preset_services(preset_id: str) -> tuple:
    """Services a preset's docs confirm; a custom endpoint may offer any of them."""
    preset = preset_for(preset_id)
    return preset.services if preset else ALL_SERVICES
