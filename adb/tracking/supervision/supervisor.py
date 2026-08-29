from __future__ import annotations

from collections.abc import Callable
from threading import Lock, Thread, current_thread

from adb.epoch import EpochIssuer
from adb.errors import AdbProtocolError, AdbServerConnectionError, AdbServiceError
from adb.server.failure import AdbServerConnectionFailure
from adb.server.lifetime import AdbServerLifetime
from adb.server.state import AdbServerStateView
from adb.tracking.supervision.policy import AdbDevicesTrackingSupervisionPolicy
from adb.server.signal import (
    AdbServerRetired,
    AdbServerRecovered,
    AdbServerReconciliationRequested,
)
from adb.tracking.snapshot.identity import AdbDevicesSnapshotEpoch
from adb.tracking.snapshot.state import AdbDevicesSnapshotState
from adb.tracking.publication import (
    AdbDevicesSnapshotStateBackedTrackingPublisher,
)
from adb.tracking.controller import (
    AdbDevicesTrackingController,
    SmartSocketAdbDevicesTrackingController,
)
from adb.tracking.signal import (
    AdbDevicesTrackingFailed,
    AdbDevicesTrackingFailure,
    AdbDevicesTrackingStarted,
    AdbDevicesTrackingStopped,
)
from eventing import EventBus, EventPublisher, EventSubscriptionToken


_ThreadFactory = Callable[..., Thread]
_ControllerFactory = Callable[
    [AdbServerLifetime, EventPublisher, EpochIssuer[AdbDevicesSnapshotEpoch]],
    AdbDevicesTrackingController,
]


def _default_thread_factory(*args, **kwargs) -> Thread:
    thread = Thread(*args, **kwargs)
    thread.daemon = True
    return thread


