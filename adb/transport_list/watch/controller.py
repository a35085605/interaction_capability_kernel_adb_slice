from __future__ import annotations

from collections.abc import Callable
from threading import Event, Lock, Thread, current_thread
from typing import Protocol, runtime_checkable

from adb.errors import (
    AdbProtocolError,
    AdbServerConnectionError,
    AdbServiceError,
)
from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServerIdentity
from adb.transport.model import AdbTransport
from adb.transport_list.coordinator import (
    AdbTransportListObservationCoordinator,
    AdbTransportListObservationServerConflict,
)
from adb.transport_list.model import AdbTransportList
from adb.transport_list.state import AdbTransportListObserved
from adb.transport_list.watch.protocol import AdbTransportListWatch, AdbTransportListWatcher
from adb.adapters.aosp.track_devices import SmartSocketAdbTransportListWatcher
from adb.transport_list.watch.signal import (
    AdbTransportListWatchObservation,
    AdbTransportListWatchFailed,
    AdbTransportListWatchFailure,
    AdbTransportListWatchStarted,
    AdbTransportListWatchStopped,
)
from eventing import EventPublisher


_TransportListWatcherFactory = Callable[[TcpAddress], AdbTransportListWatcher]
_ThreadFactory = Callable[..., Thread]


def _default_thread_factory(*args, **kwargs) -> Thread:
    thread = Thread(*args, **kwargs)
    thread.daemon = True
    return thread


@runtime_checkable
class AdbTransportListWatchController(Protocol):
    """Control one transport-list watch lifetime for one ADB server lifetime."""

    @property
    def server(self) -> AdbServerIdentity:
        ...

    @property
    def endpoint(self) -> AdbServerEndpoint:
        ...

    @property
    def active(self) -> bool:
        ...

    def start(self) -> AdbTransportList:
        """Establish the watch and return its initial complete transport list."""
        ...

    def revoke(self) -> None:
        """Synchronously prevent further authoritative watch commits."""
        ...

    def stop(self) -> None:
        """Stop the watch and return after its worker has terminated."""
        ...


