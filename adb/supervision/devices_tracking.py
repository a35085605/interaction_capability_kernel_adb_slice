from __future__ import annotations

from collections.abc import Callable
from threading import Lock, Thread, current_thread

from adb.server.endpoint import AdbServerEndpoint
from adb.server.model import (
    AdbServerAvailability,
    AdbServerFailure,
    AdbServerFailureKind,
    AdbServerObservation,
)
from adb.supervision.model import AdbDevicesTrackingSupervisionPolicy
from adb.supervision.signal import (
    AdbServerOwnershipLost,
    AdbServerOwnershipRecovered,
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
    AdbDevicesTrackingController,
)
from adb.transport.signal import (
    AdbDevicesTrackingFailed,
    AdbDevicesTrackingFailure,
    AdbDevicesTrackingStarted,
    AdbDevicesTrackingStopped,
)
from eventing import EventBus, EventSubscriptionToken


_ThreadFactory = Callable[..., Thread]
_TrackerFactory = Callable[[AdbServerEndpoint, EventBus], AdbDevicesTrackingController]


def _default_thread_factory(*args, **kwargs) -> Thread:
    thread = Thread(*args, **kwargs)
    thread.daemon = True
    return thread


def _default_tracker_factory(
    endpoint: AdbServerEndpoint,
    event_bus: EventBus,
) -> AdbDevicesTrackingController:
    return AdbDevicesTracker(endpoint, event_bus)


def _project_server_observation(
    observation: AdbServerObservation,
) -> AdbDevicesTrackingReadiness:
    if not isinstance(observation, AdbServerObservation):
        raise TypeError("observation must be AdbServerObservation")
    availability = observation.availability
    if availability is AdbServerAvailability.AVAILABLE:
        return AdbDevicesTrackingReadiness.READY
    if availability is AdbServerAvailability.UNAVAILABLE:
        return AdbDevicesTrackingReadiness.WAITING_FOR_SERVER
    return AdbDevicesTrackingReadiness.INDETERMINATE


