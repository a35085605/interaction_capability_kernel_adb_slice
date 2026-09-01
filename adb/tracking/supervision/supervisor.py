from __future__ import annotations

from collections.abc import Callable
from threading import Lock, Thread, current_thread

from adb.epoch import EpochIssuer
from adb.errors import AdbProtocolError, AdbServerConnectionError, AdbServiceError
from adb.server.failure import AdbServerConnectionFailure
from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServerIdentity
from adb.server.state import AdbServerStateView
from adb.tracking.supervision.policy import AdbTransportListWatchSupervisionPolicy
from adb.server.signal import (
    AdbServerRetired,
    AdbServerRecovered,
    AdbServerReconciliationRequested,
)
from adb.tracking.snapshot.identity import AdbTransportListSnapshotEpoch
from adb.tracking.snapshot.state import AdbTransportListSnapshotState
from adb.tracking.publication import (
    AdbTransportListStateBackedWatchPublisher,
)
from adb.tracking.watch_controller import (
    AdbTransportListWatchController,
    ThreadedAdbTransportListWatchController,
)
from adb.tracking.signal import (
    AdbTransportListWatchFailed,
    AdbTransportListWatchFailure,
    AdbTransportListWatchStarted,
    AdbTransportListWatchStopped,
)
from eventing import EventBus, EventPublisher, EventSubscriptionToken


_ThreadFactory = Callable[..., Thread]
_ControllerFactory = Callable[
    [
        AdbServerIdentity,
        AdbServerEndpoint,
        EventPublisher,
        EpochIssuer[AdbTransportListSnapshotEpoch],
    ],
    AdbTransportListWatchController,
]


def _default_thread_factory(*args, **kwargs) -> Thread:
    thread = Thread(*args, **kwargs)
    thread.daemon = True
    return thread


