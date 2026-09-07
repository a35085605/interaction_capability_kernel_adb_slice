from __future__ import annotations

from collections.abc import Callable
from threading import Lock, Thread, current_thread

from networking import TcpAddress
from adb.transport_list.coordinator import AdbTransportListCoordinator
from adb.transport_list.session_identity import AdbTransportListSessionIdentity
from adb.transport_list.watch.supervision.policy import AdbTransportListWatchSupervisionPolicy
from adb.transport_list.watch.controller import (
    AdbTransportListWatchController,
    AdbTransportListWatchStartCancelled,
    AdbTransportListWatchStartFailed,
    AdbTransportListWatchStartSucceeded,
    AdbTransportListWatchStartSuperseded,
    ThreadedAdbTransportListWatchController,
)
from adb.transport_list.watch.attachment import AdbTransportListWatchAttachment
from adb.transport_list.watch.failure import (
    AdbTransportListWatchFailure,
    AdbTransportListWatchServerConnectionFailure,
)
from adb.transport_list.watch.signal import (
    AdbTransportListWatchFailed,
    AdbTransportListWatchStarted,
    AdbTransportListWatchStopped,
)
from eventing import EventBus, EventPublisher, EventSubscriptionToken


_ThreadFactory = Callable[..., Thread]
_TransportListWatchAttachmentFactory = Callable[
    [TcpAddress, float], AdbTransportListWatchAttachment
]
_ConnectionFailureHandler = Callable[[AdbTransportListWatchServerConnectionFailure], None]
_ControllerFactory = Callable[
    [
        TcpAddress,
        EventPublisher,
        AdbTransportListCoordinator,
    ],
    AdbTransportListWatchController,
]


def _default_thread_factory(*args, **kwargs) -> Thread:
    thread = Thread(*args, **kwargs)
    thread.daemon = True
    return thread


