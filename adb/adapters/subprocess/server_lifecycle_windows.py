from __future__ import annotations

import os


if os.name != "nt":
    raise ImportError("server_lifecycle_windows is only available on Windows")

from adb.aosp.io.server_process_windows import WindowsAospAdbServerProcessDriver


# Compatibility alias for the previous private adapter implementation.
_WindowsAdbServerFactory = WindowsAospAdbServerProcessDriver


__all__ = ["_WindowsAdbServerFactory"]
