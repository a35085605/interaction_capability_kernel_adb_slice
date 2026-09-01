from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Protocol, runtime_checkable

from adb.server.lifetime import AdbServerLifetime
from adb.tracking.snapshot.identity import AdbDevicesSnapshot, AdbDevicesSnapshotEpoch


@dataclass(frozen=True, slots=True)
class AdbDevicesObservation:
    """One complete tracked-devices snapshot bound to its source server lifetime."""

    server: AdbServerLifetime
    snapshot: AdbDevicesSnapshot

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServerLifetime):
            raise TypeError("server must be AdbServerLifetime")
        if not isinstance(self.snapshot, AdbDevicesSnapshot):
            raise TypeError("snapshot must be AdbDevicesSnapshot")

    @property
    def epoch(self) -> AdbDevicesSnapshotEpoch:
        """Runtime-scoped identity of the underlying snapshot."""

        return self.snapshot.epoch


@runtime_checkable
class AdbDevicesSnapshotView(Protocol):
    """Authoritative server-bound device-observation view for one runtime."""

    @property
    def current(self) -> AdbDevicesObservation | None: ...

    @property
    def latest_epoch(self) -> AdbDevicesSnapshotEpoch | None: ...


@runtime_checkable
class AdbDevicesSnapshotWriter(Protocol):
    """Commit already-identified server-bound observations into one runtime state."""

    def invalidate_current(self) -> None: ...

    def observe(self, observation: AdbDevicesObservation) -> bool: ...


class AdbDevicesSnapshotState(AdbDevicesSnapshotView, AdbDevicesSnapshotWriter):
    """Thread-safe authoritative device-observation state advancing monotonically by snapshot epoch
    and preserving its epoch watermark.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._current: AdbDevicesObservation | None = None
        self._latest_epoch: AdbDevicesSnapshotEpoch | None = None

    @property
    def current(self) -> AdbDevicesObservation | None:
        with self._lock:
            return self._current

    @property
    def latest_epoch(self) -> AdbDevicesSnapshotEpoch | None:
        with self._lock:
            return self._latest_epoch

    def invalidate_current(self) -> None:
        """Clear the visible projection while preserving the runtime snapshot watermark."""

        with self._lock:
            self._current = None

    def observe(self, observation: AdbDevicesObservation) -> bool:
        """Commit one server-bound observation when its snapshot epoch advances runtime state."""

        if not isinstance(observation, AdbDevicesObservation):
            raise TypeError("observation must be AdbDevicesObservation")
        with self._lock:
            latest_epoch = self._latest_epoch
            if latest_epoch is not None and observation.epoch <= latest_epoch:
                return False

            self._current = observation
            self._latest_epoch = observation.epoch
            return True


__all__ = [
    "AdbDevicesObservation",
    "AdbDevicesSnapshotState",
    "AdbDevicesSnapshotView",
    "AdbDevicesSnapshotWriter",
]
