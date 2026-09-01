from __future__ import annotations

from collections.abc import Callable
from threading import Event, Lock, Thread, current_thread
from typing import Protocol, runtime_checkable

from adb.epoch import EpochIssuer
from adb.errors import (
    AdbProtocolError,
    AdbServerConnectionError,
    AdbServiceError,
)
from networking import TcpAddress
from adb.server.lifetime import AdbServerLifetime
from adb.tracking.observation import AdbTrackedTransportObservation
from adb.tracking.snapshot.identity import (
    AdbDevicesSnapshot,
    AdbDevicesSnapshotEpoch,
)
from adb.adapters.aosp.tracking import (
    AdbDevicesTrackingBackend,
    AdbDevicesTrackingBackendStream,
    SmartSocketAdbDevicesTrackingBackend,
)
from adb.tracking.signal import (
    AdbDevicesSnapshotObserved,
    AdbDevicesTrackingFailed,
    AdbDevicesTrackingFailure,
    AdbDevicesTrackingStarted,
    AdbDevicesTrackingStopped,
)
from eventing import EventPublisher


_TrackingBackendFactory = Callable[[TcpAddress], AdbDevicesTrackingBackend]
_ThreadFactory = Callable[..., Thread]


def _default_thread_factory(*args, **kwargs) -> Thread:
    thread = Thread(*args, **kwargs)
    thread.daemon = True
    return thread


@runtime_checkable
class AdbDevicesTrackingController(Protocol):
    """Control one devices-tracking lifetime for one ADB server lifetime."""

    @property
    def server(self) -> AdbServerLifetime:
        ...

    @property
    def active(self) -> bool:
        ...

    def start(self) -> AdbDevicesSnapshot:
        """Establish tracking and return its initial complete snapshot."""
        ...

    def stop(self) -> None:
        """Stop tracking and return after its worker has terminated."""
        ...


