"""Provider list (non-secret, providers.json) and API keys (Windows Credential Manager only).

The list lives in %APPDATA%\\PalEngine\\lm-api-manager\\providers.json. A key is stored by
`keyring` under the provider id; it is never written to a file, a log or stdout, and the
settings window shows only its last four characters.

Until 0.2.0 the app was called "LM OpenAI Provider": its list and keys are taken over on first
use (the list is copied, the old file stays; a key moves on its first read).
"""

import json
import os
import shutil
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .presets import preset_for

KEYRING_SERVICE = "PalEngine.LmApiManager"
LEGACY_KEYRING_SERVICE = "PalEngine.LmOpenAiProvider"
CONFIG_DIR_NAME = "lm-api-manager"
LEGACY_CONFIG_DIR_NAME = "lm-open-ai-provider"
PROVIDER_TYPES = ("llm",)
FILE_VERSION = 1
# A provider id is the model prefix ("<id>/<model>"): no slash, short, lowercase.
PROVIDER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ConfigError(ValueError):
    """A provider entry the user has to fix (shown as is in the settings window)."""


def _palengine_dir() -> Path:
    return Path(os.environ.get("APPDATA") or str(Path.home())) / "PalEngine"


def config_dir() -> Path:
    override = os.environ.get("LM_PROVIDER_HOME")
    if override:
        return Path(override)
    return _palengine_dir() / CONFIG_DIR_NAME


def migrate_legacy_providers(new_dir: Path, old_dir: Path) -> bool:
    """Copy the old providers.json into the new folder when the new one has none yet.
    The old file is kept (an older install may still read it). True when a copy was made."""
    new_file, old_file = new_dir / "providers.json", old_dir / "providers.json"
    if new_file.exists() or not old_file.is_file():
        return False
    try:
        new_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old_file, new_file)
    except OSError:
        return False
    return True


def providers_path() -> Path:
    directory = config_dir()
    if not os.environ.get("LM_PROVIDER_HOME"):
        migrate_legacy_providers(directory, _palengine_dir() / LEGACY_CONFIG_DIR_NAME)
    return directory / "providers.json"


@dataclass
class Provider:
    id: str
    name: str
    base_url: str
    type: str = "llm"
    preset: str = "custom"
    # Optional allow-list: when set, only these models are listed and accepted.
    models: list = field(default_factory=list)

    @property
    def extra_headers(self) -> dict:
        preset = preset_for(self.preset)
        return dict(preset.headers) if preset else {}

    @property
    def key_header(self) -> str:
        preset = preset_for(self.preset)
        return preset.key_header if preset else ""


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug[:40].strip("-") or "provider"


def unique_id(name: str, taken) -> str:
    base = slugify(name)
    candidate, n = base, 2
    while candidate in taken:
        suffix = f"-{n}"
        candidate = base[: 40 - len(suffix)] + suffix
        n += 1
    return candidate


