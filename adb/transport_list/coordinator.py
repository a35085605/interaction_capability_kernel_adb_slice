from __future__ import annotations

from adb.transport_list.model import AdbTransportList
from adb.transport_list.session_identity import AdbTransportListSessionIdentity
from adb.transport_list.state import (
    AdbTransportListCoordinatedObservationResult,
    AdbTransportListInvalidated,
    AdbTransportListObservationResult,
    AdbTransportListObserved,
    AdbTransportListSessionAuthority,
    AdbTransportListSessionRevocationResult,
    AdbTransportListSessionRevoked,
)
from eventing import EventPublisher


class AdbTransportListCoordinator:
    """Orchestrate transport-list observations fenced by their producer session."""

    def __init__(
        self,
        authority: AdbTransportListSessionAuthority,
        *,
        publisher: EventPublisher | None = None,
    ) -> None:
        if not isinstance(authority, AdbTransportListSessionAuthority):
            raise TypeError("authority must satisfy AdbTransportListSessionAuthority")
        if publisher is not None and not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher or be None")
        self._authority = authority
        self._publisher = publisher

    @property
    def authority(self) -> AdbTransportListSessionAuthority:
        return self._authority

    def begin(self) -> AdbTransportListSessionIdentity | None:
        """Acquire a fresh producer session while admission remains open."""

        result = self._authority.begin_session()
        if result is None:
            return None
        if self._publisher is not None:
            if result.superseded_session is not None:
                self._publisher.publish(
                    AdbTransportListSessionRevoked(
                        result.superseded_session,
                        result.invalidated_identity,
                    )
                )
            if result.invalidated_identity is not None:
                self._publisher.publish(AdbTransportListInvalidated(result.invalidated_identity))
        return result.session

    def can_observe_update(self, session: AdbTransportListSessionIdentity) -> bool:
        """Whether ``session`` is currently allowed to enter another blocking update read."""

        if not isinstance(session, AdbTransportListSessionIdentity):
            raise TypeError("session must be AdbTransportListSessionIdentity")
        return self._authority.can_observe_update(session)

    def observe_initial(
        self,
        session: AdbTransportListSessionIdentity,
        transport_list: AdbTransportList,
    ) -> AdbTransportListObservationResult:
        return self._observe(session, transport_list, initial=True)

    def observe_update(
        self,
        session: AdbTransportListSessionIdentity,
        transport_list: AdbTransportList,
    ) -> AdbTransportListObservationResult:
        return self._observe(session, transport_list, initial=False)

    def revoke(
        self,
        session: AdbTransportListSessionIdentity,
    ) -> AdbTransportListSessionRevocationResult:
        if not isinstance(session, AdbTransportListSessionIdentity):
            raise TypeError("session must be AdbTransportListSessionIdentity")
        result = self._authority.revoke_session(session)
        if isinstance(result, AdbTransportListSessionRevoked) and self._publisher is not None:
            self._publisher.publish(result)
            if result.invalidated_identity is not None:
                self._publisher.publish(AdbTransportListInvalidated(result.invalidated_identity))
        return result

    def _observe(
        self,
        session: AdbTransportListSessionIdentity,
        transport_list: AdbTransportList,
        *,
        initial: bool,
    ) -> AdbTransportListObservationResult:
        if not isinstance(session, AdbTransportListSessionIdentity):
            raise TypeError("session must be AdbTransportListSessionIdentity")
        if not isinstance(transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")

        result = (
            self._authority.observe_initial(session, transport_list)
            if initial
            else self._authority.observe_update(session, transport_list)
        )
        if isinstance(result, AdbTransportListObserved) and self._publisher is not None:
            self._publisher.publish(result)
        return result


__all__ = [
    "AdbTransportListCoordinator",
    "AdbTransportListCoordinatedObservationResult",
]
