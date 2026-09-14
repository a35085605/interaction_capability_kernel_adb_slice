from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from windows.process.owned import WindowsRetainedProcess


class WindowsProcessLifecycleError(RuntimeError):
    """Base error for Windows owned-process lifecycle operations."""


class WindowsProcessStartError(WindowsProcessLifecycleError):
    """A process could not be started under lifecycle ownership.

    When process creation already produced OS-owned state, ``retained_process``
    carries that state so a resource driver can return it with an acquisition
    failure instead of losing the ability to retry cleanup. ``original_error``
    preserves the underlying interruption or failure for lifecycle propagation.
    """

    def __init__(
        self,
        diagnostic: str,
        *,
        retained_process: WindowsRetainedProcess | None = None,
        original_error: BaseException | None = None,
    ) -> None:
        super().__init__(diagnostic)
        self.retained_process = retained_process
        self.original_error = original_error


class WindowsProcessWaitError(WindowsProcessLifecycleError):
    """Process or Job status observation failed."""


class WindowsProcessWaitTimeout(TimeoutError):
    """A root-process or process-tree wait timed out."""


class WindowsProcessTerminationUnconfirmed(WindowsProcessLifecycleError):
    """Forced cleanup was requested but could not be confirmed."""

__all__ = [
    "WindowsProcessLifecycleError",
    "WindowsProcessStartError",
    "WindowsProcessTerminationUnconfirmed",
    "WindowsProcessWaitError",
    "WindowsProcessWaitTimeout",
]
