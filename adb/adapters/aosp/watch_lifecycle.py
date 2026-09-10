from __future__ import annotations

from collections.abc import Callable, Iterator
import socket
from threading import Event
from time import monotonic

from adb.adapters.aosp.track_devices import to_transport_list
from adb.aosp.io.track_devices import (
    AospTrackDevicesOpenCancelled,
    AospTrackDevicesOpenCleanupRequired,
    AospTrackDevicesStream,
    AospTrackDevicesStreamFactory,
)
from adb._lifecycle import ResourceScope
from adb.cleanup import CleanupHandoff
from adb.errors import (
    AdbProtocolError,
    AdbServerConnectionError,
    AdbServiceError,
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
from adb.transport_list.watch.template import (
    AdbTransportListWatchAcquireError,
    AdbTransportListWatchAcquireInterruptedError,
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
    if isinstance(exc, (AdbServerConnectionError, OSError)):
        return AdbTransportListWatchServerConnectionFailure(diagnostic)
    return None


def _watch_error(exc: BaseException) -> AdbTransportListWatchError | None:
    failure = _watch_failure(exc)
    return None if failure is None else AdbTransportListWatchError(failure)


class _AospTransportListWatchResource:
    """Lifecycle-owned watch resource translating one AOSP stream into transport-list snapshots."""

    __slots__ = (
        "_stream",
        "_initial",
        "_schedule_cleanup",
        "_updates",
    )

    def __init__(
        self,
        stream: AospTrackDevicesStream,
        schedule_cleanup: Callable[["_AospTransportListWatchResource"], None],
    ) -> None:
        if not isinstance(stream, AospTrackDevicesStream):
            raise TypeError("stream must be AospTrackDevicesStream")
        if not callable(schedule_cleanup):
            raise TypeError("schedule_cleanup must be callable")
        self._stream = stream
        self._initial = to_transport_list(stream.initial)
        self._schedule_cleanup = schedule_cleanup
        self._updates = self._iterate_updates()

    @property
    def initial(self) -> AdbTransportList:
        return self._initial

    def updates(self) -> Iterator[AdbTransportList]:
        return self._updates

    def _iterate_updates(self) -> Iterator[AdbTransportList]:
        try:
            for devices in self._stream.updates():
                yield to_transport_list(devices)
        except BaseException as exc:
            self._schedule_cleanup(self)
            error = _watch_error(exc)
            if error is not None:
                raise error from exc
            raise

    def close(self) -> object | None:
        return self._stream.close()


class SmartSocketAdbTransportListWatchLifecycle(AdbTransportListWatchLifecycleTemplate):
    """Generation-fenced transport-list authority over one AOSP track-devices stream.

    The shared lifecycle template owns generation, acquire/release, and cleanup authority. AOSP
    smart-socket connection, handshake, framing, startup timeout, interruption, and socket cleanup
    are centralized in ``AospTrackDevicesStreamFactory`` / ``AospTrackDevicesStream``.
    """

    def __init__(
        self,
        generation_issuer: AdbTransportListWatchGenerationIssuer,
        *,
        cleanup_handoff: CleanupHandoff,
        startup_timeout_seconds: float = 5.0,
        _resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
        _socket_factory: Callable[..., socket.socket] = socket.socket,
        _clock: _Clock = monotonic,
    ) -> None:
        if not isinstance(generation_issuer, AdbTransportListWatchGenerationIssuer):
            raise TypeError("generation_issuer must be AdbTransportListWatchGenerationIssuer")
        if not callable(_resolver) or not callable(_socket_factory) or not callable(_clock):
            raise TypeError("resolver, socket factory, and clock must be callable")
        super().__init__(generation_issuer, cleanup_handoff=cleanup_handoff)
        self._stream_factory = AospTrackDevicesStreamFactory(
            startup_timeout_seconds,
            _resolver=_resolver,
            _socket_factory=_socket_factory,
            _clock=_clock,
        )

    def _obtain_resource(
        self,
        endpoint: TcpAddress,
        cancellation: Event,
        resources: ResourceScope,
    ) -> _AospTransportListWatchResource:
        stream: AospTrackDevicesStream | None = None
        try:
            stream = self._stream_factory.open(endpoint, cancellation)
            return _AospTransportListWatchResource(
                stream,
                lambda resource: self._schedule_cleanup(resource),
            )
        except AospTrackDevicesOpenCleanupRequired as exc:
            resources.adopt_handoff(exc.cleanup_resource)
            primary_error = exc.primary_error
            if isinstance(primary_error, AospTrackDevicesOpenCancelled):
                raise AdbTransportListWatchAcquireInterruptedError() from exc
            failure = _watch_failure(primary_error)
            if failure is not None:
                raise AdbTransportListWatchAcquireError(failure) from exc
            raise primary_error from exc
        except AospTrackDevicesOpenCancelled as exc:
            raise AdbTransportListWatchAcquireInterruptedError() from exc
        except BaseException as exc:
            if stream is not None:
                resources.adopt(stream, stream.close)
            failure = _watch_failure(exc)
            if failure is not None:
                raise AdbTransportListWatchAcquireError(failure) from exc
            raise


__all__ = ["SmartSocketAdbTransportListWatchLifecycle"]
