from __future__ import annotations

from collections.abc import Callable
import socket
from time import monotonic

from lifecycle.resource.cleanup import cleanup_reverse
from lifecycle.resource.driver import (
    PhysicalResources,
    RequirementAcquireFailed,
    RequirementAcquireInterrupted,
    RequirementAcquireResult,
    RequirementAcquireSucceeded,
)
from adb.aosp.io.track_devices import (
    AospTrackDevicesSession,
    AospTrackDevicesSessionOpenFailed,
    AospTrackDevicesSessionOpener,
)
from networking import TcpEndpoint


_Clock = Callable[[], float]
_Resolver = Callable[..., list[tuple]]
_SocketFactory = Callable[..., socket.socket]


class AospTrackDevicesSessionDriver:
    """Adapt raw AOSP session opening into lifecycle retained-resource semantics."""

    def __init__(
        self,
        startup_timeout_seconds: float = 5.0,
        *,
        _resolver: _Resolver = socket.getaddrinfo,
        _socket_factory: _SocketFactory = socket.socket,
        _clock: _Clock = monotonic,
    ) -> None:
        self._opener = AospTrackDevicesSessionOpener(
            startup_timeout_seconds,
            _resolver=_resolver,
            _socket_factory=_socket_factory,
            _clock=_clock,
        )

    def acquire(
        self,
        server_endpoint: TcpEndpoint,
    ) -> RequirementAcquireResult[AospTrackDevicesSession]:
        outcome = self._opener.open(server_endpoint)
        if isinstance(outcome, AospTrackDevicesSession):
            return RequirementAcquireSucceeded((outcome,))
        if not isinstance(outcome, AospTrackDevicesSessionOpenFailed):
            raise TypeError("session opener returned an unsupported result")

        resources = (
            ()
            if outcome.retained_session is None
            else (outcome.retained_session,)
        )
        if isinstance(outcome.error, Exception):
            return RequirementAcquireFailed(outcome.error, resources)
        return RequirementAcquireInterrupted(outcome.error, resources)

    def cleanup(
        self,
        resources: PhysicalResources[AospTrackDevicesSession],
    ) -> None:
        """Close every retained session, tolerating retries after partial cleanup."""

        cleanup_reverse(resources, self._close_session)

    @staticmethod
    def _close_session(resource: AospTrackDevicesSession) -> None:
        if not isinstance(resource, AospTrackDevicesSession):
            raise TypeError("resources must contain only AospTrackDevicesSession values")
        resource.close()


__all__ = ["AospTrackDevicesSessionDriver"]