class AdbDevicesTrackingSupervisor:
    """Maintain durable tracking intent by constructing single-use tracker scopes.

    A tracker never crosses a terminal boundary. Server ownership loss destroys the current
    tracker immediately. Fresh server ownership later permits a new tracker instance to be
    constructed and started. Tracking stop/failure follows the same rule: the old tracker is
    discarded rather than restarted.

    No tracker generation is required because only one tracker scope is current, and terminal
    scopes are never reused. Background start orchestration may finish late, but its result is
    accepted only while the tracker object it started is still the current scope.
    """

    def __init__(
        self,
        endpoint: AdbServerEndpoint,
        event_bus: EventBus,
        policy: AdbDevicesTrackingSupervisionPolicy,
        *,
        _tracker_factory: _TrackerFactory = _default_tracker_factory,
        _thread_factory: _ThreadFactory = _default_thread_factory,
    ) -> None:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        if not callable(getattr(event_bus, "publish", None)) or not callable(
            getattr(event_bus, "subscribe", None)
        ) or not callable(getattr(event_bus, "unsubscribe", None)):
            raise TypeError("event_bus must satisfy EventBus")
        if not isinstance(policy, AdbDevicesTrackingSupervisionPolicy):
            raise TypeError("policy must be AdbDevicesTrackingSupervisionPolicy")
        if not callable(_tracker_factory):
            raise TypeError("_tracker_factory must be callable")

        self.endpoint = endpoint
        self._bus = event_bus
        self._policy = policy
        self._tracker_factory = _tracker_factory
        self._thread_factory = _thread_factory
        self._lock = Lock()
        self._subscriptions: tuple[EventSubscriptionToken, ...] = ()
        self._desired_tracking = False
        self._readiness = AdbDevicesTrackingReadiness.INDETERMINATE
        self._tracker: AdbDevicesTrackingController | None = None
        self._start: AdbDevicesTrackingStartOrchestrator | None = None
        self._tracking_active = False
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

    def start(self, server_observation: AdbServerObservation) -> bool:
        """Declare durable intent and synchronously start a fresh tracker when READY."""

        self._require_observation_endpoint(server_observation)
        readiness = _project_server_observation(server_observation)
        with self._lock:
            self._require_open()
            if self._desired_tracking:
                raise RuntimeError("transport-inventory tracking supervisor is already started")
            self._ensure_subscriptions_locked()
            self._desired_tracking = True
            self._readiness = readiness
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

    def reconcile(self, server_observation: AdbServerObservation) -> None:
        """Reconcile tracking intent by creating or destroying the current tracker scope."""

        self._require_observation_endpoint(server_observation)
        readiness = _project_server_observation(server_observation)
        tracker_to_close: AdbDevicesTrackingController | None = None
        launch: tuple[
            Thread,
            AdbDevicesTrackingController,
            AdbDevicesTrackingStartOrchestrator,
        ] | None = None

        with self._lock:
            self._require_open()
            if not self._desired_tracking:
                return
            self._readiness = readiness
            if readiness is not AdbDevicesTrackingReadiness.READY:
                tracker_to_close = self._detach_tracker_locked()
            elif self._tracker is None and not self._start_in_progress:
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
        if event.endpoint != self.endpoint:
            return
        with self._lock:
            if self._closed or not self._desired_tracking or self._tracker is None:
                return
            self._tracking_active = True

    def _on_tracking_failed(self, event: AdbDevicesTrackingFailed) -> None:
        if event.endpoint != self.endpoint:
            return
        request_server_reconciliation = False
        with self._lock:
            if self._closed or self._tracker is None:
                return
            tracker = self._detach_tracker_locked()
            if event.failure is AdbDevicesTrackingFailure.SERVER_CONNECTION:
                self._readiness = AdbDevicesTrackingReadiness.WAITING_FOR_SERVER
                request_server_reconciliation = self._desired_tracking
        assert tracker is not None
        tracker.close()
        if request_server_reconciliation:
            self._bus.publish(
                AdbServerReconciliationRequested(
                    self.endpoint,
                    AdbServerFailure(
                        AdbServerFailureKind.CONNECTION,
                        event.diagnostic,
                    ),
                )
            )

    def _on_tracking_stopped(self, event: AdbDevicesTrackingStopped) -> None:
        if event.endpoint != self.endpoint:
            return
        with self._lock:
            if self._closed or self._tracker is None:
                return
            tracker = self._detach_tracker_locked()
        assert tracker is not None
        tracker.close()

    def _on_server_ownership_lost(self, event: AdbServerOwnershipLost) -> None:
        if event.endpoint != self.endpoint:
            return
        with self._lock:
            if self._closed:
                return
            self._readiness = AdbDevicesTrackingReadiness.WAITING_FOR_SERVER
            tracker = self._detach_tracker_locked()
        if tracker is not None:
            tracker.close()

    def _on_server_ownership_recovered(self, event: AdbServerOwnershipRecovered) -> None:
        if event.endpoint != self.endpoint:
            return
        with self._lock:
            if self._closed or not self._desired_tracking:
                return
        self.reconcile(
            AdbServerObservation(
                self.endpoint,
                AdbServerAvailability.AVAILABLE,
            )
        )

    def _run_start_attempt(
        self,
        tracker: AdbDevicesTrackingController,
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
                endpoint=self.endpoint,
                readiness=readiness,
                policy=AdbDevicesTrackingStartPolicy(
                    self._policy.episode_timeout_seconds,
                ),
            )
        )

    def _handle_start_result(
        self,
        tracker: AdbDevicesTrackingController,
        result: AdbDevicesTrackingStartResult,
    ) -> bool:
        request_server_reconciliation = False
        keep_tracker = False
        tracker_to_close: AdbDevicesTrackingController | None = None

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
                    request_server_reconciliation = True
            if not keep_tracker:
                tracker_to_close = self._detach_tracker_locked()

        if tracker_to_close is not None:
            tracker_to_close.close()
        if request_server_reconciliation:
            self._bus.publish(
                AdbServerReconciliationRequested(
                    self.endpoint,
                    AdbServerFailure(
                        AdbServerFailureKind.CONNECTION,
                        result.diagnostic,
                    ),
                )
            )
        return keep_tracker

    def _create_tracker_locked(
        self,
    ) -> tuple[AdbDevicesTrackingController, AdbDevicesTrackingStartOrchestrator]:
        if self._tracker is not None:
            raise RuntimeError("a tracker scope already exists")
        tracker = self._tracker_factory(self.endpoint, self._bus)
        if not isinstance(tracker, AdbDevicesTrackingController):
            raise TypeError("tracker factory must return AdbDevicesTrackingController")
        starter = AdbDevicesTrackingStartOrchestrator(
            self.endpoint,
            self._bus,
            tracker,
        )
        self._tracker = tracker
        self._start = starter
        self._tracking_active = False
        return tracker, starter

    def _detach_tracker_locked(self) -> AdbDevicesTrackingController | None:
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
            self._bus.subscribe(AdbServerOwnershipLost, self._on_server_ownership_lost),
            self._bus.subscribe(
                AdbServerOwnershipRecovered,
                self._on_server_ownership_recovered,
            ),
        )

    def _require_observation_endpoint(self, observation: AdbServerObservation) -> None:
        if not isinstance(observation, AdbServerObservation):
            raise TypeError("server observation must be AdbServerObservation")
        if observation.endpoint != self.endpoint:
            raise ValueError("server observation endpoint does not match tracking endpoint")

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("transport-inventory tracking supervisor is closed")


__all__ = ["AdbDevicesTrackingSupervisor"]
