from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Protocol, runtime_checkable

from adb.transport_list.generation import (
    AdbTransportListGeneration,
    AdbTransportListGenerationIssuer,
)
from adb.transport_list.model import AdbTransportList


@dataclass(frozen=True, slots=True)
class AdbTransportListState:
    """Atomic authoritative transport-list projection state.

    ``generation`` identifies the current projection state. ``transport_list`` is ``None`` when
    no authoritative transport list is available. An empty ``AdbTransportList`` is still a valid
    available projection and means that the observed server currently exposes zero transports.

    This state deliberately carries no server or watch generation. Cross-capability lifecycle and
    stale-work coordination belong to the orchestration layer composing those capabilities.
    """

    generation: AdbTransportListGeneration
    transport_list: AdbTransportList | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbTransportListGeneration):
            raise TypeError("generation must be AdbTransportListGeneration")
        if self.transport_list is not None and not isinstance(
            self.transport_list, AdbTransportList
        ):
            raise TypeError("transport_list must be AdbTransportList or None")


@runtime_checkable
class AdbTransportListStateView(Protocol):
    """Read a linearizable snapshot of authoritative transport-list projection state."""

    def read(self) -> AdbTransportListState:
        """Return one atomic transport-list state snapshot."""
        ...


@runtime_checkable
class AdbTransportListStateWriter(Protocol):
    """Apply transport-list-local projection transitions."""

    def update(self, transport_list: AdbTransportList) -> AdbTransportListState:
        """Make ``transport_list`` the authoritative projection and return committed state."""
        ...

    def clear(self) -> AdbTransportListState:
        """Make the authoritative transport list unavailable and return committed state."""
        ...


@runtime_checkable
class AdbTransportListStateAuthority(
    AdbTransportListStateView,
    AdbTransportListStateWriter,
    Protocol,
):
    """Atomic authority for transport-list-local projection state."""


class AdbTransportListStateStore(AdbTransportListStateAuthority):
    """Thread-safe authority for transport-list projection state.

    The store advances its own generation only when the visible projection changes. Repeating an
    equal update or clearing an already-unavailable projection is idempotent and returns the
    existing state.
    """

    def __init__(self, initial: AdbTransportListState | None = None) -> None:
        if initial is not None and not isinstance(initial, AdbTransportListState):
            raise TypeError("initial must be AdbTransportListState or None")

        self._lock = Lock()
        if initial is None:
            self._generation_issuer = AdbTransportListGenerationIssuer()
            self._state = AdbTransportListState(self._generation_issuer.issue())
        else:
            self._generation_issuer = AdbTransportListGenerationIssuer(
                after=initial.generation
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
                generation=self._generation_issuer.issue(),
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
                generation=self._generation_issuer.issue(),
                transport_list=None,
            )
            self._state = next_state
            return next_state


__all__ = [
    "AdbTransportListState",
    "AdbTransportListStateAuthority",
    "AdbTransportListStateStore",
    "AdbTransportListStateView",
    "AdbTransportListStateWriter",
]
