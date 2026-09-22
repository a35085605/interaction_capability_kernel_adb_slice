from __future__ import annotations

from threading import Lock

from adb.transport_list.revision import AdbTransportListRevisionIssuer
from adb.transport_list.model import AdbTransportList
from adb.transport_list.state import (
    AdbTransportListState,
    AdbTransportListStateAuthority,
)


class AdbTransportListStateStore(AdbTransportListStateAuthority):
    """Thread-safe authority for transport-list projection state.

    The store advances its own revision only when the visible projection changes.
    Repeating an equal update or clearing an already-unavailable projection is
    idempotent and returns the existing state.
    """

    def __init__(self, initial: AdbTransportListState | None = None) -> None:
        if initial is not None and not isinstance(initial, AdbTransportListState):
            raise TypeError("initial must be AdbTransportListState or None")

        self._lock = Lock()
        if initial is None:
            self._revision_issuer = AdbTransportListRevisionIssuer()
            self._state = AdbTransportListState(self._revision_issuer.issue())
        else:
            self._revision_issuer = AdbTransportListRevisionIssuer(
                after=initial.revision
            )
            self._state = initial

    def read(self) -> AdbTransportListState:
        with self._lock:
            return self._state

    def update(self, transport_list: AdbTransportList) -> AdbTransportListState:
        if not isinstance(transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")

        with self._lock:
            current = self._state
            if current.transport_list == transport_list:
                return current
            next_state = AdbTransportListState(
                revision=self._revision_issuer.issue(),
                transport_list=transport_list,
            )
            self._state = next_state
            return next_state

    def clear(self) -> AdbTransportListState:
        with self._lock:
            current = self._state
            if current.transport_list is None:
                return current
            next_state = AdbTransportListState(
                revision=self._revision_issuer.issue(),
                transport_list=None,
            )
            self._state = next_state
            return next_state


__all__ = ["AdbTransportListStateStore"]
