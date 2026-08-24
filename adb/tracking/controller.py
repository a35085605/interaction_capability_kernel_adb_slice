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
from adb.server.identity import AdbServer
from adb.tracking.snapshot.identity import (
    AdbDevicesSnapshot,
    AdbDevicesSnapshotEpoch,
)
from adb.tracking.snapshot.model import AdbDevicesRecord
from adb.tracking.device_tracker import AdbDeviceTracker, AdbDeviceTrackerStream
from adb.tracking.signal import (
    AdbDevicesSnapshotObserved,
    AdbDevicesTrackingFailed,
    AdbDevicesTrackingFailure,
    AdbDevicesTrackingStarted,
    AdbDevicesTrackingStopped,
)
from eventing import EventPublisher


_DeviceTrackerFactory = Callable[[AdbServer], AdbDeviceTracker]
_ThreadFactory = Callable[..., Thread]


def _default_thread_factory(*args, **kwargs) -> Thread:
    thread = Thread(*args, **kwargs)
    thread.daemon = True
    return thread


@runtime_checkable
class AdbDevicesTrackingController(Protocol):
    """Single-use controller for ADB devices tracking within one server lifetime."""

    @property
    def server(self) -> AdbServer: ...

    @property
    def active(self) -> bool: ...

    def start(self) -> None: ...

    def close(self) -> None: ...


