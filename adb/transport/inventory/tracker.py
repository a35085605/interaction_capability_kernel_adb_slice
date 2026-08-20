from __future__ import annotations

from collections.abc import Callable
from threading import Lock, Thread, current_thread
from typing import Protocol, runtime_checkable

from adb.errors import (
    AdbProtocolError,
    AdbServerConnectionError,
    AdbServiceError,
)
from adb.server.endpoint import AdbServerEndpoint
from adb.transport.inventory.source import AdbTrackDevicesSession, AdbTrackDevicesSource
from adb.transport.signal import (
    AdbDevicesSnapshotObserved,
    AdbDevicesTrackingFailed,
    AdbDevicesTrackingFailure,
    AdbDevicesTrackingStarted,
    AdbDevicesTrackingStopped,
)
from eventing import EventPublisher


_SourceFactory = Callable[[AdbServerEndpoint], AdbTrackDevicesSource]
_ThreadFactory = Callable[..., Thread]


def _default_source_factory(endpoint: AdbServerEndpoint) -> AdbTrackDevicesSource:
    return AdbTrackDevicesSource(endpoint)


def _default_thread_factory(*args, **kwargs) -> Thread:
    thread = Thread(*args, **kwargs)
    thread.daemon = True
    return thread


@runtime_checkable
class AdbDevicesTrackingController(Protocol):
    """Single-use transport-inventory tracking scope."""

    @property
    def active(self) -> bool: ...

    def start(self) -> None: ...

    def close(self) -> None: ...


class AdbDevicesTracker:
    """Track one transport-inventory stream for one tracker lifetime.

    A tracker instance is single-use. Natural stop/failure is terminal, and explicit close is
    a teardown barrier: it closes the source and joins the worker before returning. Restarting
    tracking therefore requires constructing a fresh :class:`AdbDevicesTracker`.
    """

    def __init__(
        self,
        endpoint: AdbServerEndpoint,
        publisher: EventPublisher,
        *,
        _source_factory: _SourceFactory = _default_source_factory,
        _thread_factory: _ThreadFactory = _default_thread_factory,
    ) -> None:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        if not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher")
        self.endpoint = endpoint
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
        """Start this tracker exactly once."""

        with self._lock:
            if self._closed:
                raise RuntimeError("ADB devices tracker is closed")
            if self._started:
                raise RuntimeError("ADB devices tracker is single-use and already started")
            source = self._source_factory(self.endpoint)
            if not isinstance(source, AdbTrackDevicesSource):
                raise TypeError("source factory must return AdbTrackDevicesSource")
            thread = self._thread_factory(
                target=self._run,
                args=(source,),
                name=(
                    "adb-track-devices-"
                    f"{self.endpoint.host}-{self.endpoint.port}"
                ),
            )
            self._started = True
            self._active_source = source
            self._active_thread = thread
            try:
                thread.start()
            except BaseException:
                self._active_source = None
                self._active_thread = None
                self._closed = True
                source.close()
                raise

    def close(self) -> None:
        """Destroy this tracker scope and wait for its worker to stop."""

        with self._lock:
            source = self._active_source
            thread = self._active_thread
            self._closed = True
        if source is not None:
            source.close()
        if thread is not None and thread is not current_thread():
            thread.join()

    def _run(self, source: AdbTrackDevicesSource) -> None:
        endpoint = self.endpoint
        session: AdbTrackDevicesSession | None = None
        terminal: object | None = None
        try:
            session = source.open()
            if session is None:
                terminal = AdbDevicesTrackingStopped(endpoint)
            elif self._can_publish(source):
                self._publisher.publish(AdbDevicesTrackingStarted(endpoint))
                for snapshot in session.snapshots():
                    if not self._can_publish(source):
                        break
                    self._publisher.publish(
                        AdbDevicesSnapshotObserved(endpoint, snapshot)
                    )
                terminal = AdbDevicesTrackingStopped(endpoint)
        except AdbServerConnectionError as exc:
            terminal = AdbDevicesTrackingFailed(
                endpoint,
                AdbDevicesTrackingFailure.SERVER_CONNECTION,
                str(exc),
            )
        except AdbServiceError as exc:
            terminal = AdbDevicesTrackingFailed(
                endpoint,
                AdbDevicesTrackingFailure.SERVICE,
                str(exc),
            )
        except AdbProtocolError as exc:
            terminal = AdbDevicesTrackingFailed(
                endpoint,
                AdbDevicesTrackingFailure.PROTOCOL,
                str(exc),
            )
        finally:
            if session is not None:
                session.close()
            else:
                source.close()
            publish_terminal = self._mark_terminal(source)

        if terminal is not None and publish_terminal:
            self._publisher.publish(terminal)

    def _can_publish(self, source: AdbTrackDevicesSource) -> bool:
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
    "AdbDevicesTrackingController",
]
