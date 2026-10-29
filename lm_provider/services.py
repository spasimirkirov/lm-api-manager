"""Guessing which services a model is for, from its name. The settings window pre-fills this
and the user can change it."""

IMAGE_HINTS = ("flux", "sdxl", "stable-diffusion", "sd3", "dall-e", "gpt-image", "imagen", "-image")
TTS_HINTS = ("tts", "kokoro", "eleven", "orpheus", "sonic", "speech")
VISION_HINTS = ("-vl", "vision", "gpt-4o", "gemini")


def guess_services(model_id: str, allowed=None) -> list:
    """Services for a model name; `allowed` (a preset's services) cuts the guess down."""
    name = (model_id or "").lower()
    if any(hint in name for hint in IMAGE_HINTS):
        guess = ["images"]
    elif any(hint in name for hint in TTS_HINTS):
        guess = ["tts"]
    elif any(hint in name for hint in VISION_HINTS):
        guess = ["chat", "vision"]
    else:
        guess = ["chat"]
    if allowed is not None:
        guess = [s for s in guess if s in allowed] or (["chat"] if "chat" in allowed else list(allowed)[:1])
    return guess
