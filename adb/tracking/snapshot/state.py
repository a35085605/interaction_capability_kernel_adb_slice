from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Protocol, runtime_checkable

from adb.server.identity import AdbServerIdentity
from adb.tracking.snapshot.identity import AdbTransportListSnapshot, AdbTransportListSnapshotEpoch


@dataclass(frozen=True, slots=True)
class AdbTransportListObservation:
    """One complete transport-list snapshot bound to its source server lifetime."""

    server: AdbServerIdentity
    snapshot: AdbTransportListSnapshot

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")
        if not isinstance(self.snapshot, AdbTransportListSnapshot):
            raise TypeError("snapshot must be AdbTransportListSnapshot")

    @property
    def epoch(self) -> AdbTransportListSnapshotEpoch:
        """Runtime-scoped identity of the underlying snapshot."""

        return self.snapshot.epoch


@runtime_checkable
class AdbTransportListSnapshotView(Protocol):
    """Authoritative server-bound transport-list observation view for one runtime."""

    @property
    def current(self) -> AdbTransportListObservation | None: ...

    @property
    def latest_epoch(self) -> AdbTransportListSnapshotEpoch | None: ...


@runtime_checkable
class AdbTransportListSnapshotWriter(Protocol):
    """Commit already-identified server-bound observations into one runtime state."""

    def invalidate_current(self) -> None: ...

    def observe(self, observation: AdbTransportListObservation) -> bool: ...


class AdbTransportListSnapshotState(AdbTransportListSnapshotView, AdbTransportListSnapshotWriter):
    """Thread-safe authoritative transport-list observation state advancing monotonically by
    snapshot epoch and preserving its epoch watermark.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._current: AdbTransportListObservation | None = None
        self._latest_epoch: AdbTransportListSnapshotEpoch | None = None

    @property
    def current(self) -> AdbTransportListObservation | None:
        with self._lock:
            return self._current

    @property
    def latest_epoch(self) -> AdbTransportListSnapshotEpoch | None:
        with self._lock:
            return self._latest_epoch

    def invalidate_current(self) -> None:
        """Clear the visible projection while preserving the runtime snapshot watermark."""

        with self._lock:
            self._current = None

    def observe(self, observation: AdbTransportListObservation) -> bool:
        """Commit one server-bound observation when its snapshot epoch advances runtime state."""

        if not isinstance(observation, AdbTransportListObservation):
            raise TypeError("observation must be AdbTransportListObservation")
        with self._lock:
            latest_epoch = self._latest_epoch
            if latest_epoch is not None and observation.epoch <= latest_epoch:
                return False

            self._current = observation
            self._latest_epoch = observation.epoch
            return True


__all__ = [
    "AdbTransportListObservation",
    "AdbTransportListSnapshotState",
    "AdbTransportListSnapshotView",
    "AdbTransportListSnapshotWriter",
]