class ThreadedAdbTransportListWatchController:
    """Single-use threaded controller for one transport-list watch.

    Authoritative observations are committed through the shared observation coordinator before
    their watch signals are published. The default watcher uses AOSP ``track-devices`` over smart
    socket.
    """

    def __init__(
        self,
        server: AdbServerIdentity,
        endpoint: AdbServerEndpoint,
        publisher: EventPublisher,
        observation_coordinator: AdbTransportListObservationCoordinator,
        startup_timeout_seconds: float = 5.0,
        *,
        _watcher_factory: _TransportListWatcherFactory | None = None,
        _thread_factory: _ThreadFactory = _default_thread_factory,
    ) -> None:
        if not isinstance(server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher")
        if not isinstance(observation_coordinator, AdbTransportListObservationCoordinator):
            raise TypeError(
                "observation_coordinator must be AdbTransportListObservationCoordinator"
            )
        if _watcher_factory is not None and not callable(_watcher_factory):
            raise TypeError("_watcher_factory must be callable or None")
        if not callable(_thread_factory):
            raise TypeError("_thread_factory must be callable")
        self.server = server
        self.endpoint = endpoint
        self.startup_timeout_seconds = startup_timeout_seconds
        self._publisher = publisher
        self._observation_coordinator = observation_coordinator
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

    def start(self) -> AdbTransportList:
        """Establish the watch and return its initial complete transport list."""

        with self._lock:
            if self._closed:
                raise RuntimeError("ADB transport-list watch controller is stopped")
            if self._started:
                raise RuntimeError(
                    "ADB transport-list watch controller is single-use and already started"
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
                "ADB transport-list watch controller was stopped before its initial transport list "
                "was established"
            )

        startup_complete = Event()
        startup_transport_lists: list[AdbTransportList] = []
        startup_errors: list[BaseException] = []
        try:
            thread = self._thread_factory(
                target=self._run,
                args=(
                    watcher,
                    watch,
                    startup_complete,
                    startup_transport_lists,
                    startup_errors,
                ),
                name=(
                    "adb-transport-list-watch-"
                    f"{self.endpoint.host}-{self.endpoint.port}-{self.server}"
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
                        "ADB transport-list watch controller was stopped before its worker "
                        "could start"
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
        if len(startup_transport_lists) != 1:
            raise RuntimeError(
                "ADB transport-list watch controller did not produce exactly one initial "
                "transport list"
            )
        return startup_transport_lists[0]

    def revoke(self) -> None:
        """Synchronously prevent further authoritative commits from this controller."""

        with self._lock:
            self._closed = True

    def stop(self) -> None:
        """Stop the watch and return after its worker has terminated."""

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
                "transport-list watcher factory must return AdbTransportListWatcher"
            )
        if watcher.address != self.endpoint:
            raise ValueError(
                "transport-list watcher factory returned a mismatched server endpoint"
            )
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
        startup_transport_lists: list[AdbTransportList],
        startup_errors: list[BaseException],
    ) -> None:
        server = self.server
        terminal: object | None = None
        startup_succeeded = False
        try:
            initial_transport_list = self._normalize_transport_list(watch.initial)
            if not self._prepare_watch(watcher):
                raise RuntimeError(
                    "ADB transport-list watch lost authority before entering stream mode"
                )

            self._publisher.publish(AdbTransportListWatchStarted(server))
            if not self._commit_observation(watcher, initial_transport_list):
                raise RuntimeError(
                    "ADB transport-list watch lost authority before its initial transport list "
                    "could be committed"
                )
            self._publisher.publish(
                AdbTransportListWatchObservation(
                    server,
                    initial_transport_list,
                )
            )
            startup_transport_lists.append(initial_transport_list)
            startup_succeeded = True
            startup_complete.set()

            for transport_list in watch.updates():
                normalized = self._normalize_transport_list(transport_list)
                if not self._commit_observation(watcher, normalized):
                    break
                self._publisher.publish(
                    AdbTransportListWatchObservation(
                        server,
                        normalized,
                    )
                )
            terminal = AdbTransportListWatchStopped(server)
        except AdbServerConnectionError as exc:
            if startup_succeeded:
                terminal = AdbTransportListWatchFailed(
                    server,
                    AdbTransportListWatchFailure.SERVER_CONNECTION,
                    str(exc),
                )
            else:
                startup_errors.append(exc)
        except AdbServiceError as exc:
            if startup_succeeded:
                terminal = AdbTransportListWatchFailed(
                    server,
                    AdbTransportListWatchFailure.SERVICE,
                    str(exc),
                )
            else:
                startup_errors.append(exc)
        except AdbProtocolError as exc:
            if startup_succeeded:
                terminal = AdbTransportListWatchFailed(
                    server,
                    AdbTransportListWatchFailure.PROTOCOL,
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

    def _normalize_transport_list(
        self,
        transports: AdbTransportList | tuple[AdbTransport, ...],
    ) -> AdbTransportList:
        return AdbTransportList(transports)

    def _prepare_watch(self, watcher: AdbTransportListWatcher) -> bool:
        with self._lock:
            if self._closed or self._active_watcher is not watcher:
                return False
            return self._observation_coordinator.prepare_server(self.server)

    def _commit_observation(
        self,
        watcher: AdbTransportListWatcher,
        transport_list: AdbTransportList,
    ) -> bool:
        with self._lock:
            if self._closed or self._active_watcher is not watcher:
                return False
            return self._commit_observation_locked(transport_list)

    def _commit_observation_locked(self, transport_list: AdbTransportList) -> bool:
        result = self._observation_coordinator.observe(self.server, transport_list)
        if isinstance(result, AdbTransportListObserved):
            return True
        if isinstance(result, AdbTransportListObservationServerConflict):
            return False
        raise RuntimeError(
            "ADB transport-list watch observation lost the authoritative state fence"
        )

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
    "AdbTransportListWatchController",
    "ThreadedAdbTransportListWatchController",
]
