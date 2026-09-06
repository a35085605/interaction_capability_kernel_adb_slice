from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Condition, Event, Lock, Thread, current_thread
from typing import Protocol, TypeAlias, runtime_checkable

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServerIdentity
from adb.transport_list.coordinator import (
    AdbTransportListCoordinator,
    AdbTransportListObservationServerConflict,
)
from adb.transport_list.model import AdbTransportList
from adb.transport_list.observation import AdbTransportListObservation
from adb.transport_list.state import (
    AdbTransportListObservationStateConflict,
    AdbTransportListObserved,
)
from adb.transport_list.watch.session import (
    AdbTransportListWatchSession,
    bind_transport_list_watch_session,
)
from adb.transport_list.watch.error import AdbTransportListWatchError
from adb.transport_list.watch.failure import AdbTransportListWatchFailure
from adb.transport_list.watch.watcher import (
    AdbTransportListWatchAttachment,
    AdbTransportListWatchOpenCancelled,
    AdbTransportListWatchOpenFailed,
    AdbTransportListWatchOpened,
    ReusableAdbTransportListWatcher,
    open_transport_list_watch,
)
from adb.transport_list.watch.signal import (
    AdbTransportListWatchFailed,
    AdbTransportListWatchStarted,
    AdbTransportListWatchStopped,
)
from eventing import EventPublisher


