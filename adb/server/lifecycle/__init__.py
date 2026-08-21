"""Process-owned ADB server lifecycle contracts and supervision."""

from typing import TYPE_CHECKING

from adb.server.lifecycle.handle import (
    AdbServerCloseError,
    AdbServerNativeError,
    AdbServerNativeHandle,
)

if TYPE_CHECKING:
    from adb.server.lifecycle.launch import AdbServerLaunchError, AdbServerLauncher


def __getattr__(name: str) -> object:
    if name in {"AdbServerLaunchError", "AdbServerLauncher"}:
        from adb.server.lifecycle.launch import AdbServerLaunchError, AdbServerLauncher

        exports = {
            "AdbServerLaunchError": AdbServerLaunchError,
            "AdbServerLauncher": AdbServerLauncher,
        }
        globals().update(exports)
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AdbServerCloseError",
    "AdbServerLaunchError",
    "AdbServerLauncher",
    "AdbServerNativeError",
    "AdbServerNativeHandle",
]
