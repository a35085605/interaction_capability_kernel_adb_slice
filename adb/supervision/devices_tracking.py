from __future__ import annotations

from collections.abc import Callable
from threading import Lock, Thread, current_thread

from adb.server.failure import AdbServerConnectionFailure
from adb.server.identity import AdbServer
from adb.supervision.model import AdbDevicesTrackingSupervisionPolicy
from adb.server.signal import (
    AdbServerRetired,
    AdbServerRecovered,
    AdbServerReconciliationRequested,
)
from adb.transport.inventory.start import (
    AdbDevicesTrackingReadiness,
    AdbDevicesTrackingStart,
    AdbDevicesTrackingStartOrchestrator,
    AdbDevicesTrackingStartPolicy,
    AdbDevicesTrackingStartResult,
    AdbDevicesTrackingStartStatus,
)
from adb.transport.inventory.tracker import (
    AdbDevicesTracker,
    AdbDevicesTrackingScope,
)
from adb.transport.signal import (
    AdbDevicesTrackingFailed,
    AdbDevicesTrackingFailure,
    AdbDevicesTrackingStarted,
    AdbDevicesTrackingStopped,
)
from eventing import EventBus, EventSubscriptionToken


_ThreadFactory = Callable[..., Thread]
_TrackerFactory = Callable[[AdbServer, EventBus], AdbDevicesTrackingScope]


def _default_thread_factory(*args, **kwargs) -> Thread:
    thread = Thread(*args, **kwargs)
    thread.daemon = True
    return thread


def _default_tracker_factory(
    server: AdbServer,
    event_bus: EventBus,
) -> AdbDevicesTrackingScope:
    return AdbDevicesTracker(server, event_bus)


def _project_server(server: AdbServer | None) -> AdbDevicesTrackingReadiness:
    if server is None:
        return AdbDevicesTrackingReadiness.WAITING_FOR_SERVER
    if not isinstance(server, AdbServer):
        raise TypeError("server must be AdbServer or None")
    return AdbDevicesTrackingReadiness.READY


