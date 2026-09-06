from __future__ import annotations

from collections.abc import Callable
from threading import Lock, Thread, current_thread

from adb.server.failure import AdbServerConnectionFailure
from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServerIdentity
from adb.server.state import AdbServerStateView
from adb.transport_list.watch.supervision.policy import AdbTransportListWatchSupervisionPolicy
from adb.server.signal import AdbServerReconciliationRequested
from adb.transport_list.coordinator import AdbTransportListCoordinator
from adb.transport_list.state import AdbTransportListStateStore
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
_ControllerFactory = Callable[
    [
        AdbServerIdentity,
        AdbServerEndpoint,
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
    """Maintain one long-lived watch controller across authoritative server lifetimes.

    Reconciliation replaces only the controller's short-lived watch session. Controller and
    attachment ownership is scoped to each session and remains controller-owned while opening.
    """

    def __init__(
        self,
        server: AdbServerIdentity,
        endpoint: AdbServerEndpoint,
        event_bus: EventBus,
        policy: AdbTransportListWatchSupervisionPolicy,
        *,
        server_state: AdbServerStateView,
        transport_list_state: AdbTransportListStateStore | None = None,
        transport_list_observation_coordinator: AdbTransportListCoordinator
        | None = None,
        _attachment_factory: _TransportListWatchAttachmentFactory | None = None,
        _controller_factory: _ControllerFactory | None = None,
        _thread_factory: _ThreadFactory = _default_thread_factory,
    ) -> None:
        if not isinstance(server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if not callable(getattr(event_bus, "publish", None)) or not callable(
            getattr(event_bus, "subscribe", None)
        ) or not callable(getattr(event_bus, "unsubscribe", None)):
            raise TypeError("event_bus must satisfy EventBus")
        if not isinstance(policy, AdbTransportListWatchSupervisionPolicy):
            raise TypeError("policy must be AdbTransportListWatchSupervisionPolicy")
        if not isinstance(server_state, AdbServerStateView):
            raise TypeError("server_state must satisfy AdbServerStateView")
        initial_state = server_state.snapshot()
        if initial_state.current_identity != server or initial_state.endpoint != endpoint:
            raise ValueError("server_state current server and endpoint must match")
        if transport_list_observation_coordinator is None:
            if transport_list_state is None:
                transport_list_state = AdbTransportListStateStore()
            if not isinstance(transport_list_state, AdbTransportListStateStore):
                raise TypeError(
                    "transport_list_state must be AdbTransportListStateStore or None"
                )
            transport_list_observation_coordinator = AdbTransportListCoordinator(
                transport_list_state,
                server_state,
                publisher=event_bus,
            )
        else:
            if not isinstance(
                transport_list_observation_coordinator,
                AdbTransportListCoordinator,
            ):
                raise TypeError(
                    "transport_list_observation_coordinator must be "
                    "AdbTransportListCoordinator or None"
                )
            if transport_list_observation_coordinator.server_state is not server_state:
                raise ValueError(
                    "transport-list observation coordinator must share server_state"
                )
            coordinator_state = (
                transport_list_observation_coordinator.transport_list_state
            )
            if not isinstance(coordinator_state, AdbTransportListStateStore):
                raise TypeError(
                    "transport-list observation coordinator state must be "
                    "AdbTransportListStateStore"
                )
            if (
                transport_list_state is not None
                and transport_list_state is not coordinator_state
            ):
                raise ValueError(
                    "transport_list_state must match observation coordinator state"
                )
            transport_list_state = coordinator_state
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

        self._server_state = server_state
        self._bus = event_bus
        self._transport_list_state = transport_list_state
        self._transport_list_observation_coordinator = (
            transport_list_observation_coordinator
        )
        self._policy = policy
        self._attachment_factory = _attachment_factory
        self._controller_factory = _controller_factory
        self._thread_factory = _thread_factory
        self._lock = Lock()
        self._subscriptions: tuple[EventSubscriptionToken, ...] = ()
        self._watch_requested = False
        self._controller: AdbTransportListWatchController | None = None
        self._watch_active = False
        self._start_in_progress = False
        self._start_token: object | None = None
        self._attempt_threads: set[Thread] = set()
        self._closed = False

    @property
    def server(self) -> AdbServerIdentity | None:
        """Current server lifetime from the runtime authoritative state."""

        return self._server_state.current_identity

    @property
    def server_state(self) -> AdbServerStateView:
        """Authoritative server-state view shared with the owning runtime."""

        return self._server_state

    @property
    def transport_list_state(self) -> AdbTransportListStateStore:
        """Shared transport-list state committed by the observation coordinator."""

        return self._transport_list_state

    @property
    def transport_list_observation_coordinator(
        self,
    ) -> AdbTransportListCoordinator:
        """Shared authority boundary used to commit and publish watch observations."""

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
            state = self._server_state.snapshot()
            server = state.server
            endpoint = state.endpoint
            if server is None or endpoint is None:
                return False
            controller = self._ensure_controller_locked(server, endpoint)
            token = self._begin_start_locked()

        return self._attempt_start(controller, server, endpoint, token)

    def reconcile(self) -> None:
        """Reconcile the current short-lived session against authoritative server state."""

        controller_to_stop: AdbTransportListWatchController | None = None
        launch: tuple[
            Thread,
            AdbTransportListWatchController,
            AdbServerIdentity,
            AdbServerEndpoint,
            object,
        ] | None = None

        with self._lock:
            self._require_open()
            if not self._watch_requested:
                return

            state = self._server_state.snapshot()
            server = state.server
            endpoint = state.endpoint
            controller = self._controller

            if server is None:
                if controller is not None and (controller.active or self._start_in_progress):
                    self._cancel_start_locked()
                    self._watch_active = False
                    controller_to_stop = controller
            else:
                if endpoint is None:
                    raise RuntimeError("active ADB server state has no endpoint")
                if controller is None:
                    controller = self._create_controller_locked(server, endpoint)

                binding_changed = (
                    controller.server != server or controller.endpoint != endpoint
                )
                if binding_changed and (controller.active or self._start_in_progress):
                    self._cancel_start_locked()
                    self._watch_active = False
                    controller_to_stop = controller

                if not self._start_in_progress and (
                    binding_changed or not controller.active
                ):
                    token = self._begin_start_locked()
                    thread = self._thread_factory(
                        target=self._run_start_attempt,
                        args=(controller, server, endpoint, token),
                        name=(
                            "adb-transport-list-watch-reconciliation-"
                            f"{endpoint.host}-{endpoint.port}-{server}"
                        ),
                    )
                    self._attempt_threads.add(thread)
                    launch = (thread, controller, server, endpoint, token)

        if controller_to_stop is not None:
            controller_to_stop.stop()

        if launch is not None:
            thread, controller, server, endpoint, token = launch
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
            if (
                self._closed
                or not self._watch_requested
                or controller is None
                or event.server != controller.server
                or self._server_state.current_identity != event.server
            ):
                return
            self._watch_active = True

    def _on_watch_failed(self, event: AdbTransportListWatchFailed) -> None:
        request_server_reconciliation = False
        with self._lock:
            controller = self._controller
            if (
                self._closed
                or controller is None
                or event.server != controller.server
            ):
                return
            self._watch_active = False
            request_server_reconciliation = (
                self._watch_requested
                and self._server_state.current_identity == event.server
                and isinstance(
                    event.failure,
                    AdbTransportListWatchServerConnectionFailure,
                )
            )

        if request_server_reconciliation:
            self._bus.publish(
                AdbServerReconciliationRequested(
                    event.server,
                    AdbServerConnectionFailure(event.failure.diagnostic),
                )
            )

    def _on_watch_stopped(self, event: AdbTransportListWatchStopped) -> None:
        with self._lock:
            controller = self._controller
            if (
                self._closed
                or controller is None
                or event.server != controller.server
            ):
                return
            self._watch_active = False

    def _run_start_attempt(
        self,
        controller: AdbTransportListWatchController,
        server: AdbServerIdentity,
        endpoint: AdbServerEndpoint,
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
            self._attempt_start(controller, server, endpoint, token)
        finally:
            with self._lock:
                self._attempt_threads.discard(active_thread)

    def _attempt_start(
        self,
        controller: AdbTransportListWatchController,
        server: AdbServerIdentity,
        endpoint: AdbServerEndpoint,
        token: object,
    ) -> bool:
        try:
            result = controller.start(server, endpoint)
        except BaseException:
            with self._lock:
                if self._start_token is token:
                    self._cancel_start_locked()
            raise

        if isinstance(result, AdbTransportListWatchStartSucceeded):
            return self._complete_start_attempt(
                controller,
                server,
                endpoint,
                token,
                started=True,
            )
        if isinstance(result, AdbTransportListWatchStartFailed):
            return self._complete_start_attempt(
                controller,
                server,
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
                server,
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
        server: AdbServerIdentity,
        endpoint: AdbServerEndpoint,
        token: object,
        *,
        started: bool,
        failure: AdbTransportListWatchFailure | None = None,
    ) -> bool:
        stop_superseded_session = False
        publish_failure = False

        with self._lock:
            if self._controller is not controller or self._start_token is not token:
                return False

            self._cancel_start_locked()
            state = self._server_state.snapshot()
            target_is_current = (
                not self._closed
                and self._watch_requested
                and state.server == server
                and state.endpoint == endpoint
                and controller.server == server
                and controller.endpoint == endpoint
            )
            watch_active = started and target_is_current and controller.active
            self._watch_active = watch_active
            stop_superseded_session = started and not watch_active
            publish_failure = failure is not None and target_is_current

        if stop_superseded_session:
            controller.stop()
        if publish_failure:
            assert failure is not None
            self._bus.publish(AdbTransportListWatchFailed(server, failure))
        return watch_active

    def _ensure_controller_locked(
        self,
        server: AdbServerIdentity,
        endpoint: AdbServerEndpoint,
    ) -> AdbTransportListWatchController:
        controller = self._controller
        if controller is not None:
            return controller
        return self._create_controller_locked(server, endpoint)

    def _create_controller_locked(
        self,
        server: AdbServerIdentity,
        endpoint: AdbServerEndpoint,
    ) -> AdbTransportListWatchController:
        if self._controller is not None:
            raise RuntimeError("a controller already exists")
        if not isinstance(server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        factory = self._controller_factory
        if factory is None:
            attachment_factory = self._attachment_factory
            if attachment_factory is None:
                raise RuntimeError("transport-list watch attachment factory is unavailable")
            controller = ThreadedAdbTransportListWatchController(
                server,
                endpoint,
                self._bus,
                self._transport_list_observation_coordinator,
                startup_timeout_seconds=self._policy.episode_timeout_seconds,
                _attachment_factory=attachment_factory,
            )
        else:
            controller = factory(
                server,
                endpoint,
                self._bus,
                self._transport_list_observation_coordinator,
            )
        if not isinstance(controller, AdbTransportListWatchController):
            raise TypeError("controller factory must return AdbTransportListWatchController")
        if controller.server != server or controller.endpoint != endpoint:
            raise ValueError("controller factory returned a mismatched initial server binding")
        self._controller = controller
        self._watch_active = False
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