class AdbTransportListWatchSupervisor:
    """Maintain the requested transport-list watch across server lifetimes by reconciling fresh
    single-use controllers.
    """

    def __init__(
        self,
        server: AdbServerIdentity,
        endpoint: AdbServerEndpoint,
        event_bus: EventBus,
        policy: AdbTransportListWatchSupervisionPolicy,
        *,
        server_state: AdbServerStateView,
        transport_list_snapshot_epoch_issuer: EpochIssuer[AdbTransportListSnapshotEpoch],
        transport_list_state: AdbTransportListSnapshotState | None = None,
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
        if initial_state.server != server or initial_state.endpoint != endpoint:
            raise ValueError("server_state current server and endpoint must match")
        if not isinstance(transport_list_snapshot_epoch_issuer, EpochIssuer):
            raise TypeError("transport_list_snapshot_epoch_issuer must satisfy EpochIssuer")
        if transport_list_state is None:
            transport_list_state = AdbTransportListSnapshotState()
        if not isinstance(transport_list_state, AdbTransportListSnapshotState):
            raise TypeError("transport_list_state must be AdbTransportListSnapshotState or None")
        if _controller_factory is not None and not callable(_controller_factory):
            raise TypeError("_controller_factory must be callable or None")
        if not callable(_thread_factory):
            raise TypeError("_thread_factory must be callable")

        self._server_state = server_state
        self._bus = event_bus
        self._transport_list_state = transport_list_state
        self._watch_publisher = AdbTransportListStateBackedWatchPublisher(
            self._transport_list_state,
            self._server_state,
            self._bus,
        )
        self._policy = policy
        self._transport_list_snapshot_epoch_issuer = transport_list_snapshot_epoch_issuer
        self._controller_factory = _controller_factory
        self._thread_factory = _thread_factory
        self._lock = Lock()
        self._subscriptions: tuple[EventSubscriptionToken, ...] = ()
        self._watch_requested = False
        self._controller: AdbTransportListWatchController | None = None
        self._watch_active = False
        self._start_in_progress = False
        self._attempt_threads: set[Thread] = set()
        self._closed = False

    @property
    def server(self) -> AdbServerIdentity | None:
        """Current server lifetime from the runtime authoritative state."""

        return self._server_state.current

    @property
    def server_state(self) -> AdbServerStateView:
        """Authoritative server-state view shared with the owning runtime."""

        return self._server_state

    @property
    def transport_list_state(self) -> AdbTransportListSnapshotState:
        """Shared transport-list snapshot state committed before watch events are published."""

        return self._transport_list_state

    @property
    def watch_requested(self) -> bool:
        with self._lock:
            return self._watch_requested

    @property
    def watch_active(self) -> bool:
        with self._lock:
            return self._watch_active

    def start(self) -> bool:
        """Request a transport-list watch and establish a controller for the current server."""

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
            controller = self._create_controller_locked(server, endpoint)
            self._start_in_progress = True

        return self._attempt_start(controller)

    def reconcile(self) -> None:
        """Reconcile the watch request against the authoritative current server."""

        controller_to_stop: AdbTransportListWatchController | None = None
        launch: tuple[Thread, AdbTransportListWatchController] | None = None

        with self._lock:
            self._require_open()
            if not self._watch_requested:
                return
            state = self._server_state.snapshot()
            server = state.server
            endpoint = state.endpoint
            controller = self._controller
            if server is None:
                controller_to_stop = self._detach_controller_locked()
            elif controller is not None and controller.server != server:
                controller_to_stop = self._detach_controller_locked()
            if (
                server is not None
                and self._controller is None
                and not self._start_in_progress
            ):
                if endpoint is None:
                    raise RuntimeError("active ADB server state has no endpoint")
                controller = self._create_controller_locked(server, endpoint)
                thread = self._thread_factory(
                    target=self._run_start_attempt,
                    args=(controller,),
                    name=(
                        "adb-transport-list-watch-reconciliation-"
                        f"{endpoint.host}-{endpoint.port}-{server.epoch}"
                    ),
                )
                self._start_in_progress = True
                self._attempt_threads.add(thread)
                launch = (thread, controller)

        if controller_to_stop is not None:
            controller_to_stop.stop()
        if launch is not None:
            thread, controller = launch
            try:
                thread.start()
            except BaseException:
                with self._lock:
                    self._attempt_threads.discard(thread)
                    if self._controller is controller:
                        self._detach_controller_locked()
                controller.stop()
                raise

    def close(self) -> None:
        """Stop watch supervision and join in-flight startup attempts."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._watch_requested = False
            subscriptions = self._subscriptions
            self._subscriptions = ()
            controller = self._detach_controller_locked()
            attempt_threads = tuple(self._attempt_threads)
        for token in subscriptions:
            self._bus.unsubscribe(token)
        if controller is not None:
            controller.stop()
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
                or self._server_state.current != controller.server
            ):
                return
            self._watch_active = True

    def _on_watch_failed(self, event: AdbTransportListWatchFailed) -> None:
        request_server_reconciliation = False
        failed_server: AdbServerIdentity | None = None
        with self._lock:
            current = self._controller
            if self._closed or current is None or event.server != current.server:
                return
            failed_server = current.server
            controller = self._detach_controller_locked()
            if event.failure is AdbTransportListWatchFailure.SERVER_CONNECTION:
                request_server_reconciliation = (
                    self._watch_requested
                    and self._server_state.current == failed_server
                )
        assert controller is not None
        controller.stop()
        if request_server_reconciliation:
            assert failed_server is not None
            self._bus.publish(
                AdbServerReconciliationRequested(
                    failed_server,
                    AdbServerConnectionFailure(event.diagnostic),
                )
            )

    def _on_watch_stopped(self, event: AdbTransportListWatchStopped) -> None:
        with self._lock:
            current = self._controller
            if self._closed or current is None or event.server != current.server:
                return
            controller = self._detach_controller_locked()
        assert controller is not None
        controller.stop()

    def _on_server_retired(self, _event: AdbServerRetired) -> None:
        self.reconcile()

    def _on_server_recovered(self, _event: AdbServerRecovered) -> None:
        self.reconcile()

    def _run_start_attempt(self, controller: AdbTransportListWatchController) -> None:
        active_thread = current_thread()
        try:
            with self._lock:
                if (
                    self._closed
                    or not self._watch_requested
                    or self._controller is not controller
                ):
                    return
            self._attempt_start(controller)
        finally:
            with self._lock:
                self._attempt_threads.discard(active_thread)

    def _attempt_start(self, controller: AdbTransportListWatchController) -> bool:
        try:
            controller.start()
        except AdbServerConnectionError as exc:
            return self._complete_start_attempt(
                controller,
                started=False,
                failure=AdbTransportListWatchFailure.SERVER_CONNECTION,
                diagnostic=str(exc),
            )
        except AdbServiceError as exc:
            return self._complete_start_attempt(
                controller,
                started=False,
                failure=AdbTransportListWatchFailure.SERVICE,
                diagnostic=str(exc),
            )
        except AdbProtocolError as exc:
            return self._complete_start_attempt(
                controller,
                started=False,
                failure=AdbTransportListWatchFailure.PROTOCOL,
                diagnostic=str(exc),
            )
        except RuntimeError:
            return self._complete_start_attempt(controller, started=False)
        except BaseException:
            controller_to_stop: AdbTransportListWatchController | None = None
            with self._lock:
                if self._controller is controller:
                    controller_to_stop = self._detach_controller_locked()
            if controller_to_stop is not None:
                controller_to_stop.stop()
            raise
        return self._complete_start_attempt(controller, started=True)

    def _complete_start_attempt(
        self,
        controller: AdbTransportListWatchController,
        *,
        started: bool,
        failure: AdbTransportListWatchFailure | None = None,
        diagnostic: str | None = None,
    ) -> bool:
        request_server_reconciliation = False
        reconciliation_server: AdbServerIdentity | None = None
        controller_to_stop: AdbTransportListWatchController | None = None
        publish_failure = False

        with self._lock:
            if self._controller is not controller:
                return False
            self._start_in_progress = False
            keep_controller = (
                started
                and not self._closed
                and self._watch_requested
                and self._server_state.current == controller.server
                and controller.active
            )
            if keep_controller:
                self._watch_active = True
            else:
                controller_to_stop = self._detach_controller_locked()
                publish_failure = failure is not None
                if failure is AdbTransportListWatchFailure.SERVER_CONNECTION:
                    reconciliation_server = controller.server
                    request_server_reconciliation = (
                        self._watch_requested
                        and self._server_state.current == reconciliation_server
                    )

        if controller_to_stop is not None:
            controller_to_stop.stop()
        if publish_failure:
            assert failure is not None
            self._bus.publish(
                AdbTransportListWatchFailed(
                    controller.server,
                    failure,
                    diagnostic,
                )
            )
        if request_server_reconciliation:
            assert reconciliation_server is not None
            self._bus.publish(
                AdbServerReconciliationRequested(
                    reconciliation_server,
                    AdbServerConnectionFailure(diagnostic),
                )
            )
        return keep_controller

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
        controller = (
            ThreadedAdbTransportListWatchController(
                server,
                endpoint,
                self._watch_publisher,
                startup_timeout_seconds=self._policy.episode_timeout_seconds,
                transport_list_snapshot_epoch_issuer=self._transport_list_snapshot_epoch_issuer,
            )
            if factory is None
            else factory(
                server,
                endpoint,
                self._watch_publisher,
                self._transport_list_snapshot_epoch_issuer,
            )
        )
        if not isinstance(controller, AdbTransportListWatchController):
            raise TypeError("controller factory must return AdbTransportListWatchController")
        if controller.server != server or controller.endpoint != endpoint:
            raise ValueError("controller factory returned a mismatched server binding")
        self._controller = controller
        self._watch_active = False
        return controller

    def _detach_controller_locked(self) -> AdbTransportListWatchController | None:
        controller = self._controller
        if controller is not None:
            self._watch_publisher.end_watch(controller.server)
        self._controller = None
        self._watch_active = False
        self._start_in_progress = False
        return controller

    def _ensure_subscriptions_locked(self) -> None:
        if self._subscriptions:
            return
        self._subscriptions = (
            self._bus.subscribe(AdbTransportListWatchStarted, self._on_watch_started),
            self._bus.subscribe(AdbTransportListWatchFailed, self._on_watch_failed),
            self._bus.subscribe(AdbTransportListWatchStopped, self._on_watch_stopped),
            self._bus.subscribe(
                AdbServerRetired,
                self._on_server_retired,
            ),
            self._bus.subscribe(
                AdbServerRecovered,
                self._on_server_recovered,
            ),
        )

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("transport-list watch supervisor is closed")


__all__ = ["AdbTransportListWatchSupervisor"]
