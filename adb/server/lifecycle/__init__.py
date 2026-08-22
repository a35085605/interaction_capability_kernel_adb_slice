"""ADB server lifecycle backend contracts.

ADB-level ownership is creation provenance and responsibility.  Exact OS/process lifetime
capabilities live behind lifecycle backends and are not part of that ownership model.
"""

from adb.server.lifecycle.backend import (
    AdbServerLifecycleBackend,
    LauncherAdbServerLifecycleBackend,
)
from adb.server.lifecycle.handle import (
    AdbServerCloseError,
    AdbServerProcessLifetime,
)
from adb.server.lifecycle.launch import AdbServerLaunchError, AdbServerLauncher


__all__ = [
    "AdbServerCloseError",
    "AdbServerLaunchError",
    "AdbServerLauncher",
    "AdbServerLifecycleBackend",
    "AdbServerProcessLifetime",
    "LauncherAdbServerLifecycleBackend",
]
