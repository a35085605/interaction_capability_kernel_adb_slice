from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Protocol, runtime_checkable

from adb.server.identity import AdbServer
from adb.tracking.snapshot.identity import AdbDevicesSnapshot, AdbDevicesSnapshotEpoch
from adb.tracking.snapshot.model import AdbDevicesRecord


@dataclass(frozen=True, slots=True)
class AdbDevicesObservation:
    """One complete tracked-devices snapshot bound to its source server lifetime."""

    server: AdbServer
    snapshot: AdbDevicesSnapshot

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServer):
            raise TypeError("server must be AdbServer")
        if not isinstance(self.snapshot, AdbDevicesSnapshot):
            raise TypeError("snapshot must be AdbDevicesSnapshot")

    @property
    def epoch(self) -> AdbDevicesSnapshotEpoch:
        """Runtime-scoped identity of the underlying snapshot."""

        return self.snapshot.epoch

    @property
    def record(self) -> AdbDevicesRecord:
        """Complete tracked-devices record carried by the underlying snapshot."""

        return self.snapshot.record


@runtime_checkable
class AdbDevicesSnapshotView(Protocol):
    """Read-only authoritative server-bound device observation for one runtime."""

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
    """Thread-safe authoritative current device observation for one runtime.

    Snapshot identity is minted by observation producers, not by state. The state accepts only
    monotonically newer runtime-scoped snapshot identities while retaining the exact source
    ``AdbServer`` lifetime alongside the snapshot evidence.

    Invalidating the current projection deliberately preserves ``latest_epoch`` so a stale or
    replayed observation cannot become current merely because the visible projection was cleared.
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