class AdbDevicesTrackingSupervisor:
    """Maintain durable tracking intent by constructing single-use tracker scopes.

    A tracker never crosses a terminal boundary. Server retirement destroys the current
    tracker immediately. A fresh server later permits a new tracker instance to be
    constructed and started. Tracking stop/failure follows the same rule: the old tracker is
    discarded rather than restarted.

    No tracker epoch counter is required because only one tracker scope is current, and terminal
    scopes are never reused. Background start orchestration may finish late, but its result is
    accepted only while the tracker object it started is still the current scope.
    """

    def __init__(
        self,
        server: AdbServer,
        event_bus: EventBus,
        policy: AdbDevicesTrackingSupervisionPolicy,
        *,
        _tracker_factory: _TrackerFactory = _default_tracker_factory,
        _thread_factory: _ThreadFactory = _default_thread_factory,
    ) -> None:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        if not callable(getattr(event_bus, "publish", None)) or not callable(
            getattr(event_bus, "subscribe", None)
        ) or not callable(getattr(event_bus, "unsubscribe", None)):
            raise TypeError("event_bus must satisfy EventBus")
        if not isinstance(policy, AdbDevicesTrackingSupervisionPolicy):
            raise TypeError("policy must be AdbDevicesTrackingSupervisionPolicy")
        if not callable(_tracker_factory):
            raise TypeError("_tracker_factory must be callable")

        self.server: AdbServer | None = server
        self.endpoint = server.endpoint
        self._bus = event_bus
        self._policy = policy
        self._tracker_factory = _tracker_factory
        self._thread_factory = _thread_factory
        self._lock = Lock()
        self._subscriptions: tuple[EventSubscriptionToken, ...] = ()
        self._desired_tracking = False
        self._readiness = AdbDevicesTrackingReadiness.INDETERMINATE
        self._tracker: AdbDevicesTrackingScope | None = None
        self._start: AdbDevicesTrackingStartOrchestrator | None = None
        self._tracking_active = False
        self._server_identity: AdbServer | None = None
        self._latest_server_epoch: int | None = None
        self._start_in_progress = False
        self._attempt_threads: set[Thread] = set()
        self._closed = False

    @property
    def desired_tracking(self) -> bool:
        with self._lock:
            return self._desired_tracking

    @property
    def start_readiness(self) -> AdbDevicesTrackingReadiness:
        with self._lock:
            return self._readiness

    @property
    def tracking_active(self) -> bool:
        with self._lock:
            return self._tracking_active

    def start(self) -> bool:
        """Declare durable intent and start a fresh tracker for the initial server."""

        server = self.server
        readiness = _project_server(server)
        with self._lock:
            self._require_open()
            if self._desired_tracking:
                raise RuntimeError("transport-inventory tracking supervisor is already started")
            self._ensure_subscriptions_locked()
            self._desired_tracking = True
            self._readiness = readiness
            self._server_identity = server
            if server is not None:
                self._latest_server_epoch = server.epoch
            if readiness is not AdbDevicesTrackingReadiness.READY:
                return False
            tracker, starter = self._create_tracker_locked()
            self._start_in_progress = True

        try:
            result = self._start_once(starter, readiness)
        except BaseException:
            with self._lock:
                if self._tracker is tracker:
                    self._start_in_progress = False
                    self._tracker = None
                    self._start = None
            tracker.close()
            raise
        return self._handle_start_result(tracker, result)

    def reconcile(self, server: AdbServer | None) -> None:
        """Reconcile tracking intent against the current active server."""

        readiness = _project_server(server)
        tracker_to_close: AdbDevicesTrackingScope | None = None
        launch: tuple[
            Thread,
            AdbDevicesTrackingScope,
            AdbDevicesTrackingStartOrchestrator,
        ] | None = None

        with self._lock:
            self._require_open()
            if not self._desired_tracking:
                return
            server_identity = server
            epoch = server_identity.epoch if server_identity is not None else None
            if (
                epoch is not None
                and self._latest_server_epoch is not None
                and epoch < self._latest_server_epoch
            ):
                return
            server_changed = server_identity != self._server_identity
            if server is not None and server.endpoint != self.endpoint:
                raise ValueError("recovered server endpoint does not match tracking endpoint")
            self.server = server
            self._readiness = readiness
            self._server_identity = server_identity
            if epoch is not None and (
                self._latest_server_epoch is None
                or epoch > self._latest_server_epoch
            ):
                self._latest_server_epoch = epoch
            if readiness is not AdbDevicesTrackingReadiness.READY:
                tracker_to_close = self._detach_tracker_locked()
            elif server_changed and self._tracker is not None:
                tracker_to_close = self._detach_tracker_locked()
            if (
                readiness is AdbDevicesTrackingReadiness.READY
                and self._tracker is None
                and not self._start_in_progress
            ):
                tracker, starter = self._create_tracker_locked()
                thread = self._thread_factory(
                    target=self._run_start_attempt,
                    args=(tracker, starter, readiness),
                    name=(
                        "adb-tracking-reconciliation-"
                        f"{self.endpoint.host}-{self.endpoint.port}"
                    ),
                )
                self._start_in_progress = True
                self._attempt_threads.add(thread)
                launch = (thread, tracker, starter)

        if tracker_to_close is not None:
            tracker_to_close.close()
        if launch is not None:
            thread, tracker, _ = launch
            try:
                thread.start()
            except BaseException:
                with self._lock:
                    self._attempt_threads.discard(thread)
                    if self._tracker is tracker:
                        self._start_in_progress = False
                        self._tracker = None
                        self._start = None
                tracker.close()
                raise

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._desired_tracking = False
            self._readiness = AdbDevicesTrackingReadiness.INDETERMINATE
            self._server_identity = None
            subscriptions = self._subscriptions
            self._subscriptions = ()
            tracker = self._detach_tracker_locked()
            attempt_threads = tuple(self._attempt_threads)
        for token in subscriptions:
            self._bus.unsubscribe(token)
        if tracker is not None:
            tracker.close()
        for thread in attempt_threads:
            if thread is not current_thread():
                thread.join()

    def _on_tracking_started(self, event: AdbDevicesTrackingStarted) -> None:
        if event.server != self.server:
            return
        with self._lock:
            if self._closed or not self._desired_tracking or self._tracker is None:
                return
            self._tracking_active = True

    def _on_tracking_failed(self, event: AdbDevicesTrackingFailed) -> None:
        if event.server != self.server:
            return
        request_server_reconciliation = False
        server: AdbServer | None = None
        with self._lock:
            if self._closed or self._tracker is None:
                return
            tracker = self._detach_tracker_locked()
            if event.failure is AdbDevicesTrackingFailure.SERVER_CONNECTION:
                self._readiness = AdbDevicesTrackingReadiness.WAITING_FOR_SERVER
                server = self._server_identity
                request_server_reconciliation = (
                    self._desired_tracking and server is not None
                )
        assert tracker is not None
        tracker.close()
        if request_server_reconciliation:
            assert server is not None
            self._bus.publish(
                AdbServerReconciliationRequested(
                    server,
                    AdbServerConnectionFailure(event.diagnostic),
                )
            )

    def _on_tracking_stopped(self, event: AdbDevicesTrackingStopped) -> None:
        if event.server != self.server:
            return
        with self._lock:
            if self._closed or self._tracker is None:
                return
            tracker = self._detach_tracker_locked()
        assert tracker is not None
        tracker.close()

    def _on_server_retired(self, event: AdbServerRetired) -> None:
        if event.endpoint != self.endpoint:
            return
        with self._lock:
            if self._closed:
                return
            if (
                self._latest_server_epoch is not None
                and event.server.epoch < self._latest_server_epoch
            ):
                return
            if (
                self._latest_server_epoch is None
                or event.server.epoch > self._latest_server_epoch
            ):
                self._latest_server_epoch = event.server.epoch
            if self._server_identity != event.server:
                return
            self._server_identity = None
            self.server = None
            self._readiness = AdbDevicesTrackingReadiness.WAITING_FOR_SERVER
            tracker = self._detach_tracker_locked()
        if tracker is not None:
            tracker.close()

    def _on_server_recovered(self, event: AdbServerRecovered) -> None:
        if event.endpoint != self.endpoint:
            return
        with self._lock:
            if self._closed or not self._desired_tracking:
                return
            if (
                self._latest_server_epoch is not None
                and event.server.epoch <= self._latest_server_epoch
            ):
                return
        self.reconcile(event.server)

    def _run_start_attempt(
        self,
        tracker: AdbDevicesTrackingScope,
        starter: AdbDevicesTrackingStartOrchestrator,
        readiness: AdbDevicesTrackingReadiness,
    ) -> None:
        active_thread = current_thread()
        try:
            with self._lock:
                if (
                    self._closed
                    or not self._desired_tracking
                    or self._tracker is not tracker
                    or self._start is not starter
                    or self._readiness is not readiness
                ):
                    return
            result = self._start_once(starter, readiness)
            self._handle_start_result(tracker, result)
        finally:
            with self._lock:
                self._attempt_threads.discard(active_thread)

    def _start_once(
        self,
        starter: AdbDevicesTrackingStartOrchestrator,
        readiness: AdbDevicesTrackingReadiness,
    ) -> AdbDevicesTrackingStartResult:
        return starter.start(
            AdbDevicesTrackingStart(
                server=starter.server,
                readiness=readiness,
                policy=AdbDevicesTrackingStartPolicy(
                    self._policy.episode_timeout_seconds,
                ),
            )
        )

    def _handle_start_result(
        self,
        tracker: AdbDevicesTrackingScope,
        result: AdbDevicesTrackingStartResult,
    ) -> bool:
        request_server_reconciliation = False
        reconciliation_server: AdbServer | None = None
        keep_tracker = False
        tracker_to_close: AdbDevicesTrackingScope | None = None

        with self._lock:
            if self._tracker is not tracker:
                return False
            self._start_in_progress = False
            if not self._closed and self._desired_tracking:
                keep_tracker = (
                    result.status is AdbDevicesTrackingStartStatus.SATISFIED
                    and self._readiness is AdbDevicesTrackingReadiness.READY
                    and tracker.active
                )
                if (
                    not keep_tracker
                    and result.tracking_failure
                    is AdbDevicesTrackingFailure.SERVER_CONNECTION
                ):
                    self._readiness = AdbDevicesTrackingReadiness.WAITING_FOR_SERVER
                    reconciliation_server = self._server_identity
                    request_server_reconciliation = reconciliation_server is not None
            if not keep_tracker:
                tracker_to_close = self._detach_tracker_locked()

        if tracker_to_close is not None:
            tracker_to_close.close()
        if request_server_reconciliation:
            assert reconciliation_server is not None
            self._bus.publish(
                AdbServerReconciliationRequested(
                    reconciliation_server,
                    AdbServerConnectionFailure(result.diagnostic),
                )
            )
        return keep_tracker

    def _create_tracker_locked(
        self,
    ) -> tuple[AdbDevicesTrackingScope, AdbDevicesTrackingStartOrchestrator]:
        if self._tracker is not None:
            raise RuntimeError("a tracker scope already exists")
        server = self.server
        if server is None:
            raise RuntimeError("cannot create tracker without an active server")
        tracker = self._tracker_factory(server, self._bus)
        if not isinstance(tracker, AdbDevicesTrackingScope):
            raise TypeError("tracker factory must return AdbDevicesTrackingScope")
        starter = AdbDevicesTrackingStartOrchestrator(
            server,
            self._bus,
            tracker,
        )
        self._tracker = tracker
        self._start = starter
        self._tracking_active = False
        return tracker, starter

    def _detach_tracker_locked(self) -> AdbDevicesTrackingScope | None:
        tracker = self._tracker
        self._tracker = None
        self._start = None
        self._tracking_active = False
        self._start_in_progress = False
        return tracker

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
            raise RuntimeError("transport-inventory tracking supervisor is closed")


__all__ = ["AdbDevicesTrackingSupervisor"]
