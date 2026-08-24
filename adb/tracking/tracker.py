from __future__ import annotations

from collections.abc import Callable
from threading import Event, Lock, Thread, current_thread
from typing import Protocol, runtime_checkable

from adb.errors import (
    AdbProtocolError,
    AdbServerConnectionError,
    AdbServiceError,
)
from adb.server.identity import AdbServer
from adb.tracking.source import AdbTrackDevicesSession, AdbTrackDevicesSource
from adb.tracking.signal import (
    AdbDevicesSnapshotObserved,
    AdbDevicesTrackingFailed,
    AdbDevicesTrackingFailure,
    AdbDevicesTrackingStarted,
    AdbDevicesTrackingStopped,
)
from eventing import EventPublisher


_SourceFactory = Callable[[AdbServer], AdbTrackDevicesSource]
_ThreadFactory = Callable[..., Thread]


def _default_thread_factory(*args, **kwargs) -> Thread:
    thread = Thread(*args, **kwargs)
    thread.daemon = True
    return thread


@runtime_checkable
class AdbDevicesTracker(Protocol):
    """Single-use ADB devices tracker for one server lifetime."""

    @property
    def server(self) -> AdbServer: ...

    @property
    def active(self) -> bool: ...

    def start(self) -> None: ...

    def close(self) -> None: ...


class SmartSocketAdbDevicesTracker:
    """Track one smart-socket track-devices stream for one server lifetime.

    ``start`` establishes the stream and synchronously publishes its initial complete snapshot
    before returning. Subsequent snapshots are consumed by the worker. A tracker is single-use.
    Stop or failure is terminal; ``close`` closes the source and joins the worker before returning.
    """

    def __init__(
        self,
        server: AdbServer,
        publisher: EventPublisher,
        startup_timeout_seconds: float = 5.0,
        *,
        _source_factory: _SourceFactory | None = None,
        _thread_factory: _ThreadFactory = _default_thread_factory,
    ) -> None:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        if not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher")
        if _source_factory is not None and not callable(_source_factory):
            raise TypeError("_source_factory must be callable or None")
        if not callable(_thread_factory):
            raise TypeError("_thread_factory must be callable")
        self.server = server
        self.endpoint = server.endpoint
        self.startup_timeout_seconds = startup_timeout_seconds
        self._publisher = publisher
        self._source_factory = _source_factory
        self._thread_factory = _thread_factory
        self._lock = Lock()
        self._started = False
        self._active_source: AdbTrackDevicesSource | None = None
        self._active_thread: Thread | None = None
        self._closed = False

    @property
    def active(self) -> bool:
        with self._lock:
            return not self._closed and self._active_thread is not None

    def start(self) -> None:
        """Establish this tracker and return after its initial snapshot is published."""

        with self._lock:
            if self._closed:
                raise RuntimeError("ADB devices tracker is closed")
            if self._started:
                raise RuntimeError("ADB devices tracker is single-use and already started")
            source = self._create_source()
            self._started = True
            self._active_source = source

        try:
            session = source.open()
        except BaseException:
            self._abort_start(source)
            raise

        if session is None:
            self._abort_start(source)
            raise RuntimeError(
                "ADB devices tracker was closed before its initial snapshot was established"
            )

        startup_complete = Event()
        startup_errors: list[BaseException] = []
        try:
            thread = self._thread_factory(
                target=self._run,
                args=(source, session, startup_complete, startup_errors),
                name=(
                    "adb-track-devices-"
                    f"{self.endpoint.host}-{self.endpoint.port}-{self.server.epoch}"
                ),
            )
        except BaseException:
            session.close()
            self._abort_start(source)
            raise

        try:
            with self._lock:
                if self._closed or self._active_source is not source:
                    if self._active_source is source:
                        self._active_source = None
                    raise RuntimeError(
                        "ADB devices tracker was closed before its worker could start"
                    )
                self._active_thread = thread
                try:
                    thread.start()
                except BaseException:
                    self._active_thread = None
                    self._active_source = None
                    self._closed = True
                    raise
        except BaseException:
            session.close()
            source.close()
            raise

        startup_complete.wait()
        if startup_errors:
            if thread is not current_thread():
                thread.join()
            raise startup_errors[0]

    def close(self) -> None:
        """Destroy this tracker and wait for its worker to stop."""

        with self._lock:
            source = self._active_source
            thread = self._active_thread
            self._closed = True
        if source is not None:
            source.close()
        if thread is not None and thread is not current_thread():
            thread.join()

    def _create_source(self) -> AdbTrackDevicesSource:
        factory = self._source_factory
        source = (
            AdbTrackDevicesSource(
                self.server,
                startup_timeout_seconds=self.startup_timeout_seconds,
            )
            if factory is None
            else factory(self.server)
        )
        if not isinstance(source, AdbTrackDevicesSource):
            raise TypeError("source factory must return AdbTrackDevicesSource")
        return source

    def _abort_start(self, source: AdbTrackDevicesSource) -> None:
        with self._lock:
            if self._active_source is source:
                self._active_source = None
            self._active_thread = None
            self._closed = True
        source.close()

    def _run(
        self,
        source: AdbTrackDevicesSource,
        session: AdbTrackDevicesSession,
        startup_complete: Event,
        startup_errors: list[BaseException],
    ) -> None:
        server = self.server
        terminal: object | None = None
        startup_succeeded = False
        try:
            if not self._can_publish_from(source):
                raise RuntimeError(
                    "ADB devices tracker was closed before its initial snapshot was published"
                )

            self._publisher.publish(AdbDevicesTrackingStarted(server))
            self._publisher.publish(
                AdbDevicesSnapshotObserved(server, session.initial_snapshot)
            )
            startup_succeeded = True
            startup_complete.set()

            for snapshot in session.snapshots():
                if not self._can_publish_from(source):
                    break
                self._publisher.publish(
                    AdbDevicesSnapshotObserved(server, snapshot)
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
            session.close()
            publish_terminal = self._mark_terminal(source)

        if startup_succeeded and terminal is not None and publish_terminal:
            self._publisher.publish(terminal)

    def _can_publish_from(self, source: AdbTrackDevicesSource) -> bool:
        with self._lock:
            return not self._closed and self._active_source is source

    def _mark_terminal(self, source: AdbTrackDevicesSource) -> bool:
        with self._lock:
            publish_terminal = not self._closed and self._active_source is source
            if self._active_source is source:
                self._active_source = None
                self._active_thread = None
            self._closed = True
            return publish_terminal


__all__ = [
    "AdbDevicesTracker",
    "SmartSocketAdbDevicesTracker",
]
