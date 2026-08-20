from __future__ import annotations

from collections.abc import Callable
from threading import Lock, Thread, current_thread

from adb.server.endpoint import AdbServerEndpoint
from adb.server.lifecycle import AdbServerAvailability
from adb.supervision.model import AdbDevicesTrackingSupervisionPolicy
from adb.supervision.signal import AdbServerReconciliationRequested
from adb.transport.inventory.model import AdbDevicesTrackingSessionId
from adb.transport.inventory.start import (
    AdbDevicesTrackingStart,
    AdbDevicesTrackingStartOrchestrator,
    AdbDevicesTrackingStartPolicy,
    AdbDevicesTrackingReadiness,
    AdbDevicesTrackingStartResult,
    AdbDevicesTrackingStartStatus,
)
from adb.transport.inventory.tracker import AdbDevicesTrackingController
from adb.transport.signal import (
    AdbDevicesTrackingFailed,
    AdbDevicesTrackingFailure,
    AdbDevicesTrackingStarted,
    AdbDevicesTrackingStopped,
)
from eventing import EventBus, EventSubscriptionToken


_ThreadFactory = Callable[..., Thread]


def _default_thread_factory(*args, **kwargs) -> Thread:
    thread = Thread(*args, **kwargs)
    thread.daemon = True
    return thread


def _project_server_availability(
    availability: AdbServerAvailability,
) -> AdbDevicesTrackingReadiness:
    """Translate fresh server evidence into tracking-local readiness."""

    if not isinstance(availability, AdbServerAvailability):
        raise TypeError("availability must be AdbServerAvailability")
    if availability is AdbServerAvailability.AVAILABLE:
        return AdbDevicesTrackingReadiness.READY
    if availability is AdbServerAvailability.UNAVAILABLE:
        return AdbDevicesTrackingReadiness.WAITING_FOR_SERVER
    return AdbDevicesTrackingReadiness.INDETERMINATE


