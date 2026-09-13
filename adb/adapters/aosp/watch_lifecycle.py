from __future__ import annotations

from collections.abc import Callable, Iterator
import socket
from time import monotonic

from _lifecycle_new.resource.driver import (
    PhysicalResources,
    RequirementAcquireFailed,
    RequirementAcquireResult,
)
from _lifecycle_new.resource.manager import ResolvedResourceProvider
from adb.adapters.aosp.track_devices import to_transport_list
from adb.aosp.io.track_devices import (
    AospTrackDevicesSession,
    AospTrackDevicesSessionDriver,
)
from adb.errors import (
    AdbProtocolError,
    AdbServerConnectionError,
    AdbServiceError,
    AdbTimeoutError,
)
from adb.transport_list.model import AdbTransportList
from adb.transport_list.watch.error import AdbTransportListWatchError
from adb.transport_list.watch.failure import (
    AdbTransportListWatchFailure,
    AdbTransportListWatchProtocolFailure,
    AdbTransportListWatchServerConnectionFailure,
    AdbTransportListWatchServiceFailure,
)
from adb.transport_list.watch.generation import AdbTransportListWatchGenerationIssuer
from adb.transport_list.watch.request import AdbTransportListWatchRequest
from adb.transport_list.watch.stream import AdbTransportListWatchStream
from adb.transport_list.watch.template import (
    AdbTransportListWatchAcquireError,
    AdbTransportListWatchLifecycleTemplate,
)
from networking import TcpAddress


_Clock = Callable[[], float]


def _watch_failure(exc: BaseException) -> AdbTransportListWatchFailure | None:
    diagnostic = str(exc).strip() or None
    if isinstance(exc, AdbProtocolError):
        return AdbTransportListWatchProtocolFailure(diagnostic)
    if isinstance(exc, AdbServiceError):
        return AdbTransportListWatchServiceFailure(diagnostic)
    if isinstance(exc, (AdbServerConnectionError, AdbTimeoutError, OSError)):
        return AdbTransportListWatchServerConnectionFailure(diagnostic)
    return None


def _watch_error(exc: BaseException) -> AdbTransportListWatchError | None:
    failure = _watch_failure(exc)
    return None if failure is None else AdbTransportListWatchError(failure)


class _AospTrackDevicesWatchRequirementsResolver:
    """Resolve one watch request into its single AOSP track-devices session requirement."""

    def resolve(self, request: AdbTransportListWatchRequest) -> tuple[TcpAddress, ...]:
        if not isinstance(request, AdbTransportListWatchRequest):
            raise TypeError("request must be AdbTransportListWatchRequest")
        return (request.server_address,)


class _AospTrackDevicesWatchDriver:
    """Translate AOSP session acquisition failures into watch-domain failures."""

    def __init__(self, driver: AospTrackDevicesSessionDriver) -> None:
        if not isinstance(driver, AospTrackDevicesSessionDriver):
            raise TypeError("driver must be AospTrackDevicesSessionDriver")
        self._driver = driver

    def acquire(
        self,
        server_address: TcpAddress,
    ) -> RequirementAcquireResult[AospTrackDevicesSession]:
        outcome = self._driver.acquire(server_address)
        if not isinstance(outcome, RequirementAcquireFailed):
            return outcome

        failure = _watch_failure(outcome.error)
        if failure is None:
            return outcome
        return RequirementAcquireFailed(
            AdbTransportListWatchAcquireError(failure),
            outcome.resources,
        )

    def cleanup(
        self,
        resources: PhysicalResources[AospTrackDevicesSession],
    ) -> None:
        self._driver.cleanup(resources)


class _AospTransportListWatchCapability:
    """Narrow watch capability projected from one lifecycle-owned AOSP session."""

    __slots__ = ("__session", "_initial", "_updates")

    def __init__(self, session: AospTrackDevicesSession) -> None:
        if not isinstance(session, AospTrackDevicesSession):
            raise TypeError("session must be AospTrackDevicesSession")
        self.__session = session
        self._initial = to_transport_list(session.initial)
        self._updates = self._iterate_updates()

    @property
    def initial(self) -> AdbTransportList:
        return self._initial

    def updates(self) -> Iterator[AdbTransportList]:
        return self._updates

    def _iterate_updates(self) -> Iterator[AdbTransportList]:
        try:
            for devices in self.__session.updates():
                yield to_transport_list(devices)
        except BaseException as exc:
            error = _watch_error(exc)
            if error is not None:
                raise error from exc
            raise


class _AospTransportListWatchProjector:
    """Project the public watch capability without exposing session ownership APIs."""

    def project(
        self,
        request: AdbTransportListWatchRequest,
        resources: PhysicalResources[AospTrackDevicesSession],
    ) -> AdbTransportListWatchStream:
        if not isinstance(request, AdbTransportListWatchRequest):
            raise TypeError("request must be AdbTransportListWatchRequest")
        if len(resources) != 1 or not isinstance(resources[0], AospTrackDevicesSession):
            raise TypeError("watch acquisition must provide exactly one AospTrackDevicesSession")
        return _AospTransportListWatchCapability(resources[0])


class SmartSocketAdbTransportListWatchLifecycle(
    AdbTransportListWatchLifecycleTemplate[AospTrackDevicesSession]
):
    """Transport-list watch backed by one lifecycle-owned AOSP track-devices session."""

    def __init__(
        self,
        generation_issuer: AdbTransportListWatchGenerationIssuer,
        *,
        startup_timeout_seconds: float = 5.0,
        _resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
        _socket_factory: Callable[..., socket.socket] = socket.socket,
        _clock: _Clock = monotonic,
    ) -> None:
        if not isinstance(generation_issuer, AdbTransportListWatchGenerationIssuer):
            raise TypeError("generation_issuer must be AdbTransportListWatchGenerationIssuer")
        if not callable(_resolver) or not callable(_socket_factory) or not callable(_clock):
            raise TypeError("resolver, socket factory, and clock must be callable")

        driver = _AospTrackDevicesWatchDriver(
            AospTrackDevicesSessionDriver(
                startup_timeout_seconds,
                _resolver=_resolver,
                _socket_factory=_socket_factory,
                _clock=_clock,
            )
        )
        resource_provider = ResolvedResourceProvider(
            _AospTrackDevicesWatchRequirementsResolver(),
            driver,
        )
        super().__init__(
            generation_issuer,
            resource_provider,
            _AospTransportListWatchProjector(),
        )


__all__ = ["SmartSocketAdbTransportListWatchLifecycle"]
