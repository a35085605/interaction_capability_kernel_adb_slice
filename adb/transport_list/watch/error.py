from __future__ import annotations

from adb.errors import AdbError
from adb.transport_list.watch.failure import AdbTransportListWatchFailure


class AdbTransportListWatchError(AdbError):
    """Typed transport-list watch failure surfaced by the watcher boundary."""

    def __init__(self, failure: AdbTransportListWatchFailure) -> None:
        if not isinstance(failure, AdbTransportListWatchFailure):
            raise TypeError("failure must be AdbTransportListWatchFailure")
        self.failure = failure
        detail = failure.diagnostic
        suffix = f": {detail}" if detail else ""
        super().__init__(
            f"ADB transport-list watch failed with {type(failure).__name__}{suffix}"
        )


class AdbTransportListWatchCancelledError(RuntimeError):
    """The watcher attachment was closed before a watch session became established."""


__all__ = [
    "AdbTransportListWatchCancelledError",
    "AdbTransportListWatchError",
]
