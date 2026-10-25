import argparse
import asyncio
import logging
import os
import sys

from lm_provider import __version__

TOKEN_ENV = "PAL_ENGINE_SESSION_TOKEN"


class RedactKeys(logging.Filter):
    """Last line of defence: no stored key ever reaches a log line, even by accident."""

    def __init__(self, store):
        super().__init__()
        self._store = store

    def filter(self, record):
        from lm_provider.upstream import redact

        message = record.getMessage()
        cleaned = redact(message, self._store.all_keys())
        if cleaned != message:
            record.msg, record.args = cleaned, None
        return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local OpenAI-compatible gateway that adds your own API keys (Pal Engine integration).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--serve", action="store_true",
                      help=f"Run the local server. The session token comes from the {TOKEN_ENV} environment variable.")
    mode.add_argument("--settings", action="store_true", help="Open the provider settings window.")
    mode.add_argument("--version", action="store_true", help="Print the version and exit.")
    parser.add_argument("--parent-pid", type=int, default=0, help="Exit when this process exits.")
    return parser


def run_server(parent_pid: int) -> int:
    from lm_provider.config import KeyVault, ProviderStore
    from lm_provider.lifecycle import watch_parent, watch_stdin
    from lm_provider.server import serve

    token = os.environ.pop(TOKEN_ENV, "")
    if len(token) < 32:
        print(f"error: {TOKEN_ENV} is missing or too short", file=sys.stderr)
        return 2

    store = ProviderStore(KeyVault())
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RedactKeys(store))
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(levelname)s %(message)s")
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    log = logging.getLogger("lm_provider")

    async def main() -> None:
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()

        def request_stop(reason: str) -> None:
            log.info("Stopping: %s", reason)
            loop.call_soon_threadsafe(stop.set)

        watch_stdin(request_stop)
        if parent_pid and not watch_parent(parent_pid, request_stop):
            log.warning("Parent process %s is not running", parent_pid)
            return
        store.refresh()
        await serve(store, token, stop)

    code = 0
    try:
        asyncio.run(main())
    except OSError as error:
        print(f"error: {error}", file=sys.stderr)
        code = 1
    # The stdin watcher may still be blocked in a read: leave without the interpreter shutdown
    # that would wait on it. The server is already closed at this point.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def main() -> int:
    args = build_parser().parse_args()
    if args.version:
        print(__version__)
        return 0
    if args.serve:
        return run_server(args.parent_pid)
    # No mode (double-click on the exe) opens the settings too.
    from lm_provider.settings_ui import run_settings

    return run_settings()


if __name__ == "__main__":
    raise SystemExit(main())
