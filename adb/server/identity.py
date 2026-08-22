from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from adb.server.endpoint import AdbServerEndpoint


@dataclass(frozen=True, slots=True)
class AdbServer:
    """Coordinator-local reference to one ADB server lifetime epoch.

    ``endpoint`` answers where the service was reached for this epoch. ``epoch`` is a local
    monotonic fencing token used to reject delayed work and stale events. The identity makes no
    claim about native process identity, OS handles, ADB ownership provenance, or termination
    capability.
    """

    endpoint: AdbServerEndpoint
    epoch: int

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        if isinstance(self.epoch, bool) or not isinstance(self.epoch, int):
            raise TypeError("epoch must be an integer")
        if self.epoch <= 0:
            raise ValueError("epoch must be greater than zero")


class _AdbServerSequence:
    """Mint monotonic server epochs for one local coordination domain."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._latest_epoch = 0

    def next(self, endpoint: AdbServerEndpoint) -> AdbServer:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        with self._lock:
            self._latest_epoch += 1
            return AdbServer(endpoint, self._latest_epoch)

__all__ = ["AdbServer"]
