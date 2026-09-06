from __future__ import annotations

from adb.authority import AdbTransportListObservationAuthority
from adb.server.identity import AdbServerIdentity
from adb.transport_list.model import AdbTransportList
from adb.transport_list.observation import AdbTransportListObservationBasis
from adb.transport_list.state import (
    AdbTransportListCoordinatedObservationResult,
    AdbTransportListObservationServerConflict,
    AdbTransportListObserved,
)
from eventing import EventPublisher


class AdbTransportListCoordinator:
    """Orchestrate ordered transport-list observations through one authority boundary."""

    def __init__(
        self,
        authority: AdbTransportListObservationAuthority,
        *,
        publisher: EventPublisher | None = None,
    ) -> None:
        if not isinstance(authority, AdbTransportListObservationAuthority):
            raise TypeError(
                "authority must satisfy AdbTransportListObservationAuthority"
            )
        if publisher is not None and not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher or be None")

        self._authority = authority
        self._publisher = publisher

    @property
    def authority(self) -> AdbTransportListObservationAuthority:
        """Observation authority used for all fenced transport-list commits."""

        return self._authority

    def capture_basis(
        self,
        server: AdbServerIdentity,
    ) -> AdbTransportListObservationBasis | None:
        """Capture the authority basis immediately before a raw observation is read.

        ``None`` means ``server`` is no longer the authoritative active server lifetime and the
        caller must not start another read for that binding.
        """

        if not isinstance(server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")
        return self._authority.capture_transport_list_basis(server)

    def observe(
        self,
        basis: AdbTransportListObservationBasis,
        transport_list: AdbTransportList,
    ) -> AdbTransportListCoordinatedObservationResult:
        """Commit raw transport-list data when the captured authority fences still hold."""

        if not isinstance(basis, AdbTransportListObservationBasis):
            raise TypeError("basis must be AdbTransportListObservationBasis")
        if not isinstance(transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")

        result = self._authority.observe_transport_list(basis, transport_list)
        if isinstance(result, AdbTransportListObserved) and self._publisher is not None:
            self._publisher.publish(result)
        return result


__all__ = [
    "AdbTransportListCoordinator",
    "AdbTransportListCoordinatedObservationResult",
    "AdbTransportListObservationServerConflict",
]