class SmartSocketAdbDevicesTrackingController:
    """Single-use controller for one smart-socket track-devices stream, publishing initial and
    subsequent snapshots until terminal stop or failure.
    """

    def __init__(
        self,
        server: AdbServerLifetime,
        publisher: EventPublisher,
        startup_timeout_seconds: float = 5.0,
        *,
        devices_snapshot_epoch_issuer: EpochIssuer[AdbDevicesSnapshotEpoch],
        _backend_factory: _TrackingBackendFactory | None = None,
        _thread_factory: _ThreadFactory = _default_thread_factory,
    ) -> None:
        if not isinstance(server, AdbServerLifetime):
            raise TypeError("server must be AdbServerLifetime")
        if not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher")
        if not isinstance(devices_snapshot_epoch_issuer, EpochIssuer):
            raise TypeError("devices_snapshot_epoch_issuer must satisfy EpochIssuer")
        if _backend_factory is not None and not callable(_backend_factory):
            raise TypeError("_backend_factory must be callable or None")
        if not callable(_thread_factory):
            raise TypeError("_thread_factory must be callable")
        self.server = server
        self.endpoint = server.endpoint
        self.startup_timeout_seconds = startup_timeout_seconds
        self._publisher = publisher
        self._devices_snapshot_epoch_issuer = devices_snapshot_epoch_issuer
        self._backend_factory = _backend_factory
        self._thread_factory = _thread_factory
        self._lock = Lock()
        self._started = False
        self._active_backend: AdbDevicesTrackingBackend | None = None
        self._active_thread: Thread | None = None
        self._closed = False

    @property
    def active(self) -> bool:
        with self._lock:
            return not self._closed and self._active_thread is not None

    def start(self) -> AdbDevicesSnapshot:
        """Establish tracking and return its initial complete snapshot."""

        with self._lock:
            if self._closed:
                raise RuntimeError("ADB devices tracking controller is stopped")
            if self._started:
                raise RuntimeError(
                    "ADB devices tracking controller is single-use and already started"
                )
            backend = self._create_backend()
            self._started = True
            self._active_backend = backend

        try:
            stream = backend.open()
        except BaseException:
            self._abort_start(backend)
            raise

        if stream is None:
            self._abort_start(backend)
            raise RuntimeError(
                "ADB devices tracking controller was stopped before its initial snapshot "
                "was established"
            )

        startup_complete = Event()
        startup_snapshots: list[AdbDevicesSnapshot] = []
        startup_errors: list[BaseException] = []
        try:
            thread = self._thread_factory(
                target=self._run,
                args=(
                    backend,
                    stream,
                    startup_complete,
                    startup_snapshots,
                    startup_errors,
                ),
                name=(
                    "adb-track-devices-"
                    f"{self.endpoint.host}-{self.endpoint.port}-{self.server.epoch}"
                ),
            )
        except BaseException:
            stream.close()
            self._abort_start(backend)
            raise

        try:
            with self._lock:
                if self._closed or self._active_backend is not backend:
                    if self._active_backend is backend:
                        self._active_backend = None
                    raise RuntimeError(
                        "ADB devices tracking controller was stopped before its worker could start"
                    )
                self._active_thread = thread
                try:
                    thread.start()
                except BaseException:
                    self._active_thread = None
                    self._active_backend = None
                    self._closed = True
                    raise
        except BaseException:
            stream.close()
            backend.close()
            raise

        startup_complete.wait()
        if startup_errors:
            if thread is not current_thread():
                thread.join()
            raise startup_errors[0]
        if len(startup_snapshots) != 1:
            raise RuntimeError(
                "ADB devices tracking controller did not produce exactly one initial snapshot"
            )
        return startup_snapshots[0]

    def stop(self) -> None:
        """Stop tracking and return after its worker has terminated."""

        with self._lock:
            backend = self._active_backend
            thread = self._active_thread
            self._closed = True
        if backend is not None:
            backend.close()
        if thread is not None and thread is not current_thread():
            thread.join()

    def _create_backend(self) -> AdbDevicesTrackingBackend:
        factory = self._backend_factory
        backend = (
            SmartSocketAdbDevicesTrackingBackend(
                self.endpoint,
                startup_timeout_seconds=self.startup_timeout_seconds,
            )
            if factory is None
            else factory(self.endpoint)
        )
        if not isinstance(backend, AdbDevicesTrackingBackend):
            raise TypeError(
                "tracking backend factory must return AdbDevicesTrackingBackend"
            )
        if backend.address != self.endpoint:
            raise ValueError("tracking backend factory returned a mismatched server endpoint")
        return backend

    def _abort_start(self, backend: AdbDevicesTrackingBackend) -> None:
        with self._lock:
            if self._active_backend is backend:
                self._active_backend = None
            self._active_thread = None
            self._closed = True
        backend.close()

    def _run(
        self,
        backend: AdbDevicesTrackingBackend,
        stream: AdbDevicesTrackingBackendStream,
        startup_complete: Event,
        startup_snapshots: list[AdbDevicesSnapshot],
        startup_errors: list[BaseException],
    ) -> None:
        server = self.server
        terminal: object | None = None
        startup_succeeded = False
        try:
            if not self._can_publish_from(backend):
                raise RuntimeError(
                    "ADB devices tracking controller was stopped before its initial snapshot "
                    "was published"
                )

            self._publisher.publish(AdbDevicesTrackingStarted(server))
            initial_snapshot = self._snapshot(stream.initial_record)
            self._publisher.publish(
                AdbDevicesSnapshotObserved(
                    server,
                    initial_snapshot,
                )
            )
            startup_snapshots.append(initial_snapshot)
            startup_succeeded = True
            startup_complete.set()

            for record in stream.records():
                if not self._can_publish_from(backend):
                    break
                self._publisher.publish(
                    AdbDevicesSnapshotObserved(server, self._snapshot(record))
                )
            terminal = AdbDevicesTrackingStopped(server)
        except AdbServerConnectionError as exc:
            if startup_succeeded:
                terminal = AdbDevicesTrackingFailed(
                    server,
                    AdbDevicesTrackingFailure.SERVER_CONNECTION,
                    str(exc),
                )
            else:
                startup_errors.append(exc)
        except AdbServiceError as exc:
            if startup_succeeded:
                terminal = AdbDevicesTrackingFailed(
                    server,
                    AdbDevicesTrackingFailure.SERVICE,
                    str(exc),
                )
            else:
                startup_errors.append(exc)
        except AdbProtocolError as exc:
            if startup_succeeded:
                terminal = AdbDevicesTrackingFailed(
                    server,
                    AdbDevicesTrackingFailure.PROTOCOL,
                    str(exc),
                )
            else:
                startup_errors.append(exc)
        except BaseException as exc:
            if startup_succeeded:
                raise
            startup_errors.append(exc)
        finally:
            startup_complete.set()
            stream.close()
            publish_terminal = self._mark_terminal(backend)

        if startup_succeeded and terminal is not None and publish_terminal:
            self._publisher.publish(terminal)

    def _snapshot(
        self,
        observations: tuple[AdbTrackedTransportObservation, ...],
    ) -> AdbDevicesSnapshot:
        if not isinstance(observations, tuple) or not all(
            isinstance(row, AdbTrackedTransportObservation) for row in observations
        ):
            raise TypeError(
                "observations must be a tuple of AdbTrackedTransportObservation values"
            )
        return AdbDevicesSnapshot(
            observations=observations,
            epoch=self._devices_snapshot_epoch_issuer.issue(),
        )

    def _can_publish_from(self, backend: AdbDevicesTrackingBackend) -> bool:
        with self._lock:
            return not self._closed and self._active_backend is backend

    def _mark_terminal(self, backend: AdbDevicesTrackingBackend) -> bool:
        with self._lock:
            publish_terminal = (
                not self._closed and self._active_backend is backend
            )
            if self._active_backend is backend:
                self._active_backend = None
                self._active_thread = None
            self._closed = True
            return publish_terminal


__all__ = [
    "AdbDevicesTrackingController",
    "SmartSocketAdbDevicesTrackingController",
]
