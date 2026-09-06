from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from threading import Condition, Event, Lock, Thread, current_thread
from typing import Protocol, TypeAlias, runtime_checkable

from adb.errors import AdbProtocolError, AdbServerConnectionError, AdbServiceError
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
from adb.transport_list.watch.attachment import AdbTransportListWatchAttachment
from adb.transport_list.watch.session import (
    AdbTransportListWatchSession,
    bind_transport_list_watch_session,
)
from adb.transport_list.watch.stream import AdbTransportListWatchStream
from adb.transport_list.watch.error import (
    AdbTransportListWatchCancelledError,
    AdbTransportListWatchError,
)
from adb.transport_list.watch.failure import (
    AdbTransportListWatchFailure,
    AdbTransportListWatchProtocolFailure,
    AdbTransportListWatchServerConnectionFailure,
    AdbTransportListWatchServiceFailure,
)
from adb.transport_list.watch.signal import (
    AdbTransportListWatchFailed,
    AdbTransportListWatchStarted,
    AdbTransportListWatchStopped,
)
from eventing import EventPublisher


_TransportListWatchAttachmentFactory = Callable[
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


@dataclass(frozen=True, slots=True)
class _AdbTransportListWatchOpened:
    stream: AdbTransportListWatchStream
    initial: AdbTransportList


class _CloseOnceAdbTransportListWatchAttachment:
    """Make one adapter attachment safe to close from competing lifecycle paths."""

    __slots__ = ("_attachment", "_lock", "_closed")

    def __init__(self, attachment: AdbTransportListWatchAttachment) -> None:
        if not isinstance(attachment, AdbTransportListWatchAttachment):
            raise TypeError("attachment must satisfy AdbTransportListWatchAttachment")
        self._attachment = attachment
        self._lock = Lock()
        self._closed = False

    @property
    def address(self) -> TcpAddress:
        return self._attachment.address

    def open(self) -> AdbTransportListWatchStream | None:
        return self._attachment.open()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._attachment.close()


class _FailureNormalizingAdbTransportListWatchStream:
    """Translate ADB request errors from an established stream into typed watch errors."""

    def __init__(self, stream: AdbTransportListWatchStream) -> None:
        if not isinstance(stream, AdbTransportListWatchStream):
            raise TypeError("stream must satisfy AdbTransportListWatchStream")
        self._stream = stream

    @property
    def initial(self) -> AdbTransportList:
        try:
            return self._stream.initial
        except BaseException as exc:
            _raise_normalized_watch_error(exc)
            raise AssertionError("unreachable")

    def updates(self) -> Iterator[AdbTransportList]:
        try:
            yield from self._stream.updates()
        except BaseException as exc:
            _raise_normalized_watch_error(exc)
            raise AssertionError("unreachable")

    def close(self) -> None:
        self._stream.close()


def _open_transport_list_watch_attachment(
    attachment: AdbTransportListWatchAttachment,
) -> (
    _AdbTransportListWatchOpened
    | AdbTransportListWatchStartCancelled
    | AdbTransportListWatchStartFailed
):
    """Establish one raw stream from an already-authorized endpoint attachment."""

    if not isinstance(attachment, AdbTransportListWatchAttachment):
        raise TypeError("attachment must satisfy AdbTransportListWatchAttachment")

    try:
        stream = attachment.open()
    except AdbTransportListWatchCancelledError:
        attachment.close()
        return AdbTransportListWatchStartCancelled()
    except BaseException as exc:
        try:
            attachment.close()
        except BaseException:
            # Preserve the open failure; cleanup failure must not replace its diagnosis.
            pass
        failure = _watch_failure_from_exception(exc)
        if failure is None:
            raise
        return AdbTransportListWatchStartFailed(failure)

    if stream is None:
        attachment.close()
        return AdbTransportListWatchStartCancelled()
    if not isinstance(stream, AdbTransportListWatchStream):
        attachment.close()
        raise TypeError(
            "transport-list watch attachment must return AdbTransportListWatchStream or None"
        )

    normalized_stream = _FailureNormalizingAdbTransportListWatchStream(stream)
    try:
        initial = normalized_stream.initial
    except AdbTransportListWatchCancelledError:
        _close_stream_and_attachment(normalized_stream, attachment)
        return AdbTransportListWatchStartCancelled()
    except AdbTransportListWatchError as exc:
        _close_stream_and_attachment(normalized_stream, attachment)
        return AdbTransportListWatchStartFailed(exc.failure)
    except BaseException:
        _close_stream_and_attachment(normalized_stream, attachment)
        raise

    if not isinstance(initial, AdbTransportList):
        _close_stream_and_attachment(normalized_stream, attachment)
        raise TypeError("transport-list watch stream initial must be AdbTransportList")
    return _AdbTransportListWatchOpened(normalized_stream, initial)


def _close_stream_and_attachment(
    stream: AdbTransportListWatchStream,
    attachment: AdbTransportListWatchAttachment,
) -> None:
    first_error: BaseException | None = None
    try:
        stream.close()
    except BaseException as exc:
        first_error = exc
    try:
        attachment.close()
    except BaseException as exc:
        if first_error is None:
            first_error = exc
    if first_error is not None:
        raise first_error


def _watch_failure_from_exception(
    exc: BaseException,
) -> AdbTransportListWatchFailure | None:
    if isinstance(exc, AdbTransportListWatchError):
        return exc.failure
    if isinstance(exc, AdbServerConnectionError):
        return AdbTransportListWatchServerConnectionFailure(str(exc) or None)
    if isinstance(exc, AdbServiceError):
        return AdbTransportListWatchServiceFailure(str(exc) or None)
    if isinstance(exc, AdbProtocolError):
        return AdbTransportListWatchProtocolFailure(str(exc) or None)
    return None


def _raise_normalized_watch_error(exc: BaseException) -> None:
    if isinstance(exc, AdbTransportListWatchCancelledError):
        raise exc
    failure = _watch_failure_from_exception(exc)
    if failure is not None:
        if isinstance(exc, AdbTransportListWatchError):
            raise exc
        raise AdbTransportListWatchError(failure) from exc
    raise exc


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
        """Permanently close the controller and its current watch resources."""
        ...


class ThreadedAdbTransportListWatchController:
    """Reusable threaded controller for sequential transport-list watch sessions.

    The controller lives across ADB server lifetimes. Each ``start`` creates one endpoint
    attachment and one server-bound :class:`AdbTransportListWatchSession`; session identity is
    the authoritative stale-work fence for observations produced by its worker.
    """

    def __init__(
        self,
        server: AdbServerIdentity,
        endpoint: AdbServerEndpoint,
        publisher: EventPublisher,
        observation_coordinator: AdbTransportListCoordinator,
        startup_timeout_seconds: float = 5.0,
        *,
        _attachment_factory: _TransportListWatchAttachmentFactory,
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
        if not callable(_attachment_factory):
            raise TypeError("_attachment_factory must be callable")
        if not callable(_thread_factory):
            raise TypeError("_thread_factory must be callable")

        self.startup_timeout_seconds = startup_timeout_seconds
        self._publisher = publisher
        self._observation_coordinator = observation_coordinator
        self._attachment_factory = _attachment_factory
        self._thread_factory = _thread_factory
        self._condition = Condition(Lock())
        self._server = server
        self._endpoint = endpoint
        self._starting = False
        self._starting_thread: Thread | None = None
        self._start_token: object | None = None
        self._opening_attachment: AdbTransportListWatchAttachment | None = None
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
        """Start one short-lived server-bound session on this reusable controller."""

        if (server is None) != (endpoint is None):
            raise ValueError("server and endpoint must be provided together")

        with self._condition:
            if self._closed:
                raise RuntimeError("ADB transport-list watch controller is closed")
            if self._starting:
                raise RuntimeError("ADB transport-list watch controller startup is already active")
            if self._active_session is not None or self._active_thread is not None:
                raise RuntimeError(
                    "ADB transport-list watch controller already has an active session"
                )

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
            attachment = self._create_attachment(target_endpoint)
        except BaseException:
            self._finish_start(token)
            raise

        with self._condition:
            if self._closed or self._start_token is not token:
                cancelled_before_open = True
            else:
                cancelled_before_open = False
                self._opening_attachment = attachment

        if cancelled_before_open:
            attachment.close()
            self._finish_start(token)
            return AdbTransportListWatchStartCancelled()

        try:
            open_result = _open_transport_list_watch_attachment(attachment)
        except BaseException:
            self._clear_opening_attachment(attachment)
            self._finish_start(token)
            raise

        if not self._start_is_authorized(token):
            self._clear_opening_attachment(attachment)
            if isinstance(open_result, _AdbTransportListWatchOpened):
                _close_stream_and_attachment(open_result.stream, attachment)
            self._finish_start(token)
            return AdbTransportListWatchStartCancelled()

        if isinstance(open_result, AdbTransportListWatchStartCancelled):
            self._clear_opening_attachment(attachment)
            self._finish_start(token)
            return open_result
        if isinstance(open_result, AdbTransportListWatchStartFailed):
            self._clear_opening_attachment(attachment)
            self._finish_start(token)
            return open_result
        if not isinstance(open_result, _AdbTransportListWatchOpened):
            self._clear_opening_attachment(attachment)
            self._finish_start(token)
            raise TypeError("transport-list attachment open returned an unsupported result")

        session = bind_transport_list_watch_session(
            target_server,
            open_result.stream,
            open_result.initial,
            self._observation_coordinator.observation_identifier,
            attachment=attachment,
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
            self._clear_opening_attachment(attachment)
            session.close()
            self._finish_start(token)
            raise

        startup_error: BaseException | None = None
        cancelled_before_start = False
        with self._condition:
            if self._closed or self._start_token is not token:
                cancelled_before_start = True
                if self._opening_attachment is attachment:
                    self._opening_attachment = None
                self._finish_start_locked(token)
            else:
                if self._opening_attachment is not attachment:
                    raise RuntimeError("transport-list watch opening attachment disappeared")
                self._opening_attachment = None
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
            attachment = self._opening_attachment
            self._active_session = None
            self._opening_attachment = None
            self._start_token = None
            self._condition.notify_all()

        if session is not None:
            session.close()
        elif attachment is not None:
            attachment.close()

    def stop(self) -> None:
        """Synchronously stop current startup/session while preserving reusable ownership."""

        with self._condition:
            session = self._active_session
            attachment = self._opening_attachment
            worker = self._active_thread
            starting_thread = self._starting_thread
            self._active_session = None
            self._opening_attachment = None
            self._start_token = None
            self._condition.notify_all()

        first_error: BaseException | None = None
        try:
            if session is not None:
                session.close()
            elif attachment is not None:
                attachment.close()
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
        """Permanently close the controller and its current watch resources."""

        with self._condition:
            if self._closed:
                return
            self._closed = True
            session = self._active_session
            attachment = self._opening_attachment
            worker = self._active_thread
            starting_thread = self._starting_thread
            self._active_session = None
            self._opening_attachment = None
            self._start_token = None
            self._condition.notify_all()

        first_error: BaseException | None = None
        try:
            if session is not None:
                session.close()
            elif attachment is not None:
                attachment.close()
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

    def _create_attachment(
        self,
        endpoint: AdbServerEndpoint,
    ) -> AdbTransportListWatchAttachment:
        raw_attachment = self._attachment_factory(
            endpoint,
            self.startup_timeout_seconds,
        )
        if not isinstance(raw_attachment, AdbTransportListWatchAttachment):
            raise TypeError(
                "transport-list attachment factory must return "
                "AdbTransportListWatchAttachment"
            )
        if raw_attachment.address != endpoint:
            raw_attachment.close()
            raise ValueError(
                "transport-list attachment factory returned a mismatched server endpoint"
            )
        return _CloseOnceAdbTransportListWatchAttachment(raw_attachment)

    def _start_is_authorized(self, token: object) -> bool:
        with self._condition:
            return not self._closed and self._start_token is token

    def _clear_opening_attachment(
        self,
        attachment: AdbTransportListWatchAttachment,
    ) -> None:
        with self._condition:
            if self._opening_attachment is attachment:
                self._opening_attachment = None

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