def normalize_base_url(url: str) -> str:
    """https only; plain http is accepted for this machine (a local server), never remote,
    because the key would cross the network in clear text."""
    url = (url or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise ConfigError("The base URL must start with https://")
    if parsed.scheme == "http" and parsed.hostname not in LOCAL_HOSTS:
        raise ConfigError("The base URL must use https:// (http:// only for this computer)")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ConfigError("The base URL must not contain a query, fragment or credentials")
    return url


def validate_provider(provider: Provider) -> Provider:
    if not PROVIDER_ID_PATTERN.match(provider.id or ""):
        raise ConfigError(f"Invalid provider id: {provider.id!r}")
    provider.name = (provider.name or "").strip()
    if not provider.name:
        raise ConfigError("The provider needs a name")
    if provider.type not in PROVIDER_TYPES:
        raise ConfigError(f"Unsupported provider type: {provider.type}")
    provider.base_url = normalize_base_url(provider.base_url)
    provider.models = [m.strip() for m in provider.models if isinstance(m, str) and m.strip()]
    return provider


def load_providers(path: Path = None) -> list:
    path = path or providers_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    providers, seen = [], set()
    for entry in data.get("providers", []) if isinstance(data, dict) else []:
        try:
            provider = validate_provider(Provider(
                id=str(entry.get("id", "")),
                name=str(entry.get("name", "")),
                base_url=str(entry.get("base_url", "")),
                type=str(entry.get("type", "llm")),
                preset=str(entry.get("preset", "custom")),
                models=list(entry.get("models") or []),
            ))
        except (ConfigError, AttributeError, TypeError):
            continue
        if provider.id in seen:
            continue
        seen.add(provider.id)
        providers.append(provider)
    return providers


def save_providers(providers, path: Path = None) -> None:
    """Atomic write: a half-written file is never read by the running server."""
    path = path or providers_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": FILE_VERSION, "providers": [asdict(p) for p in providers]}
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".providers-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class KeyVault:
    """API keys in the OS credential store (Windows Credential Manager, DPAPI)."""

    def __init__(self, service: str = KEYRING_SERVICE, legacy_service: str = LEGACY_KEYRING_SERVICE):
        import keyring  # imported here so tests and --version need no backend

        self._keyring = keyring
        self._service = service
        self._legacy_service = legacy_service

    def get(self, provider_id: str):
        key = self._keyring.get_password(self._service, provider_id)
        if key or not self._legacy_service:
            return key
        # A key saved under the old name (LM OpenAI Provider) moves to the new one.
        key = self._keyring.get_password(self._legacy_service, provider_id)
        if key:
            self._keyring.set_password(self._service, provider_id, key)
            self._delete(self._legacy_service, provider_id)
        return key

    def set(self, provider_id: str, key: str) -> None:
        self._keyring.set_password(self._service, provider_id, key)

    def delete(self, provider_id: str) -> None:
        self._delete(self._service, provider_id)
        if self._legacy_service:
            self._delete(self._legacy_service, provider_id)

    def _delete(self, service: str, provider_id: str) -> None:
        try:
            self._keyring.delete_password(service, provider_id)
        except self._keyring.errors.PasswordDeleteError:
            pass


class MemoryVault:
    """In-memory stand-in for tests."""

    def __init__(self, keys=None):
        self.keys = dict(keys or {})

    def get(self, provider_id):
        return self.keys.get(provider_id)

    def set(self, provider_id, key):
        self.keys[provider_id] = key

    def delete(self, provider_id):
        self.keys.pop(provider_id, None)


def key_hint(key: str) -> str:
    """What the settings window may show of a key: its last four characters."""
    key = key or ""
    return f"••••{key[-4:]}" if len(key) >= 8 else ("••••" if key else "")


class ProviderStore:
    """The server's view of the providers: reloaded when the settings window saves the file
    (mtime check per request), keys read from the vault on reload and kept in memory."""

    def __init__(self, vault, path: Path = None):
        self._vault = vault
        self._path = path or providers_path()
        self._mtime = None
        self._providers = {}
        self._keys = {}

    def _current_mtime(self):
        try:
            return self._path.stat().st_mtime_ns
        except FileNotFoundError:
            return None

    def refresh(self) -> None:
        mtime = self._current_mtime()
        if self._mtime is not None and mtime == self._mtime:
            return
        providers = load_providers(self._path)
        self._providers = {p.id: p for p in providers}
        self._keys = {p.id: self._vault.get(p.id) or "" for p in providers}
        self._mtime = mtime if mtime is not None else 0

    def providers(self) -> list:
        self.refresh()
        return list(self._providers.values())

    def get(self, provider_id: str):
        self.refresh()
        return self._providers.get(provider_id)

    def key(self, provider_id: str) -> str:
        return self._keys.get(provider_id, "")

    def all_keys(self) -> list:
        return [k for k in self._keys.values() if k]
