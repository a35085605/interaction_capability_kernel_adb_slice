from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from networking import TcpAddress

from adb.transport_list.session_identity import AdbTransportListSessionIdentity
from adb.transport_list.watch.generation import AdbTransportListWatchGeneration


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchState:
    """Atomic view of current runtime-scoped transport-list watch authority.

    ``generation`` always identifies the current authority lifetime. ``endpoint`` and
    ``session_identity`` are present together only while that generation owns a usable
    watch session. Pending acquisition, idle state, and post-revocation cleanup therefore
    expose neither resource field.
    """

    generation: AdbTransportListWatchGeneration
    endpoint: TcpAddress | None = None
    session_identity: AdbTransportListSessionIdentity | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")
        if self.endpoint is not None and not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress or None")
        if self.session_identity is not None and not isinstance(
            self.session_identity, AdbTransportListSessionIdentity
        ):
            raise TypeError(
                "session_identity must be AdbTransportListSessionIdentity or None"
            )
        if (self.endpoint is None) != (self.session_identity is None):
            raise ValueError(
                "endpoint and session_identity must either both be present or both be absent"
            )

    @property
    def active(self) -> bool:
        """Whether this generation currently owns a usable watch session."""

        return self.session_identity is not None


@runtime_checkable
class AdbTransportListWatchStateView(Protocol):
    """Read a linearizable snapshot of current transport-list watch authority."""

    def read(self) -> AdbTransportListWatchState:
        """Return one atomic watch-state snapshot."""
        ...


__all__ = ["AdbTransportListWatchState", "AdbTransportListWatchStateView"]