class SmartSocketAdbDevicesTrackingController:
    """Control one smart-socket track-devices stream for one server lifetime.

    ``start`` establishes the stream and synchronously publishes its initial complete snapshot
    before returning. Subsequent snapshots are consumed by the worker. A controller is single-use.
    Stop or failure is terminal; ``close`` closes the device tracker and joins the worker before
    returning.
    """

    def __init__(
        self,
        server: AdbServer,
        publisher: EventPublisher,
        startup_timeout_seconds: float = 5.0,
        *,
        devices_snapshot_epoch_issuer: EpochIssuer[AdbDevicesSnapshotEpoch],
        _device_tracker_factory: _DeviceTrackerFactory | None = None,
        _thread_factory: _ThreadFactory = _default_thread_factory,
    ) -> None:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        if not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher")
        if not isinstance(devices_snapshot_epoch_issuer, EpochIssuer):
            raise TypeError("devices_snapshot_epoch_issuer must satisfy EpochIssuer")
        if _device_tracker_factory is not None and not callable(_device_tracker_factory):
            raise TypeError("_device_tracker_factory must be callable or None")
        if not callable(_thread_factory):
            raise TypeError("_thread_factory must be callable")
        self.server = server
        self.endpoint = server.endpoint
        self.startup_timeout_seconds = startup_timeout_seconds
        self._publisher = publisher
        self._devices_snapshot_epoch_issuer = devices_snapshot_epoch_issuer
        self._device_tracker_factory = _device_tracker_factory
        self._thread_factory = _thread_factory
        self._lock = Lock()
        self._started = False
        self._active_device_tracker: AdbDeviceTracker | None = None
        self._active_thread: Thread | None = None
        self._closed = False

    @property
    def active(self) -> bool:
        with self._lock:
            return not self._closed and self._active_thread is not None

    def start(self) -> None:
        """Start this tracking controller and return after its initial snapshot is published."""

        with self._lock:
            if self._closed:
                raise RuntimeError("ADB devices tracking controller is closed")
            if self._started:
                raise RuntimeError(
                    "ADB devices tracking controller is single-use and already started"
                )
            device_tracker = self._create_device_tracker()
            self._started = True
            self._active_device_tracker = device_tracker

        try:
            stream = device_tracker.open()
        except BaseException:
            self._abort_start(device_tracker)
            raise

        if stream is None:
            self._abort_start(device_tracker)
            raise RuntimeError(
                "ADB devices tracking controller was closed before its initial snapshot "
                "was established"
            )

        startup_complete = Event()
        startup_errors: list[BaseException] = []
        try:
            thread = self._thread_factory(
                target=self._run,
                args=(device_tracker, stream, startup_complete, startup_errors),
                name=(
                    "adb-track-devices-"
                    f"{self.endpoint.host}-{self.endpoint.port}-{self.server.epoch}"
                ),
            )
        except BaseException:
            stream.close()
            self._abort_start(device_tracker)
            raise

        try:
            with self._lock:
                if self._closed or self._active_device_tracker is not device_tracker:
                    if self._active_device_tracker is device_tracker:
                        self._active_device_tracker = None
                    raise RuntimeError(
                        "ADB devices tracking controller was closed before its worker could start"
                    )
                self._active_thread = thread
                try:
                    thread.start()
                except BaseException:
                    self._active_thread = None
                    self._active_device_tracker = None
                    self._closed = True
                    raise
        except BaseException:
            stream.close()
            device_tracker.close()
            raise

        startup_complete.wait()
        if startup_errors:
            if thread is not current_thread():
                thread.join()
            raise startup_errors[0]

    def close(self) -> None:
        """Close this tracking controller and wait for its worker to stop."""

        with self._lock:
            device_tracker = self._active_device_tracker
            thread = self._active_thread
            self._closed = True
        if device_tracker is not None:
            device_tracker.close()
        if thread is not None and thread is not current_thread():
            thread.join()

    def _create_device_tracker(self) -> AdbDeviceTracker:
        factory = self._device_tracker_factory
        device_tracker = (
            AdbDeviceTracker(
                self.server,
                startup_timeout_seconds=self.startup_timeout_seconds,
            )
            if factory is None
            else factory(self.server)
        )
        if not isinstance(device_tracker, AdbDeviceTracker):
            raise TypeError("device tracker factory must return AdbDeviceTracker")
        return device_tracker

    def _abort_start(self, device_tracker: AdbDeviceTracker) -> None:
        with self._lock:
            if self._active_device_tracker is device_tracker:
                self._active_device_tracker = None
            self._active_thread = None
            self._closed = True
        device_tracker.close()

    def _run(
        self,
        device_tracker: AdbDeviceTracker,
        stream: AdbDeviceTrackerStream,
        startup_complete: Event,
        startup_errors: list[BaseException],
    ) -> None:
        server = self.server
        terminal: object | None = None
        startup_succeeded = False
        try:
            if not self._can_publish_from(device_tracker):
                raise RuntimeError(
                    "ADB devices tracking controller was closed before its initial snapshot "
                    "was published"
                )

            self._publisher.publish(AdbDevicesTrackingStarted(server))
            self._publisher.publish(
                AdbDevicesSnapshotObserved(
                    server,
                    self._snapshot(stream.initial_record),
                )
            )
            startup_succeeded = True
            startup_complete.set()

            for record in stream.records():
                if not self._can_publish_from(device_tracker):
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
            publish_terminal = self._mark_terminal(device_tracker)

        if startup_succeeded and terminal is not None and publish_terminal:
            self._publisher.publish(terminal)

    def _snapshot(self, record: AdbDevicesRecord) -> AdbDevicesSnapshot:
        if not isinstance(record, AdbDevicesRecord):
            raise TypeError("record must be AdbDevicesRecord")
        return AdbDevicesSnapshot(
            record,
            self._devices_snapshot_epoch_issuer.issue(),
        )

    def _can_publish_from(self, device_tracker: AdbDeviceTracker) -> bool:
        with self._lock:
            return not self._closed and self._active_device_tracker is device_tracker

    def _mark_terminal(self, device_tracker: AdbDeviceTracker) -> bool:
        with self._lock:
            publish_terminal = (
                not self._closed and self._active_device_tracker is device_tracker
            )
            if self._active_device_tracker is device_tracker:
                self._active_device_tracker = None
                self._active_thread = None
            self._closed = True
            return publish_terminal


__all__ = [
    "AdbDevicesTrackingController",
    "SmartSocketAdbDevicesTrackingController",
]