class AdbDevicesTrackingSupervisor:
    """Maintain durable intent to observe one configured server's transport inventory.

    Fresh ``AdbServerAvailability`` evidence is projected into tracking-start readiness.
    A generation may be started only while readiness is ``READY``; waiting for server recovery
    carries no tracking-start deadline. The supervisor deliberately owns no retry/
    backoff policy.

    Matching ``AdbDevicesTrackingStarted`` evidence is the linearization point that makes one
    start generation current. The supervisor subscribes to that evidence for the entire
    supervised lifetime, so an immediately following ``Failed`` or ``Stopped`` signal can clear the
    same generation even before the bounded start call returns to its caller. A completed
    start result never resurrects a generation that already became terminal.

    A ``SERVER_CONNECTION`` failure ends the current generation, moves readiness to
    ``WAITING_FOR_SERVER``, and publishes ``AdbServerReconciliationRequested`` so upstream server
    supervision can decide whether recovery is required. Service and protocol failures remain
    terminal tracking-local failures until a caller explicitly reconciles from fresh server
    availability evidence.
    """

    def __init__(
        self,
        endpoint: AdbServerEndpoint,
        event_bus: EventBus,
        tracker: AdbDevicesTrackingController,
        policy: AdbDevicesTrackingSupervisionPolicy,
        *,
        _thread_factory: _ThreadFactory = _default_thread_factory,
    ) -> None:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        if not callable(getattr(event_bus, "publish", None)) or not callable(
            getattr(event_bus, "subscribe", None)
        ) or not callable(getattr(event_bus, "unsubscribe", None)):
            raise TypeError("event_bus must satisfy EventBus")
        if not isinstance(tracker, AdbDevicesTrackingController):
            raise TypeError("tracker must satisfy AdbDevicesTrackingController")
        if not isinstance(policy, AdbDevicesTrackingSupervisionPolicy):
            raise TypeError(
                "policy must be AdbDevicesTrackingSupervisionPolicy"
            )
        self.endpoint = endpoint
        self._bus = event_bus
        self._tracker = tracker
        self._start = AdbDevicesTrackingStartOrchestrator(
            endpoint,
            event_bus,
            tracker,
        )
        self._policy = policy
        self._thread_factory = _thread_factory
        self._lock = Lock()
        self._subscriptions: tuple[EventSubscriptionToken, ...] = ()
        self._desired_tracking = False
        self._readiness = AdbDevicesTrackingReadiness.INDETERMINATE
        # Only matching Started evidence received during supervision may make a session current.
        # Capturing controller state here can retain a session that terminates before start()
        # installs the terminal-event subscriptions.
        self._current_session_id: AdbDevicesTrackingSessionId | None = None
        self._start_in_progress = False
        self._attempt_thread: Thread | None = None
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
    def current_session_id(self) -> AdbDevicesTrackingSessionId | None:
        with self._lock:
            return self._current_session_id

    def start(
        self,
        server_availability: AdbServerAvailability,
    ) -> AdbDevicesTrackingSessionId | None:
        """Declare durable intent and start only from fresh AVAILABLE server evidence."""

        readiness = _project_server_availability(server_availability)
        with self._lock:
            self._require_open()
            if self._desired_tracking:
                raise RuntimeError("transport-inventory tracking supervisor is already started")
            started = self._bus.subscribe(
                AdbDevicesTrackingStarted,
                self._on_tracking_started,
            )
            failed = self._bus.subscribe(
                AdbDevicesTrackingFailed,
                self._on_tracking_failed,
            )
            stopped = self._bus.subscribe(
                AdbDevicesTrackingStopped,
                self._on_tracking_stopped,
            )
            self._subscriptions = (started, failed, stopped)
            self._desired_tracking = True
            self._readiness = readiness
            if readiness is not AdbDevicesTrackingReadiness.READY:
                return None
            self._start_in_progress = True

        try:
            result = self._start_once(readiness)
        except BaseException:
            with self._lock:
                self._start_in_progress = False
            raise
        return self._handle_start_result(result)

    def reconcile(self, server_availability: AdbServerAvailability) -> None:
        """Project fresh server evidence and start once only when readiness becomes READY."""

        readiness = _project_server_availability(server_availability)
        with self._lock:
            self._require_open()
            if not self._desired_tracking:
                return
            self._readiness = readiness
            if readiness is not AdbDevicesTrackingReadiness.READY:
                return
            if self._current_session_id is not None:
                return
            if self._start_in_progress:
                return
            if self._tracker.active_session_id is not None:
                return
            thread = self._thread_factory(
                target=self._run_start_attempt,
                args=(readiness,),
                name=(
                    "adb-tracking-reconciliation-"
                    f"{self.endpoint.host}-{self.endpoint.port}"
                ),
            )
            self._attempt_thread = thread
            self._start_in_progress = True
            try:
                # Publish and start the attempt while holding the same lock observed by close().
                # This prevents close() from trying to join a thread that has not started yet.
                thread.start()
            except BaseException:
                if self._attempt_thread is thread:
                    self._attempt_thread = None
                    self._start_in_progress = False
                raise

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._desired_tracking = False
            self._readiness = AdbDevicesTrackingReadiness.INDETERMINATE
            self._current_session_id = None
            subscriptions = self._subscriptions
            self._subscriptions = ()
            attempt_thread = self._attempt_thread
            self._attempt_thread = None
            self._start_in_progress = False
        for token in subscriptions:
            self._bus.unsubscribe(token)
        self._tracker.close()
        if attempt_thread is not None and attempt_thread is not current_thread():
            attempt_thread.join()

    def _on_tracking_started(self, event: AdbDevicesTrackingStarted) -> None:
        if event.session_id.endpoint != self.endpoint:
            return
        with self._lock:
            if self._closed or not self._desired_tracking:
                return
            if not self._start_in_progress:
                return
            if self._readiness is not AdbDevicesTrackingReadiness.READY:
                return
            if self._current_session_id is not None:
                return
            if self._tracker.active_session_id != event.session_id:
                return
            self._current_session_id = event.session_id

    def _on_tracking_failed(self, event: AdbDevicesTrackingFailed) -> None:
        if event.session_id.endpoint != self.endpoint:
            return

        request_server_reconciliation = False
        with self._lock:
            if self._closed or event.session_id != self._current_session_id:
                return
            self._current_session_id = None
            if event.failure is AdbDevicesTrackingFailure.SERVER_CONNECTION:
                self._readiness = (
                    AdbDevicesTrackingReadiness.WAITING_FOR_SERVER
                )
                request_server_reconciliation = self._desired_tracking

        if request_server_reconciliation:
            self._bus.publish(AdbServerReconciliationRequested(self.endpoint))

    def _on_tracking_stopped(self, event: AdbDevicesTrackingStopped) -> None:
        if event.session_id.endpoint != self.endpoint:
            return
        with self._lock:
            if self._closed or event.session_id != self._current_session_id:
                return
            self._current_session_id = None

    def _run_start_attempt(
        self,
        readiness: AdbDevicesTrackingReadiness,
    ) -> None:
        active = current_thread()
        with self._lock:
            attempt_is_current = self._attempt_thread is active
            start_is_allowed = (
                attempt_is_current
                and not self._closed
                and self._desired_tracking
                and readiness is AdbDevicesTrackingReadiness.READY
                and self._readiness is readiness
            )
            if not start_is_allowed:
                if attempt_is_current:
                    self._attempt_thread = None
                    self._start_in_progress = False
                return

        try:
            result = self._start_once(readiness)
        except BaseException:
            with self._lock:
                if self._attempt_thread is active:
                    self._attempt_thread = None
                    self._start_in_progress = False
            raise
        self._handle_start_result(result, active_thread=active)

    def _start_once(
        self,
        readiness: AdbDevicesTrackingReadiness,
    ) -> AdbDevicesTrackingStartResult:
        return self._start.start(
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
        result: AdbDevicesTrackingStartResult,
        *,
        active_thread: Thread | None = None,
    ) -> AdbDevicesTrackingSessionId | None:
        session_id = result.tracking_session_id
        satisfied = result.status is AdbDevicesTrackingStartStatus.SATISFIED
        request_server_reconciliation = False
        keep_session = False

        with self._lock:
            if active_thread is not None and self._attempt_thread is active_thread:
                self._attempt_thread = None
            self._start_in_progress = False
            if not self._closed and self._desired_tracking:
                if satisfied:
                    assert session_id is not None
                    keep_session = (
                        self._readiness
                        is AdbDevicesTrackingReadiness.READY
                        and self._current_session_id == session_id
                        and self._tracker.active_session_id == session_id
                    )
                elif result.tracking_failure is AdbDevicesTrackingFailure.SERVER_CONNECTION:
                    self._readiness = (
                        AdbDevicesTrackingReadiness.WAITING_FOR_SERVER
                    )
                    request_server_reconciliation = True

        if satisfied:
            # Started evidence already owns current-session commitment. Never write the session here:
            # it may have emitted a terminal signal after Started but before start() returned.
            if keep_session:
                return session_id
            if session_id is not None and self._tracker.active_session_id == session_id:
                self._tracker.stop()
            return None

        # A bounded start timeout may leave the generation alive even though no matching
        # Started evidence arrived before the deadline. Do not let such an unowned generation
        # block a future reconciliation after fresh server readiness evidence.
        if session_id is not None and self._tracker.active_session_id == session_id:
            self._tracker.stop()

        if request_server_reconciliation:
            self._bus.publish(AdbServerReconciliationRequested(self.endpoint))
        return None

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("transport-inventory tracking supervisor is closed")


__all__ = ["AdbDevicesTrackingSupervisor"]
