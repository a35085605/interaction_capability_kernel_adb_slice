from __future__ import annotations

from threading import Lock
from typing import Protocol, runtime_checkable

from adb.server.identity import ServerEpoch
from adb.tracking.snapshot.identity import AdbDevicesSnapshot, AdbDevicesSnapshotEpoch


@runtime_checkable
class AdbDevicesSnapshotView(Protocol):
    """Read-only authoritative device-snapshot projection for one runtime."""

    @property
    def current(self) -> AdbDevicesSnapshot | None: ...

    @property
    def latest_epoch(self) -> AdbDevicesSnapshotEpoch | None: ...

    @property
    def server_epoch(self) -> ServerEpoch | None: ...


@runtime_checkable
class AdbDevicesSnapshotWriter(Protocol):
    """Commit already-identified server-local snapshots into one runtime state."""

    def advance_server(self, server_epoch: ServerEpoch) -> bool: ...

    def observe(
        self,
        server_epoch: ServerEpoch,
        snapshot: AdbDevicesSnapshot,
    ) -> bool: ...


class AdbDevicesSnapshotState(AdbDevicesSnapshotView, AdbDevicesSnapshotWriter):
    """Thread-safe authoritative current device snapshot for one runtime.

    Snapshot identity is minted by observation producers, not by state.  The state rejects
    snapshot epochs that do not advance monotonically and writes from older server epochs.

    Moving to a newer ``ServerEpoch`` invalidates the prior server-local snapshot. Re-observing
    the same server epoch preserves the last snapshot until a newer observation is committed.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._current: AdbDevicesSnapshot | None = None
        self._latest_epoch: AdbDevicesSnapshotEpoch | None = None
        self._server_epoch: ServerEpoch | None = None

    @property
    def current(self) -> AdbDevicesSnapshot | None:
        with self._lock:
            return self._current

    @property
    def latest_epoch(self) -> AdbDevicesSnapshotEpoch | None:
        with self._lock:
            return self._latest_epoch

    @property
    def server_epoch(self) -> ServerEpoch | None:
        with self._lock:
            return self._server_epoch

    def advance_server(self, server_epoch: ServerEpoch) -> bool:
        """Advance the snapshot data world without coupling it to a tracking session."""

        self._require_server_epoch(server_epoch)
        with self._lock:
            current_server_epoch = self._server_epoch
            if current_server_epoch is None:
                self._server_epoch = server_epoch
                return True
            if server_epoch < current_server_epoch:
                return False
            if server_epoch == current_server_epoch:
                return True
            self._server_epoch = server_epoch
            self._current = None
            return True

    def observe(
        self,
        server_epoch: ServerEpoch,
        snapshot: AdbDevicesSnapshot,
    ) -> bool:
        """Commit one already-identified snapshot when its server and epoch are current."""

        self._require_server_epoch(server_epoch)
        if not isinstance(snapshot, AdbDevicesSnapshot):
            raise TypeError("snapshot must be AdbDevicesSnapshot")
        with self._lock:
            current_server_epoch = self._server_epoch
            if current_server_epoch is not None and server_epoch < current_server_epoch:
                return False

            latest_epoch = self._latest_epoch
            if latest_epoch is not None and snapshot.epoch <= latest_epoch:
                return False

            if current_server_epoch is None or server_epoch > current_server_epoch:
                self._server_epoch = server_epoch
                self._current = None

            self._current = snapshot
            self._latest_epoch = snapshot.epoch
            return True

    @staticmethod
    def _require_server_epoch(server_epoch: ServerEpoch) -> None:
        if not isinstance(server_epoch, ServerEpoch):
            raise TypeError("server_epoch must be ServerEpoch")


__all__ = [
    "AdbDevicesSnapshotState",
    "AdbDevicesSnapshotView",
    "AdbDevicesSnapshotWriter",
]
