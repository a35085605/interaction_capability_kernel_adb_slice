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
from adb.tracking.watch import (
    AdbTransportList,
    AdbTransportListWatch,
    AdbTransportListWatcher,
)
from adb.tracking.snapshot.identity import (
    AdbDevicesSnapshot,
    AdbDevicesSnapshotEpoch,
)
from adb.adapters.aosp.track_devices import SmartSocketAdbTransportListWatcher
from adb.tracking.signal import (
    AdbDevicesSnapshotObserved,
    AdbDevicesTrackingFailed,
    AdbDevicesTrackingFailure,
    AdbDevicesTrackingStarted,
    AdbDevicesTrackingStopped,
)
from eventing import EventPublisher


_TransportListWatcherFactory = Callable[[TcpAddress], AdbTransportListWatcher]
_ThreadFactory = Callable[..., Thread]


def _default_thread_factory(*args, **kwargs) -> Thread:
    thread = Thread(*args, **kwargs)
    thread.daemon = True
    return thread


@runtime_checkable
class AdbDevicesTrackingController(Protocol):
    """Control one transport-list tracking lifetime for one ADB server lifetime."""

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
    """Single-use controller for one smart-socket track-devices watch, publishing initial and
    subsequent snapshots until terminal stop or failure.
    """

    def __init__(
        self,
        server: AdbServerLifetime,
        publisher: EventPublisher,
        startup_timeout_seconds: float = 5.0,
        *,
        devices_snapshot_epoch_issuer: EpochIssuer[AdbDevicesSnapshotEpoch],
        _watcher_factory: _TransportListWatcherFactory | None = None,
        _thread_factory: _ThreadFactory = _default_thread_factory,
    ) -> None:
        if not isinstance(server, AdbServerLifetime):
            raise TypeError("server must be AdbServerLifetime")
        if not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher")
        if not isinstance(devices_snapshot_epoch_issuer, EpochIssuer):
            raise TypeError("devices_snapshot_epoch_issuer must satisfy EpochIssuer")
        if _watcher_factory is not None and not callable(_watcher_factory):
            raise TypeError("_watcher_factory must be callable or None")
        if not callable(_thread_factory):
            raise TypeError("_thread_factory must be callable")
        self.server = server
        self.endpoint = server.endpoint
        self.startup_timeout_seconds = startup_timeout_seconds
        self._publisher = publisher
        self._devices_snapshot_epoch_issuer = devices_snapshot_epoch_issuer
        self._watcher_factory = _watcher_factory
        self._thread_factory = _thread_factory
        self._lock = Lock()
        self._started = False
        self._active_watcher: AdbTransportListWatcher | None = None
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
            watcher = self._create_watcher()
            self._started = True
            self._active_watcher = watcher

        try:
            watch = watcher.open()
        except BaseException:
            self._abort_start(watcher)
            raise

        if watch is None:
            self._abort_start(watcher)
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
                    watcher,
                    watch,
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
            watch.close()
            self._abort_start(watcher)
            raise

        try:
            with self._lock:
                if self._closed or self._active_watcher is not watcher:
                    if self._active_watcher is watcher:
                        self._active_watcher = None
                    raise RuntimeError(
                        "ADB devices tracking controller was stopped before its worker could start"
                    )
                self._active_thread = thread
                try:
                    thread.start()
                except BaseException:
                    self._active_thread = None
                    self._active_watcher = None
                    self._closed = True
                    raise
        except BaseException:
            watch.close()
            watcher.close()
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
            watcher = self._active_watcher
            thread = self._active_thread
            self._closed = True
        if watcher is not None:
            watcher.close()
        if thread is not None and thread is not current_thread():
            thread.join()

    def _create_watcher(self) -> AdbTransportListWatcher:
        factory = self._watcher_factory
        watcher = (
            SmartSocketAdbTransportListWatcher(
                self.endpoint,
                startup_timeout_seconds=self.startup_timeout_seconds,
            )
            if factory is None
            else factory(self.endpoint)
        )
        if not isinstance(watcher, AdbTransportListWatcher):
            raise TypeError(
                "tracking watcher factory must return AdbTransportListWatcher"
            )
        if watcher.address != self.endpoint:
            raise ValueError("tracking watcher factory returned a mismatched server endpoint")
        return watcher

    def _abort_start(self, watcher: AdbTransportListWatcher) -> None:
        with self._lock:
            if self._active_watcher is watcher:
                self._active_watcher = None
            self._active_thread = None
            self._closed = True
        watcher.close()

    def _run(
        self,
        watcher: AdbTransportListWatcher,
        watch: AdbTransportListWatch,
        startup_complete: Event,
        startup_snapshots: list[AdbDevicesSnapshot],
        startup_errors: list[BaseException],
    ) -> None:
        server = self.server
        terminal: object | None = None
        startup_succeeded = False
        try:
            if not self._can_publish_from(watcher):
                raise RuntimeError(
                    "ADB devices tracking controller was stopped before its initial snapshot "
                    "was published"
                )

            self._publisher.publish(AdbDevicesTrackingStarted(server))
            initial_snapshot = self._snapshot(watch.initial)
            self._publisher.publish(
                AdbDevicesSnapshotObserved(
                    server,
                    initial_snapshot,
                )
            )
            startup_snapshots.append(initial_snapshot)
            startup_succeeded = True
            startup_complete.set()

            for transport_list in watch.updates():
                if not self._can_publish_from(watcher):
                    break
                self._publisher.publish(
                    AdbDevicesSnapshotObserved(server, self._snapshot(transport_list))
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
            watch.close()
            publish_terminal = self._mark_terminal(watcher)

        if startup_succeeded and terminal is not None and publish_terminal:
            self._publisher.publish(terminal)

    def _snapshot(
        self,
        observations: AdbTransportList,
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

    def _can_publish_from(self, watcher: AdbTransportListWatcher) -> bool:
        with self._lock:
            return not self._closed and self._active_watcher is watcher

    def _mark_terminal(self, watcher: AdbTransportListWatcher) -> bool:
        with self._lock:
            publish_terminal = (
                not self._closed and self._active_watcher is watcher
            )
            if self._active_watcher is watcher:
                self._active_watcher = None
                self._active_thread = None
            self._closed = True
            return publish_terminal


__all__ = [
    "AdbDevicesTrackingController",
    "SmartSocketAdbDevicesTrackingController",
]
