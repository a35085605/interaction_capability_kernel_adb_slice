from __future__ import annotations

from collections.abc import Callable
import socket
from time import monotonic

from lifecycle.resource.cleanup import cleanup_reverse
from lifecycle.resource.result import ResourceCleanupResult
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
        if outcome.interruption is not None:
            return RequirementAcquireInterrupted(
                outcome.interruption,
                resources,
                outcome.cleanup_errors,
                outcome.error,
            )
        if isinstance(outcome.error, Exception):
            return RequirementAcquireFailed(
                outcome.error, resources, outcome.cleanup_errors
            )
        return RequirementAcquireInterrupted(
            outcome.error, resources, outcome.cleanup_errors
        )

    def cleanup(
        self,
        resources: PhysicalResources[AospTrackDevicesSession],
    ) -> ResourceCleanupResult[AospTrackDevicesSession]:
        """Close retained sessions while reporting exact remaining ownership."""

        return cleanup_reverse(resources, self._cleanup_session)

    @staticmethod
    def _cleanup_session(
        resource: AospTrackDevicesSession,
    ) -> ResourceCleanupResult[AospTrackDevicesSession]:
        if not isinstance(resource, AospTrackDevicesSession):
            return ResourceCleanupResult.blocked(
                (resource,),
                errors=(TypeError("resources must contain only AospTrackDevicesSession values"),),
            )
        try:
            resource.close()
        except Exception as exc:
            if resource.closed:
                return ResourceCleanupResult.complete(errors=(exc,))
            return ResourceCleanupResult.blocked((resource,), errors=(exc,))
        return ResourceCleanupResult.complete()


__all__ = ["AospTrackDevicesSessionDriver"]
