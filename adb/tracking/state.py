from __future__ import annotations

from threading import Lock
from typing import Protocol, runtime_checkable

from adb.tracking.model import AdbDevicesSnapshot
from adb.tracking.identity import AdbDevicesTrackingScope
from adb.tracking.signal import AdbDevicesSnapshotObserved


def _scope_order(scope: AdbDevicesTrackingScope) -> tuple[int, int]:
    return scope.server_epoch, scope.generation


@runtime_checkable
class AdbDevicesView(Protocol):
    """Read-only current tracked-devices projection for one runtime."""

    @property
    def active_scope(self) -> AdbDevicesTrackingScope | None: ...

    @property
    def current_observation(self) -> AdbDevicesSnapshotObserved | None: ...

    @property
    def current_snapshot(self) -> AdbDevicesSnapshot | None: ...


@runtime_checkable
class AdbDevicesWriter(Protocol):
    """Commit one exact tracking scope into the current tracked-devices projection."""

    def begin_tracking(self, scope: AdbDevicesTrackingScope) -> bool: ...

    def observe(self, observation: AdbDevicesSnapshotObserved) -> bool: ...

    def end_tracking(self, scope: AdbDevicesTrackingScope) -> bool: ...


class AdbDevicesState(AdbDevicesView, AdbDevicesWriter):
    """Thread-safe current tracked-devices state for one runtime.

    The state is scoped to the newest accepted tracking lifetime. A new scope clears the prior
    current observation, and ending that exact scope clears current tracked devices entirely. Late
    writes from older server epochs or tracker generations are rejected rather than resurrecting
    stale tracked-device state. Successive accepted scopes may belong to servers at different endpoints.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._active_scope: AdbDevicesTrackingScope | None = None
        self._latest_scope: AdbDevicesTrackingScope | None = None
        self._current_observation: AdbDevicesSnapshotObserved | None = None

    @property
    def active_scope(self) -> AdbDevicesTrackingScope | None:
        with self._lock:
            return self._active_scope

    @property
    def current_observation(self) -> AdbDevicesSnapshotObserved | None:
        with self._lock:
            return self._current_observation

    @property
    def current_snapshot(self) -> AdbDevicesSnapshot | None:
        with self._lock:
            observation = self._current_observation
            return None if observation is None else observation.snapshot

    def begin_tracking(self, scope: AdbDevicesTrackingScope) -> bool:
        self._require_scope(scope)
        with self._lock:
            if self._active_scope == scope:
                return True
            latest = self._latest_scope
            if latest is not None and _scope_order(scope) <= _scope_order(latest):
                return False
            self._latest_scope = scope
            self._active_scope = scope
            self._current_observation = None
            return True

    def observe(self, observation: AdbDevicesSnapshotObserved) -> bool:
        if not isinstance(observation, AdbDevicesSnapshotObserved):
            raise TypeError("observation must be AdbDevicesSnapshotObserved")
        self._require_scope(observation.scope)
        with self._lock:
            if observation.scope != self._active_scope:
                return False
            self._current_observation = observation
            return True

    def end_tracking(self, scope: AdbDevicesTrackingScope) -> bool:
        self._require_scope(scope)
        with self._lock:
            if scope != self._active_scope:
                return False
            self._active_scope = None
            self._current_observation = None
            return True

    @staticmethod
    def _require_scope(scope: AdbDevicesTrackingScope) -> None:
        if not isinstance(scope, AdbDevicesTrackingScope):
            raise TypeError("scope must be AdbDevicesTrackingScope")


__all__ = [
    "AdbDevicesState",
    "AdbDevicesView",
    "AdbDevicesWriter",
]
