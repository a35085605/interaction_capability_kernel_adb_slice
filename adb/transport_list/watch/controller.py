from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event, Lock, Thread, current_thread
from typing import Protocol, TypeAlias, runtime_checkable

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServerIdentity
from adb.transport.model import AdbTransport
from adb.transport_list.coordinator import (
    AdbTransportListObservationCoordinator,
    AdbTransportListObservationServerConflict,
)
from adb.transport_list.model import AdbTransportList
from adb.transport_list.state import (
    AdbTransportListObservationStateConflict,
    AdbTransportListObserved,
)
from adb.transport_list.watch.session import AdbTransportListWatchSession
from adb.transport_list.watch.error import AdbTransportListWatchError
from adb.transport_list.watch.failure import AdbTransportListWatchFailure
from adb.transport_list.watch.watcher import (
    AdbTransportListWatchOpenCancelled,
    AdbTransportListWatchOpenFailed,
    AdbTransportListWatchOpened,
    AdbTransportListWatcher,
    open_transport_list_watch,
)
from adb.transport_list.watch.signal import (
    AdbTransportListWatchFailed,
    AdbTransportListWatchStarted,
    AdbTransportListWatchStopped,
)
from eventing import EventPublisher


_TransportListWatcherFactory = Callable[[TcpAddress, float], AdbTransportListWatcher]
_ThreadFactory = Callable[..., Thread]


def _default_thread_factory(*args, **kwargs) -> Thread:
    thread = Thread(*args, **kwargs)
    thread.daemon = True
    return thread


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchStartSucceeded:
    """The watch entered stream mode and committed its initial complete transport list."""

    initial: AdbTransportList

    def __post_init__(self) -> None:
        if not isinstance(self.initial, AdbTransportList):
            raise TypeError("initial must be AdbTransportList")


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchStartCancelled:
    """Startup was cancelled by lifecycle closure before the watch became active."""


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchStartSuperseded:
    """Startup lost its authoritative server/watch fence before becoming active."""


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchStartFailed:
    """Startup completed with a known transport-list watch domain failure."""

    failure: AdbTransportListWatchFailure

    def __post_init__(self) -> None:
        if not isinstance(self.failure, AdbTransportListWatchFailure):
            raise TypeError("failure must be AdbTransportListWatchFailure")


AdbTransportListWatchStartResult: TypeAlias = (
    AdbTransportListWatchStartSucceeded
    | AdbTransportListWatchStartCancelled
    | AdbTransportListWatchStartSuperseded
    | AdbTransportListWatchStartFailed
)


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

    def start(self) -> AdbTransportListWatchStartResult:
        """Attempt startup and return typed evidence of the startup outcome."""
        ...

    def revoke(self) -> None:
        """Synchronously prevent further authoritative watch commits."""
        ...

    def stop(self) -> None:
        """Stop the watch and return after its worker has terminated."""
        ...


