"""Which models are ticked and for what: the logic behind the model table in the settings
window, with no tkinter in it so it can be tested.

`services` is a dict {model id: [service, ...]}. A model is ticked when at least one service is
checked; a model with nothing checked is not in the dict at all. Nothing is ever ticked on its
own: the guessed defaults are applied only when the user asks for them (click on the name).
"""

from .config import SERVICES
from .services import guess_services

CHECKED = "☑"    # ballot box with check
UNCHECKED = "☐"  # ballot box
DISABLED = "–"   # the provider does not do this service


def _ordered(wanted) -> list:
    return [s for s in SERVICES if s in wanted]


def toggle_service(services: dict, model_id: str, service: str, allowed=SERVICES) -> bool:
    """Flip one service of one model. Vision comes with chat (ticking vision ticks chat,
    clearing chat clears vision); a model left with nothing checked is no longer ticked.
    Returns False when the service is not available for this provider."""
    if service not in SERVICES or service not in allowed:
        return False
    wanted = set(services.get(model_id, ()))
    if service in wanted:
        wanted.discard(service)
        if service == "chat":
            wanted.discard("vision")
    else:
        wanted.add(service)
        if service == "vision":
            wanted.add("chat")
    if wanted:
        services[model_id] = _ordered(wanted)
    else:
        services.pop(model_id, None)
    return True


def toggle_model(services: dict, model_id: str, allowed=SERVICES) -> None:
    """Tick a model with the services guessed from its name, or untick it entirely."""
    if model_id in services:
        del services[model_id]
    else:
        services[model_id] = guess_services(model_id, allowed)


def cell_glyph(services: dict, model_id: str, service: str, allowed=SERVICES) -> str:
    if service not in allowed:
        return DISABLED
    return CHECKED if service in services.get(model_id, ()) else UNCHECKED


def visible_models(offered, services: dict, needle: str = "", ticked_only: bool = False) -> list:
    """Offered plus ticked model ids, sorted, through the filter box and the ticked-only switch."""
    needle = (needle or "").strip().lower()
    names = sorted(set(offered) | set(services), key=str.lower)
    return [n for n in names if needle in n.lower() and (not ticked_only or n in services)]


def ticked_count(services: dict) -> int:
    return sum(1 for value in services.values() if value)


def count_text(services: dict, offered_total: int = 0) -> str:
    of = f" of {offered_total}" if offered_total else ""
    if not ticked_count(services):
        return "Nothing ticked: Pal Engine gets no models from this provider"
    return f"{ticked_count(services)}{of} ticked"
