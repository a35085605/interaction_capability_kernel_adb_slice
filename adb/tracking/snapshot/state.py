from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Protocol, runtime_checkable

from adb.epoch import EpochIssuer
from adb.server.identity import ServerEpoch
from adb.tracking.snapshot.identity import (
    AdbDevicesSnapshotEpoch,
    AdbDevicesSnapshotEpochSequence,
)
from adb.tracking.snapshot.model import AdbDevicesSnapshot


@dataclass(frozen=True, slots=True)
class AdbDevicesSnapshotRevision:
    """One committed runtime device snapshot with snapshot-local identity.

    ``epoch`` advances for every accepted snapshot observation. ``server_epoch`` identifies
    the ADB server data world the snapshot belongs to. Observation-channel identity is
    deliberately absent from committed snapshot state.
    """

    server_epoch: ServerEpoch
    epoch: AdbDevicesSnapshotEpoch
    snapshot: AdbDevicesSnapshot

    def __post_init__(self) -> None:
        if not isinstance(self.server_epoch, ServerEpoch):
            raise TypeError("server_epoch must be ServerEpoch")
        if not isinstance(self.epoch, AdbDevicesSnapshotEpoch):
            raise TypeError("epoch must be AdbDevicesSnapshotEpoch")
        if not isinstance(self.snapshot, AdbDevicesSnapshot):
            raise TypeError("snapshot must be AdbDevicesSnapshot")


@runtime_checkable
class AdbDevicesSnapshotView(Protocol):
    """Read-only authoritative device-snapshot projection for one runtime."""

    @property
    def current(self) -> AdbDevicesSnapshotRevision | None: ...

    @property
    def current_snapshot(self) -> AdbDevicesSnapshot | None: ...

    @property
    def latest_epoch(self) -> AdbDevicesSnapshotEpoch | None: ...

    @property
    def server_epoch(self) -> ServerEpoch | None: ...


@runtime_checkable
class AdbDevicesSnapshotWriter(Protocol):
    """Commit server-local device snapshots into one runtime snapshot state."""

    def advance_server(self, server_epoch: ServerEpoch) -> bool: ...

    def observe(
        self,
        server_epoch: ServerEpoch,
        snapshot: AdbDevicesSnapshot,
    ) -> AdbDevicesSnapshotRevision | None: ...


class AdbDevicesSnapshotState(AdbDevicesSnapshotView, AdbDevicesSnapshotWriter):
    """Thread-safe authoritative current device snapshot for one runtime.

    The state deliberately stores no tracker-session or connection identity. Every accepted
    snapshot receives a fresh runtime-scoped ``AdbDevicesSnapshotEpoch`` even when its value is
    equal to the previous snapshot.

    Moving to a newer ``ServerEpoch`` invalidates the prior server-local snapshot. Re-observing
    the same server epoch preserves the last snapshot until a newer observation is committed.
    Writes from older server epochs are rejected.
    """

    def __init__(
        self,
        *,
        _epoch_issuer: EpochIssuer[AdbDevicesSnapshotEpoch] | None = None,
    ) -> None:
        if _epoch_issuer is None:
            _epoch_issuer = AdbDevicesSnapshotEpochSequence()
        if not isinstance(_epoch_issuer, EpochIssuer):
            raise TypeError("_epoch_issuer must satisfy EpochIssuer")
        self._lock = Lock()
        self._epoch_issuer = _epoch_issuer
        self._current: AdbDevicesSnapshotRevision | None = None
        self._latest_epoch: AdbDevicesSnapshotEpoch | None = None
        self._server_epoch: ServerEpoch | None = None

    @property
    def current(self) -> AdbDevicesSnapshotRevision | None:
        with self._lock:
            return self._current

    @property
    def current_snapshot(self) -> AdbDevicesSnapshot | None:
        with self._lock:
            current = self._current
            return None if current is None else current.snapshot

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
    ) -> AdbDevicesSnapshotRevision | None:
        """Commit one accepted snapshot observation and assign a fresh snapshot epoch."""

        self._require_server_epoch(server_epoch)
        if not isinstance(snapshot, AdbDevicesSnapshot):
            raise TypeError("snapshot must be AdbDevicesSnapshot")
        with self._lock:
            current_server_epoch = self._server_epoch
            if current_server_epoch is not None and server_epoch < current_server_epoch:
                return None
            if current_server_epoch is None or server_epoch > current_server_epoch:
                self._server_epoch = server_epoch
                self._current = None
            epoch = self._epoch_issuer.issue()
            if not isinstance(epoch, AdbDevicesSnapshotEpoch):
                raise TypeError("snapshot epoch issuer must return AdbDevicesSnapshotEpoch")
            revision = AdbDevicesSnapshotRevision(server_epoch, epoch, snapshot)
            self._current = revision
            self._latest_epoch = epoch
            return revision

    @staticmethod
    def _require_server_epoch(server_epoch: ServerEpoch) -> None:
        if not isinstance(server_epoch, ServerEpoch):
            raise TypeError("server_epoch must be ServerEpoch")


__all__ = [
    "AdbDevicesSnapshotRevision",
    "AdbDevicesSnapshotState",
    "AdbDevicesSnapshotView",
    "AdbDevicesSnapshotWriter",
]