class ThreadedAdbTransportListWatchController:
    """Single-use threaded controller for one transport-list watch.

    Authoritative observations are committed and published through the shared observation
    coordinator. This controller publishes only watch-lifecycle signals. The low-level watcher
    implementation is supplied by the composition root.
    """

    def __init__(
        self,
        server: AdbServerIdentity,
        endpoint: AdbServerEndpoint,
        publisher: EventPublisher,
        observation_coordinator: AdbTransportListObservationCoordinator,
        startup_timeout_seconds: float = 5.0,
        *,
        _watcher_factory: _TransportListWatcherFactory,
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
        if not callable(_watcher_factory):
            raise TypeError("_watcher_factory must be callable")
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

    def start(self) -> AdbTransportListWatchStartResult:
        """Attempt startup and return typed evidence of the startup outcome."""

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
            open_result = open_transport_list_watch(watcher)
        except BaseException:
            self._abort_start(watcher)
            raise

        if isinstance(open_result, AdbTransportListWatchOpenCancelled):
            self._abort_start(watcher)
            return AdbTransportListWatchStartCancelled()
        if isinstance(open_result, AdbTransportListWatchOpenFailed):
            self._abort_start(watcher)
            return AdbTransportListWatchStartFailed(open_result.failure)
        if not isinstance(open_result, AdbTransportListWatchOpened):
            self._abort_start(watcher)
            raise TypeError("open_transport_list_watch() returned an unsupported result")

        session = open_result.session
        initial_transport_list = open_result.initial
        startup_complete = Event()
        startup_results: list[AdbTransportListWatchStartResult] = []
        startup_errors: list[BaseException] = []
        try:
            thread = self._thread_factory(
                target=self._run,
                args=(
                    watcher,
                    session,
                    initial_transport_list,
                    startup_complete,
                    startup_results,
                    startup_errors,
                ),
                name=(
                    "adb-transport-list-watch-"
                    f"{self.endpoint.host}-{self.endpoint.port}-{self.server}"
                ),
            )
        except BaseException:
            session.close()
            self._abort_start(watcher)
            raise

        startup_error: BaseException | None = None
        with self._lock:
            if self._closed:
                if self._active_watcher is watcher:
                    self._active_watcher = None
                cancelled_before_start = True
            elif self._active_watcher is not watcher:
                cancelled_before_start = False
                startup_error = RuntimeError(
                    "ADB transport-list watch controller active watcher changed during startup"
                )
            else:
                cancelled_before_start = False
                self._active_thread = thread
                try:
                    thread.start()
                except BaseException as exc:
                    self._active_thread = None
                    self._active_watcher = None
                    self._closed = True
                    startup_error = exc

        if cancelled_before_start or startup_error is not None:
            session.close()
            watcher.close()
        if startup_error is not None:
            raise startup_error
        if cancelled_before_start:
            return AdbTransportListWatchStartCancelled()

        startup_complete.wait()
        if startup_errors:
            if thread is not current_thread():
                thread.join()
            raise startup_errors[0]
        if len(startup_results) != 1:
            raise RuntimeError(
                "ADB transport-list watch controller did not produce exactly one startup result"
            )
        return startup_results[0]

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
        watcher = self._watcher_factory(
            self.endpoint,
            self.startup_timeout_seconds,
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
        session: AdbTransportListWatchSession,
        initial_transport_list: AdbTransportList,
        startup_complete: Event,
        startup_results: list[AdbTransportListWatchStartResult],
        startup_errors: list[BaseException],
    ) -> None:
        server = self.server
        terminal: object | None = None
        startup_succeeded = False
        try:
            initial_transport_list = self._normalize_transport_list(initial_transport_list)
            if not self._prepare_watch(watcher):
                startup_results.append(AdbTransportListWatchStartSuperseded())
                return

            if not self._commit_observation(watcher, initial_transport_list):
                startup_results.append(AdbTransportListWatchStartSuperseded())
                return

            self._publisher.publish(AdbTransportListWatchStarted(server))
            startup_results.append(
                AdbTransportListWatchStartSucceeded(initial_transport_list)
            )
            startup_succeeded = True
            startup_complete.set()

            for transport_list in session.updates():
                normalized = self._normalize_transport_list(transport_list)
                if not self._commit_observation(watcher, normalized):
                    break
            terminal = AdbTransportListWatchStopped(server)
        except AdbTransportListWatchError as exc:
            if startup_succeeded:
                terminal = AdbTransportListWatchFailed(server, exc.failure)
            else:
                startup_results.append(AdbTransportListWatchStartFailed(exc.failure))
        except BaseException as exc:
            if startup_succeeded:
                raise
            startup_errors.append(exc)
        finally:
            startup_complete.set()
            session.close()
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
        if isinstance(
            result,
            (
                AdbTransportListObservationServerConflict,
                AdbTransportListObservationStateConflict,
            ),
        ):
            return False
        raise TypeError("transport-list observation coordinator returned an unsupported result")

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
    "AdbTransportListWatchStartCancelled",
    "AdbTransportListWatchStartFailed",
    "AdbTransportListWatchStartResult",
    "AdbTransportListWatchStartSucceeded",
    "AdbTransportListWatchStartSuperseded",
    "ThreadedAdbTransportListWatchController",
]
