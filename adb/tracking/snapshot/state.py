from __future__ import annotations

from threading import Lock
from typing import Protocol, runtime_checkable

from adb.tracking.snapshot.identity import AdbDevicesSnapshot, AdbDevicesSnapshotEpoch


@runtime_checkable
class AdbDevicesSnapshotView(Protocol):
    """Read-only authoritative device-snapshot projection for one runtime."""

    @property
    def current(self) -> AdbDevicesSnapshot | None: ...

    @property
    def latest_epoch(self) -> AdbDevicesSnapshotEpoch | None: ...


@runtime_checkable
class AdbDevicesSnapshotWriter(Protocol):
    """Commit already-identified snapshots into one runtime state."""

    def invalidate_current(self) -> None: ...

    def observe(self, snapshot: AdbDevicesSnapshot) -> bool: ...


class AdbDevicesSnapshotState(AdbDevicesSnapshotView, AdbDevicesSnapshotWriter):
    """Thread-safe authoritative current device snapshot for one runtime.

    Snapshot identity is minted by observation producers, not by state. The state accepts only
    monotonically newer runtime-scoped snapshot identities. Server-lifetime correlation belongs
    to the tracking publication and supervision layers that own ``AdbServer`` identity.

    Invalidating the current projection deliberately preserves ``latest_epoch`` so a stale or
    replayed observation cannot become current merely because the visible projection was cleared.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._current: AdbDevicesSnapshot | None = None
        self._latest_epoch: AdbDevicesSnapshotEpoch | None = None

    @property
    def current(self) -> AdbDevicesSnapshot | None:
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

    def observe(self, snapshot: AdbDevicesSnapshot) -> bool:
        """Commit one already-identified snapshot when its epoch advances runtime state."""

        if not isinstance(snapshot, AdbDevicesSnapshot):
            raise TypeError("snapshot must be AdbDevicesSnapshot")
        with self._lock:
            latest_epoch = self._latest_epoch
            if latest_epoch is not None and snapshot.epoch <= latest_epoch:
                return False

            self._current = snapshot
            self._latest_epoch = snapshot.epoch
            return True


__all__ = [
    "AdbDevicesSnapshotState",
    "AdbDevicesSnapshotView",
    "AdbDevicesSnapshotWriter",
]
