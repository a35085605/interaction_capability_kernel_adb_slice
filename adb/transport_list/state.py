from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from adb.transport_list.generation import AdbTransportListGeneration
from adb.transport_list.model import AdbTransportList


@dataclass(frozen=True, slots=True)
class AdbTransportListState:
    """Atomic authoritative transport-list projection state.

    ``generation`` is retained for compatibility; ``revision`` is the preferred name
    because this value versions the visible projection rather than a lifecycle.
    ``transport_list`` is ``None`` when no authoritative projection is available.

    This state deliberately carries no server or watch generation. Cross-capability
    lifecycle and stale-work coordination belong to the orchestration layer composing
    those capabilities.
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

    @property
    def revision(self) -> AdbTransportListGeneration:
        """Preferred name for the projection version."""

        return self.generation


@runtime_checkable
class AdbTransportListStateView(Protocol):
    """Read a linearizable snapshot of authoritative transport-list projection state."""

    def read(self) -> AdbTransportListState:
        """Return one atomic transport-list state snapshot."""
        ...


# Preferred reader terminology; retained as an alias to avoid breaking callers.
AdbTransportListStateReader = AdbTransportListStateView


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


__all__ = [
    "AdbTransportListState",
    "AdbTransportListStateAuthority",
    "AdbTransportListStateReader",
    "AdbTransportListStateView",
    "AdbTransportListStateWriter",
]