class AdbDevicesTrackingSupervisor:
    """Maintain desired track-devices state across ADB server lifetimes.

    Controllers are single-use; terminal or server-replacement events discard the current controller
    and reconciliation creates a fresh one while tracking remains desired.
    """

    def __init__(
        self,
        server: AdbServerLifetime,
        event_bus: EventBus,
        policy: AdbDevicesTrackingSupervisionPolicy,
        *,
        server_state: AdbServerStateView,
        devices_snapshot_epoch_issuer: EpochIssuer[AdbDevicesSnapshotEpoch],
        snapshot_state: AdbDevicesSnapshotState | None = None,
        _controller_factory: _ControllerFactory | None = None,
        _thread_factory: _ThreadFactory = _default_thread_factory,
    ) -> None:
        if not isinstance(server, AdbServerLifetime):
            raise TypeError("server must be AdbServerLifetime")
        if not callable(getattr(event_bus, "publish", None)) or not callable(
            getattr(event_bus, "subscribe", None)
        ) or not callable(getattr(event_bus, "unsubscribe", None)):
            raise TypeError("event_bus must satisfy EventBus")
        if not isinstance(policy, AdbDevicesTrackingSupervisionPolicy):
            raise TypeError("policy must be AdbDevicesTrackingSupervisionPolicy")
        if not isinstance(server_state, AdbServerStateView):
            raise TypeError("server_state must satisfy AdbServerStateView")
        if server_state.current != server:
            raise ValueError("server_state current server must match server")
        if not isinstance(devices_snapshot_epoch_issuer, EpochIssuer):
            raise TypeError("devices_snapshot_epoch_issuer must satisfy EpochIssuer")
        if snapshot_state is None:
            snapshot_state = AdbDevicesSnapshotState()
        if not isinstance(snapshot_state, AdbDevicesSnapshotState):
            raise TypeError("snapshot_state must be AdbDevicesSnapshotState or None")
        if _controller_factory is not None and not callable(_controller_factory):
            raise TypeError("_controller_factory must be callable or None")
        if not callable(_thread_factory):
            raise TypeError("_thread_factory must be callable")

        self._server_state = server_state
        self._bus = event_bus
        self._devices = snapshot_state
        self._tracking_publisher = AdbDevicesSnapshotStateBackedTrackingPublisher(
            self._devices,
            self._server_state,
            self._bus,
        )
        self._policy = policy
        self._devices_snapshot_epoch_issuer = devices_snapshot_epoch_issuer
        self._controller_factory = _controller_factory
        self._thread_factory = _thread_factory
        self._lock = Lock()
        self._subscriptions: tuple[EventSubscriptionToken, ...] = ()
        self._desired_tracking = False
        self._controller: AdbDevicesTrackingController | None = None
        self._tracking_active = False
        self._start_in_progress = False
        self._attempt_threads: set[Thread] = set()
        self._closed = False

    @property
    def server(self) -> AdbServerLifetime | None:
        """Current server lifetime from the runtime authoritative state."""

        return self._server_state.current

    @property
    def server_state(self) -> AdbServerStateView:
        """Read-only authoritative server state shared with the owning runtime."""

        return self._server_state

    @property
    def devices(self) -> AdbDevicesSnapshotState:
        """Shared current tracked-devices state committed before tracking events are published."""

        return self._devices

    @property
    def desired_tracking(self) -> bool:
        with self._lock:
            return self._desired_tracking

    @property
    def tracking_active(self) -> bool:
        with self._lock:
            return self._tracking_active

    def start(self) -> bool:
        """Declare tracking intent and establish a controller for the current server."""

        with self._lock:
            self._require_open()
            if self._desired_tracking:
                raise RuntimeError("track-devices supervisor is already started")
            self._ensure_subscriptions_locked()
            self._desired_tracking = True
            server = self._server_state.current
            if server is None:
                return False
            controller = self._create_controller_locked(server)
            self._start_in_progress = True

        return self._attempt_start(controller)

    def reconcile(self) -> None:
        """Reconcile tracking intent against the authoritative current server."""

        controller_to_stop: AdbDevicesTrackingController | None = None
        launch: tuple[Thread, AdbDevicesTrackingController] | None = None

        with self._lock:
            self._require_open()
            if not self._desired_tracking:
                return
            server = self._server_state.current
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
                controller = self._create_controller_locked(server)
                thread = self._thread_factory(
                    target=self._run_start_attempt,
                    args=(controller,),
                    name=(
                        "adb-tracking-reconciliation-"
                        f"{server.endpoint.host}-{server.endpoint.port}-{server.epoch}"
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
        """Stop tracking supervision and join in-flight startup attempts."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._desired_tracking = False
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

    def _on_tracking_started(self, event: AdbDevicesTrackingStarted) -> None:
        with self._lock:
            controller = self._controller
            if (
                self._closed
                or not self._desired_tracking
                or controller is None
                or event.server != controller.server
                or self._server_state.current != controller.server
            ):
                return
            self._tracking_active = True

    def _on_tracking_failed(self, event: AdbDevicesTrackingFailed) -> None:
        request_server_reconciliation = False
        failed_server: AdbServerLifetime | None = None
        with self._lock:
            current = self._controller
            if self._closed or current is None or event.server != current.server:
                return
            failed_server = current.server
            controller = self._detach_controller_locked()
            if event.failure is AdbDevicesTrackingFailure.SERVER_CONNECTION:
                request_server_reconciliation = (
                    self._desired_tracking
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

    def _on_tracking_stopped(self, event: AdbDevicesTrackingStopped) -> None:
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

    def _run_start_attempt(self, controller: AdbDevicesTrackingController) -> None:
        active_thread = current_thread()
        try:
            with self._lock:
                if (
                    self._closed
                    or not self._desired_tracking
                    or self._controller is not controller
                ):
                    return
            self._attempt_start(controller)
        finally:
            with self._lock:
                self._attempt_threads.discard(active_thread)

    def _attempt_start(self, controller: AdbDevicesTrackingController) -> bool:
        try:
            controller.start()
        except AdbServerConnectionError as exc:
            return self._complete_start_attempt(
                controller,
                started=False,
                failure=AdbDevicesTrackingFailure.SERVER_CONNECTION,
                diagnostic=str(exc),
            )
        except AdbServiceError as exc:
            return self._complete_start_attempt(
                controller,
                started=False,
                failure=AdbDevicesTrackingFailure.SERVICE,
                diagnostic=str(exc),
            )
        except AdbProtocolError as exc:
            return self._complete_start_attempt(
                controller,
                started=False,
                failure=AdbDevicesTrackingFailure.PROTOCOL,
                diagnostic=str(exc),
            )
        except RuntimeError:
            return self._complete_start_attempt(controller, started=False)
        except BaseException:
            controller_to_stop: AdbDevicesTrackingController | None = None
            with self._lock:
                if self._controller is controller:
                    controller_to_stop = self._detach_controller_locked()
            if controller_to_stop is not None:
                controller_to_stop.stop()
            raise
        return self._complete_start_attempt(controller, started=True)

    def _complete_start_attempt(
        self,
        controller: AdbDevicesTrackingController,
        *,
        started: bool,
        failure: AdbDevicesTrackingFailure | None = None,
        diagnostic: str | None = None,
    ) -> bool:
        request_server_reconciliation = False
        reconciliation_server: AdbServerLifetime | None = None
        controller_to_stop: AdbDevicesTrackingController | None = None
        publish_failure = False

        with self._lock:
            if self._controller is not controller:
                return False
            self._start_in_progress = False
            keep_controller = (
                started
                and not self._closed
                and self._desired_tracking
                and self._server_state.current == controller.server
                and controller.active
            )
            if keep_controller:
                self._tracking_active = True
            else:
                controller_to_stop = self._detach_controller_locked()
                publish_failure = failure is not None
                if failure is AdbDevicesTrackingFailure.SERVER_CONNECTION:
                    reconciliation_server = controller.server
                    request_server_reconciliation = (
                        self._desired_tracking
                        and self._server_state.current == reconciliation_server
                    )

        if controller_to_stop is not None:
            controller_to_stop.stop()
        if publish_failure:
            assert failure is not None
            self._bus.publish(
                AdbDevicesTrackingFailed(
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
        server: AdbServerLifetime,
    ) -> AdbDevicesTrackingController:
        if self._controller is not None:
            raise RuntimeError("a controller already exists")
        if not isinstance(server, AdbServerLifetime):
            raise TypeError("server must be AdbServerLifetime")
        factory = self._controller_factory
        controller = (
            SmartSocketAdbDevicesTrackingController(
                server,
                self._tracking_publisher,
                startup_timeout_seconds=self._policy.episode_timeout_seconds,
                devices_snapshot_epoch_issuer=self._devices_snapshot_epoch_issuer,
            )
            if factory is None
            else factory(
                server,
                self._tracking_publisher,
                self._devices_snapshot_epoch_issuer,
            )
        )
        if not isinstance(controller, AdbDevicesTrackingController):
            raise TypeError("controller factory must return AdbDevicesTrackingController")
        if controller.server != server:
            raise ValueError("controller factory returned a mismatched server lifetime")
        self._controller = controller
        self._tracking_active = False
        return controller

    def _detach_controller_locked(self) -> AdbDevicesTrackingController | None:
        controller = self._controller
        if controller is not None:
            self._tracking_publisher.end_tracking(controller.server)
        self._controller = None
        self._tracking_active = False
        self._start_in_progress = False
        return controller

    def _ensure_subscriptions_locked(self) -> None:
        if self._subscriptions:
            return
        self._subscriptions = (
            self._bus.subscribe(AdbDevicesTrackingStarted, self._on_tracking_started),
            self._bus.subscribe(AdbDevicesTrackingFailed, self._on_tracking_failed),
            self._bus.subscribe(AdbDevicesTrackingStopped, self._on_tracking_stopped),
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
            raise RuntimeError("track-devices supervisor is closed")


__all__ = ["AdbDevicesTrackingSupervisor"]