_TransportListWatcherFactory = Callable[
    [TcpAddress, float], AdbTransportListWatchAttachment
]
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
    """Startup completed with a known transport-list watch failure."""

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
    """Long-lived controller that runs one short-lived watch session at a time."""

    @property
    def server(self) -> AdbServerIdentity:
        """Server binding requested for the current or most recent session."""
        ...

    @property
    def endpoint(self) -> AdbServerEndpoint:
        """Endpoint binding requested for the current or most recent session."""
        ...

    @property
    def active(self) -> bool:
        ...

    def start(
        self,
        server: AdbServerIdentity | None = None,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbTransportListWatchStartResult:
        """Start one session, optionally replacing the retained server binding."""
        ...

    def revoke(self) -> None:
        """Synchronously fence and cancel the current session without closing the controller."""
        ...

    def stop(self) -> None:
        """Stop the current session and join its worker while keeping the controller reusable."""
        ...

    def close(self) -> None:
        """Permanently close the controller and its long-lived watcher."""
        ...


class ThreadedAdbTransportListWatchController:
    """Reusable threaded controller for sequential transport-list watch sessions.

    The controller and watcher live across ADB server lifetimes. Each ``start`` creates one
    server-bound :class:`AdbTransportListWatchSession`; session identity is the authoritative
    stale-work fence for observations produced by its worker.
    """

    def __init__(
        self,
        server: AdbServerIdentity,
        endpoint: AdbServerEndpoint,
        publisher: EventPublisher,
        observation_coordinator: AdbTransportListCoordinator,
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
        if not isinstance(observation_coordinator, AdbTransportListCoordinator):
            raise TypeError(
                "observation_coordinator must be AdbTransportListCoordinator"
            )
        if not callable(_watcher_factory):
            raise TypeError("_watcher_factory must be callable")
        if not callable(_thread_factory):
            raise TypeError("_thread_factory must be callable")

        self.startup_timeout_seconds = startup_timeout_seconds
        self._publisher = publisher
        self._observation_coordinator = observation_coordinator
        self._watcher = ReusableAdbTransportListWatcher(
            _watcher_factory,
            startup_timeout_seconds=startup_timeout_seconds,
        )
        self._thread_factory = _thread_factory
        self._condition = Condition(Lock())
        self._server = server
        self._endpoint = endpoint
        self._starting = False
        self._starting_thread: Thread | None = None
        self._start_token: object | None = None
        self._active_session: AdbTransportListWatchSession | None = None
        self._active_thread: Thread | None = None
        self._closed = False

    @property
    def server(self) -> AdbServerIdentity:
        with self._condition:
            return self._server

    @property
    def endpoint(self) -> AdbServerEndpoint:
        with self._condition:
            return self._endpoint

    @property
    def active(self) -> bool:
        with self._condition:
            return (
                not self._closed
                and self._active_session is not None
                and self._active_thread is not None
            )

    def start(
        self,
        server: AdbServerIdentity | None = None,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbTransportListWatchStartResult:
        """Start one short-lived session on this reusable controller and watcher."""

        if (server is None) != (endpoint is None):
            raise ValueError("server and endpoint must be provided together")

        with self._condition:
            if self._closed:
                raise RuntimeError("ADB transport-list watch controller is closed")
            if self._starting:
                raise RuntimeError("ADB transport-list watch controller startup is already active")
            if self._active_session is not None or self._active_thread is not None:
                raise RuntimeError("ADB transport-list watch controller already has an active session")

            target_server = self._server if server is None else server
            target_endpoint = self._endpoint if endpoint is None else endpoint
            if not isinstance(target_server, AdbServerIdentity):
                raise TypeError("server must be AdbServerIdentity")
            if not isinstance(target_endpoint, TcpAddress):
                raise TypeError("endpoint must be TcpAddress")

            self._server = target_server
            self._endpoint = target_endpoint
            token = object()
            self._starting = True
            self._starting_thread = current_thread()
            self._start_token = token

        try:
            open_result = open_transport_list_watch(self._watcher, target_endpoint)
        except BaseException:
            self._finish_start(token)
            raise

        if not self._start_is_authorized(token):
            if isinstance(open_result, AdbTransportListWatchOpened):
                open_result.stream.close()
            self._finish_start(token)
            return AdbTransportListWatchStartCancelled()

        if isinstance(open_result, AdbTransportListWatchOpenCancelled):
            self._finish_start(token)
            return AdbTransportListWatchStartCancelled()
        if isinstance(open_result, AdbTransportListWatchOpenFailed):
            self._finish_start(token)
            return AdbTransportListWatchStartFailed(open_result.failure)
        if not isinstance(open_result, AdbTransportListWatchOpened):
            self._finish_start(token)
            raise TypeError("open_transport_list_watch() returned an unsupported result")

        session = bind_transport_list_watch_session(
            target_server,
            open_result.stream,
            open_result.initial,
            self._observation_coordinator.observation_identifier,
        )
        startup_complete = Event()
        startup_results: list[AdbTransportListWatchStartResult] = []
        startup_errors: list[BaseException] = []
        try:
            thread = self._thread_factory(
                target=self._run,
                args=(
                    session,
                    startup_complete,
                    startup_results,
                    startup_errors,
                ),
                name=(
                    "adb-transport-list-watch-"
                    f"{target_endpoint.host}-{target_endpoint.port}-{target_server}"
                ),
            )
        except BaseException:
            session.close()
            self._finish_start(token)
            raise

        startup_error: BaseException | None = None
        cancelled_before_start = False
        with self._condition:
            if self._closed or self._start_token is not token:
                cancelled_before_start = True
                self._finish_start_locked(token)
            else:
                self._active_session = session
                self._active_thread = thread
                self._finish_start_locked(token)
                try:
                    thread.start()
                except BaseException as exc:
                    self._active_session = None
                    self._active_thread = None
                    self._condition.notify_all()
                    startup_error = exc

        if cancelled_before_start or startup_error is not None:
            session.close()
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
        """Fence current session authority immediately without retiring the controller."""

        with self._condition:
            session = self._active_session
            starting = self._starting
            self._active_session = None
            self._start_token = None
            self._condition.notify_all()

        if session is not None:
            session.close()
        elif starting:
            self._watcher.cancel()

    def stop(self) -> None:
        """Synchronously stop current startup/session while preserving reusable ownership."""

        with self._condition:
            session = self._active_session
            worker = self._active_thread
            starting = self._starting
            starting_thread = self._starting_thread
            self._active_session = None
            self._start_token = None
            self._condition.notify_all()

        first_error: BaseException | None = None
        try:
            if session is not None:
                session.close()
            elif starting:
                self._watcher.cancel()
        except BaseException as exc:
            first_error = exc

        with self._condition:
            while self._starting and starting_thread is not current_thread():
                self._condition.wait()

        if worker is not None and worker is not current_thread():
            try:
                worker.join()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc

        if first_error is not None:
            raise first_error

    def close(self) -> None:
        """Permanently close controller/watcher ownership after stopping the active session."""

        with self._condition:
            if self._closed:
                return
            self._closed = True
            session = self._active_session
            worker = self._active_thread
            starting_thread = self._starting_thread
            self._active_session = None
            self._start_token = None
            self._condition.notify_all()

        first_error: BaseException | None = None
        if session is not None:
            try:
                session.close()
            except BaseException as exc:
                first_error = exc
        try:
            self._watcher.close()
        except BaseException as exc:
            if first_error is None:
                first_error = exc

        with self._condition:
            while self._starting and starting_thread is not current_thread():
                self._condition.wait()

        if worker is not None and worker is not current_thread():
            try:
                worker.join()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc

        if first_error is not None:
            raise first_error

    def _start_is_authorized(self, token: object) -> bool:
        with self._condition:
            return not self._closed and self._start_token is token

    def _finish_start(self, token: object) -> None:
        with self._condition:
            self._finish_start_locked(token)

    def _finish_start_locked(self, token: object) -> None:
        if not self._starting:
            return
        if self._start_token is token:
            self._start_token = None
        self._starting = False
        self._starting_thread = None
        self._condition.notify_all()

    def _run(
        self,
        session: AdbTransportListWatchSession,
        startup_complete: Event,
        startup_results: list[AdbTransportListWatchStartResult],
        startup_errors: list[BaseException],
    ) -> None:
        server = session.server
        terminal: object | None = None
        startup_succeeded = False
        try:
            initial_observation = session.initial
            if initial_observation.server != server:
                raise ValueError(
                    "transport-list watch session initial observation has mismatched server provenance"
                )
            if not self._commit_observation(session, initial_observation):
                startup_results.append(AdbTransportListWatchStartSuperseded())
                return

            self._publisher.publish(AdbTransportListWatchStarted(server))
            startup_results.append(
                AdbTransportListWatchStartSucceeded(initial_observation.transport_list)
            )
            startup_succeeded = True
            startup_complete.set()

            for observation in session.updates():
                if not self._commit_observation(session, observation):
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
            close_error: BaseException | None = None
            try:
                session.close()
            except BaseException as exc:
                close_error = exc
            finally:
                publish_terminal = self._mark_terminal(session)

            if close_error is not None:
                if startup_succeeded:
                    raise close_error
                startup_errors.append(close_error)

        if startup_succeeded and terminal is not None and publish_terminal:
            self._publisher.publish(terminal)

    def _commit_observation(
        self,
        session: AdbTransportListWatchSession,
        observation: AdbTransportListObservation,
    ) -> bool:
        if not isinstance(observation, AdbTransportListObservation):
            raise TypeError("observation must be AdbTransportListObservation")
        with self._condition:
            if self._closed or self._active_session is not session:
                return False
            result = self._observation_coordinator.observe(observation)
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

    def _mark_terminal(self, session: AdbTransportListWatchSession) -> bool:
        active_thread = current_thread()
        with self._condition:
            publish_terminal = not self._closed and self._active_session is session
            if self._active_session is session:
                self._active_session = None
            if self._active_thread is active_thread:
                self._active_thread = None
            self._condition.notify_all()
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
