"""The server ends with Pal Engine: when its stdin closes or the parent process is gone."""

import os
import sys
import threading
import time

PARENT_POLL_S = 2.0


def watch_stdin(on_close) -> None:
    def run():
        try:
            while sys.stdin.buffer.read(4096):
                pass
        except (OSError, ValueError, AttributeError):
            pass
        on_close("stdin closed")

    threading.Thread(target=run, name="stdin-watch", daemon=True).start()


def _windows_parent_waiter(pid: int):
    """A handle opened once at start, so a reused PID is never mistaken for the parent."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    SYNCHRONIZE = 0x00100000
    WAIT_OBJECT_0 = 0
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return None

    def gone() -> bool:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_OBJECT_0

    return gone


def _posix_parent_waiter(pid: int):
    def gone() -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return os.getppid() != pid

    return gone


def watch_parent(pid: int, on_gone) -> bool:
    """False when the parent cannot be watched (it is already gone)."""
    gone = _windows_parent_waiter(pid) if os.name == "nt" else _posix_parent_waiter(pid)
    if gone is None or gone():
        return False

    def run():
        while not gone():
            time.sleep(PARENT_POLL_S)
        on_gone("parent process exited")

    threading.Thread(target=run, name="parent-watch", daemon=True).start()
    return True
