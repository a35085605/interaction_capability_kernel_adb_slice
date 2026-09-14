"""Owned Windows process-tree lifecycle primitives."""

from windows.process.errors import (
    WindowsProcessLifecycleError,
    WindowsProcessStartError,
    WindowsProcessTerminationUnconfirmed,
    WindowsProcessWaitError,
    WindowsProcessWaitTimeout,
)
from windows.process.manager import WindowsProcessLifecycleManager
from windows.process.model import ProcessExit, WindowsProcessSpec
from windows.process.owned import WindowsOwnedProcess, WindowsRetainedProcess

__all__ = [
    "ProcessExit",
    "WindowsOwnedProcess",
    "WindowsProcessLifecycleError",
    "WindowsProcessLifecycleManager",
    "WindowsRetainedProcess",
    "WindowsProcessSpec",
    "WindowsProcessStartError",
    "WindowsProcessTerminationUnconfirmed",
    "WindowsProcessWaitError",
    "WindowsProcessWaitTimeout",
]
