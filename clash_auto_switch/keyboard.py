import os
import sys
from types import TracebackType
from typing import Optional


class KeyboardInput:
    """Non-blocking single-key terminal input."""

    def __init__(self) -> None:
        self._old_settings: Optional[list] = None

    def __enter__(self) -> "KeyboardInput":
        if os.name != "nt" and sys.stdin.isatty():
            import termios
            import tty

            fd = sys.stdin.fileno()
            self._old_settings = termios.tcgetattr(fd)
            tty.setraw(fd)
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        if os.name != "nt" and self._old_settings is not None:
            import termios

            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_settings)
            self._old_settings = None

    def read_key(self) -> Optional[str]:
        if os.name == "nt":
            return _read_windows_key()
        return _read_posix_key()


def _read_windows_key() -> Optional[str]:
    import msvcrt

    if not msvcrt.kbhit():
        return None

    key = msvcrt.getwch()
    if key in ("\x00", "\xe0"):
        msvcrt.getwch()
        return None
    if key == "\r":
        return "enter"
    return key.lower()


def _read_posix_key() -> Optional[str]:
    import select

    if not sys.stdin.isatty():
        return None

    readable, _, _ = select.select([sys.stdin], [], [], 0)
    if not readable:
        return None

    key = sys.stdin.read(1)
    if key in ("\r", "\n"):
        return "enter"
    return key.lower()