class AdbTransportListWatchSupervisor:
    """Maintain endpoint-bound watch controllers and correlate their WatchSession signals."""

    def __init__(
        self,
        endpoint: TcpAddress,
        event_bus: EventBus,
        policy: AdbTransportListWatchSupervisionPolicy,
        *,
        transport_list_observation_coordinator: AdbTransportListCoordinator,
        connection_failure_handler: _ConnectionFailureHandler,
        _attachment_factory: _TransportListWatchAttachmentFactory | None = None,
        _controller_factory: _ControllerFactory | None = None,
        _thread_factory: _ThreadFactory = _default_thread_factory,
    ) -> None:
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if not callable(getattr(event_bus, "publish", None)) or not callable(
            getattr(event_bus, "subscribe", None)
        ) or not callable(getattr(event_bus, "unsubscribe", None)):
            raise TypeError("event_bus must satisfy EventBus")
        if not isinstance(policy, AdbTransportListWatchSupervisionPolicy):
            raise TypeError("policy must be AdbTransportListWatchSupervisionPolicy")
        if not isinstance(
            transport_list_observation_coordinator,
            AdbTransportListCoordinator,
        ):
            raise TypeError(
                "transport_list_observation_coordinator must be "
                "AdbTransportListCoordinator"
            )
        if not callable(connection_failure_handler):
            raise TypeError("connection_failure_handler must be callable")
        if _attachment_factory is not None and not callable(_attachment_factory):
            raise TypeError("_attachment_factory must be callable or None")
        if _controller_factory is not None and not callable(_controller_factory):
            raise TypeError("_controller_factory must be callable or None")
        if _attachment_factory is None and _controller_factory is None:
            raise ValueError(
                "_attachment_factory is required when no controller factory is provided"
            )
        if not callable(_thread_factory):
            raise TypeError("_thread_factory must be callable")

        self._endpoint = endpoint
        self._bus = event_bus
        self._transport_list_observation_coordinator = (
            transport_list_observation_coordinator
        )
        self._connection_failure_handler = connection_failure_handler
        self._policy = policy
        self._attachment_factory = _attachment_factory
        self._controller_factory = _controller_factory
        self._thread_factory = _thread_factory
        self._lock = Lock()
        self._subscriptions: tuple[EventSubscriptionToken, ...] = ()
        self._watch_requested = False
        self._controller: AdbTransportListWatchController | None = None
        self._current_session_identity: AdbTransportListSessionIdentity | None = None
        self._last_session_epoch_value = 0
        self._watch_active = False
        self._start_in_progress = False
        self._start_token: object | None = None
        self._attempt_threads: set[Thread] = set()
        self._closed = False

    @property
    def endpoint(self) -> TcpAddress:
        with self._lock:
            return self._endpoint

    @property
    def transport_list_observation_coordinator(
        self,
    ) -> AdbTransportListCoordinator:
        """Shared transport-list authority coordinator used by all watch sessions."""

        return self._transport_list_observation_coordinator

    @property
    def watch_requested(self) -> bool:
        with self._lock:
            return self._watch_requested

    @property
    def watch_active(self) -> bool:
        with self._lock:
            return self._watch_active

    def start(self) -> bool:
        """Request transport-list watching and start the first short-lived session."""

        with self._lock:
            self._require_open()
            if self._watch_requested:
                raise RuntimeError("transport-list watch supervisor is already started")
            self._ensure_subscriptions_locked()
            self._watch_requested = True
            controller = self._ensure_controller_locked(self._endpoint)
            token = self._begin_start_locked()

        return self._attempt_start(controller, token)

    def reconcile(
        self,
        endpoint: TcpAddress | None = None,
        *,
        replace_controller: bool = False,
        connection_failure_handler: _ConnectionFailureHandler | None = None,
    ) -> None:
        """Restart an inactive session or replace the endpoint controller after activation.

        ``replace_controller`` is the explicit server-lifetime boundary supplied by runtime
        activation wiring. Session authority is fenced by StateStore admission and WatchSession
        identity rather than by controller-scoped issuer replacement.
        """

        if endpoint is not None and not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress or None")
        if not isinstance(replace_controller, bool):
            raise TypeError("replace_controller must be bool")
        if connection_failure_handler is not None and not callable(connection_failure_handler):
            raise TypeError("connection_failure_handler must be callable or None")
        if replace_controller and connection_failure_handler is None:
            raise ValueError(
                "controller replacement requires a fresh failure handler"
            )

        launch: tuple[Thread, AdbTransportListWatchController, object] | None = None

        while True:
            controller_to_close: AdbTransportListWatchController | None = None

            with self._lock:
                self._require_open()
                if endpoint is not None:
                    self._endpoint = endpoint
                if connection_failure_handler is not None:
                    self._connection_failure_handler = connection_failure_handler
                if not self._watch_requested:
                    return

                target_endpoint = self._endpoint
                controller = self._controller
                if controller is not None and (
                    replace_controller or controller.endpoint != target_endpoint
                ):
                    self._cancel_start_locked()
                    self._watch_active = False
                    self._current_session_identity = None
                    self._controller = None
                    controller_to_close = controller
                    replace_controller = False
                else:
                    if controller is None:
                        controller = self._create_controller_locked(target_endpoint)

                    if not self._start_in_progress and not controller.active:
                        token = self._begin_start_locked()
                        thread = self._thread_factory(
                            target=self._run_start_attempt,
                            args=(controller, token),
                            name=(
                                "adb-transport-list-watch-reconciliation-"
                                f"{target_endpoint.host}-{target_endpoint.port}"
                            ),
                        )
                        self._attempt_threads.add(thread)
                        launch = (thread, controller, token)
                    break

            assert controller_to_close is not None
            controller_to_close.close()

        if launch is not None:
            thread, controller, token = launch
            try:
                thread.start()
            except BaseException:
                with self._lock:
                    self._attempt_threads.discard(thread)
                    if self._start_token is token:
                        self._cancel_start_locked()
                controller.stop()
                raise

    def close(self) -> None:
        """Close long-lived controller ownership and join startup workers."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._watch_requested = False
            self._watch_active = False
            self._current_session_identity = None
            self._cancel_start_locked()
            subscriptions = self._subscriptions
            self._subscriptions = ()
            controller = self._controller
            self._controller = None
            attempt_threads = tuple(self._attempt_threads)

        for token in subscriptions:
            self._bus.unsubscribe(token)
        if controller is not None:
            controller.close()
        for thread in attempt_threads:
            if thread is not current_thread():
                thread.join()

    def _on_watch_started(self, event: AdbTransportListWatchStarted) -> None:
        with self._lock:
            controller = self._controller
            if self._closed or not self._watch_requested or controller is None:
                return

            current_session = self._current_session_identity
            if current_session is None:
                if (
                    not self._start_in_progress
                    or event.session_identity.epoch.value <= self._last_session_epoch_value
                ):
                    return
                self._current_session_identity = event.session_identity
                self._last_session_epoch_value = event.session_identity.epoch.value
            elif event.session_identity is not current_session:
                return
            self._watch_active = controller.active

    def _on_watch_failed(self, event: AdbTransportListWatchFailed) -> None:
        connection_failure: AdbTransportListWatchServerConnectionFailure | None = None
        handler: _ConnectionFailureHandler | None = None
        with self._lock:
            if (
                self._closed
                or self._controller is None
                or event.session_identity is not self._current_session_identity
            ):
                return
            self._current_session_identity = None
            self._watch_active = False
            if self._watch_requested and isinstance(
                event.failure,
                AdbTransportListWatchServerConnectionFailure,
            ):
                connection_failure = event.failure
                handler = self._connection_failure_handler

        if connection_failure is not None and handler is not None:
            handler(connection_failure)

    def _on_watch_stopped(self, event: AdbTransportListWatchStopped) -> None:
        with self._lock:
            if (
                self._closed
                or self._controller is None
                or event.session_identity is not self._current_session_identity
            ):
                return
            self._current_session_identity = None
            self._watch_active = False

    def _run_start_attempt(
        self,
        controller: AdbTransportListWatchController,
        token: object,
    ) -> None:
        active_thread = current_thread()
        try:
            with self._lock:
                if (
                    self._closed
                    or not self._watch_requested
                    or self._controller is not controller
                    or self._start_token is not token
                ):
                    return
            self._attempt_start(controller, token)
        finally:
            with self._lock:
                self._attempt_threads.discard(active_thread)

    def _attempt_start(
        self,
        controller: AdbTransportListWatchController,
        token: object,
    ) -> bool:
        endpoint = controller.endpoint
        try:
            result = controller.start()
        except BaseException:
            with self._lock:
                if self._start_token is token:
                    self._cancel_start_locked()
            raise

        if isinstance(result, AdbTransportListWatchStartSucceeded):
            return self._complete_start_attempt(
                controller,
                result.session_identity,
                endpoint,
                token,
                started=True,
            )
        if isinstance(result, AdbTransportListWatchStartFailed):
            return self._complete_start_attempt(
                controller,
                result.session_identity,
                endpoint,
                token,
                started=False,
                failure=result.failure,
            )
        if isinstance(
            result,
            (
                AdbTransportListWatchStartCancelled,
                AdbTransportListWatchStartSuperseded,
            ),
        ):
            return self._complete_start_attempt(
                controller,
                result.session_identity,
                endpoint,
                token,
                started=False,
            )

        with self._lock:
            if self._start_token is token:
                self._cancel_start_locked()
        controller.stop()
        raise TypeError("transport-list watch controller start() returned an unsupported result")

    def _complete_start_attempt(
        self,
        controller: AdbTransportListWatchController,
        session_identity: AdbTransportListSessionIdentity | None,
        endpoint: TcpAddress,
        token: object,
        *,
        started: bool,
        failure: AdbTransportListWatchFailure | None = None,
    ) -> bool:
        stop_superseded_session = False
        publish_failure = False
        connection_failure: AdbTransportListWatchServerConnectionFailure | None = None
        handler: _ConnectionFailureHandler | None = None

        with self._lock:
            if self._controller is not controller or self._start_token is not token:
                return False

            self._cancel_start_locked()
            target_is_current = (
                not self._closed
                and self._watch_requested
                and self._endpoint == endpoint
                and controller.endpoint == endpoint
            )
            if (
                session_identity is not None
                and session_identity.epoch.value > self._last_session_epoch_value
            ):
                self._last_session_epoch_value = session_identity.epoch.value
            watch_active = started and target_is_current and controller.active
            self._watch_active = watch_active
            self._current_session_identity = session_identity if watch_active else None
            stop_superseded_session = started and not watch_active
            publish_failure = (
                failure is not None
                and session_identity is not None
                and target_is_current
            )
            if (
                failure is not None
                and target_is_current
                and self._watch_requested
                and isinstance(failure, AdbTransportListWatchServerConnectionFailure)
            ):
                connection_failure = failure
                handler = self._connection_failure_handler

        if stop_superseded_session:
            controller.stop()
        if publish_failure:
            assert failure is not None
            assert session_identity is not None
            self._bus.publish(AdbTransportListWatchFailed(session_identity, failure))
        if connection_failure is not None and handler is not None:
            handler(connection_failure)
        return watch_active

    def _ensure_controller_locked(
        self,
        endpoint: TcpAddress,
    ) -> AdbTransportListWatchController:
        controller = self._controller
        if controller is not None:
            return controller
        return self._create_controller_locked(endpoint)

    def _create_controller_locked(
        self,
        endpoint: TcpAddress,
    ) -> AdbTransportListWatchController:
        if self._controller is not None:
            raise RuntimeError("a controller already exists")
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        factory = self._controller_factory
        if factory is None:
            attachment_factory = self._attachment_factory
            if attachment_factory is None:
                raise RuntimeError("transport-list watch attachment factory is unavailable")
            controller = ThreadedAdbTransportListWatchController(
                endpoint,
                self._bus,
                self._transport_list_observation_coordinator,
                startup_timeout_seconds=self._policy.episode_timeout_seconds,
                _attachment_factory=attachment_factory,
            )
        else:
            controller = factory(
                endpoint,
                self._bus,
                self._transport_list_observation_coordinator,
            )
        if not isinstance(controller, AdbTransportListWatchController):
            raise TypeError("controller factory must return AdbTransportListWatchController")
        if controller.endpoint != endpoint:
            raise ValueError("controller factory returned a mismatched endpoint binding")
        self._controller = controller
        self._watch_active = False
        self._current_session_identity = None
        return controller

    def _begin_start_locked(self) -> object:
        if self._start_in_progress:
            raise RuntimeError("transport-list watch startup is already in progress")
        token = object()
        self._start_in_progress = True
        self._start_token = token
        return token

    def _cancel_start_locked(self) -> None:
        self._start_in_progress = False
        self._start_token = None

    def _ensure_subscriptions_locked(self) -> None:
        if self._subscriptions:
            return
        self._subscriptions = (
            self._bus.subscribe(AdbTransportListWatchStarted, self._on_watch_started),
            self._bus.subscribe(AdbTransportListWatchFailed, self._on_watch_failed),
            self._bus.subscribe(AdbTransportListWatchStopped, self._on_watch_stopped),
        )

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("transport-list watch supervisor is closed")


__all__ = ["AdbTransportListWatchSupervisor"]
