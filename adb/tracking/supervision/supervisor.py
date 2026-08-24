from __future__ import annotations

from collections.abc import Callable
from threading import Lock, Thread, current_thread

from adb.errors import AdbProtocolError, AdbServerConnectionError, AdbServiceError
from adb.server.failure import AdbServerConnectionFailure
from adb.server.identity import AdbServer, ServerEpoch
from adb.tracking.supervision.policy import AdbDevicesTrackingSupervisionPolicy
from adb.server.signal import (
    AdbServerRetired,
    AdbServerRecovered,
    AdbServerReconciliationRequested,
)
from adb.tracking.state import AdbDevicesSnapshotState
from adb.tracking.publication import (
    AdbDevicesSnapshotStateBackedTrackingPublisher,
)
from adb.tracking.tracker import (
    AdbDevicesTracker,
    SmartSocketAdbDevicesTracker,
)
from adb.tracking.signal import (
    AdbDevicesTrackingFailed,
    AdbDevicesTrackingFailure,
    AdbDevicesTrackingStarted,
    AdbDevicesTrackingStopped,
)
from eventing import EventBus, EventPublisher, EventSubscriptionToken


_ThreadFactory = Callable[..., Thread]
_TrackerFactory = Callable[[AdbServer, EventPublisher], AdbDevicesTracker]


def _default_thread_factory(*args, **kwargs) -> Thread:
    thread = Thread(*args, **kwargs)
    thread.daemon = True
    return thread


class AdbDevicesTrackingSupervisor:
    """Maintain desired track-devices with single-use trackers.

    Terminal tracker or server events discard the current tracker; a fresh server creates a
    new tracker, even when the replacement server uses a different endpoint. Tracker startup is
    a direct capability call: a successful ``start`` return means stream mode was established
    for the current server lifetime.
    """

    def __init__(
        self,
        server: AdbServer,
        event_bus: EventBus,
        policy: AdbDevicesTrackingSupervisionPolicy,
        *,
        snapshot_state: AdbDevicesSnapshotState | None = None,
        _tracker_factory: _TrackerFactory | None = None,
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
        if snapshot_state is None:
            snapshot_state = AdbDevicesSnapshotState()
        if not isinstance(snapshot_state, AdbDevicesSnapshotState):
            raise TypeError("snapshot_state must be AdbDevicesSnapshotState or None")
        if _tracker_factory is not None and not callable(_tracker_factory):
            raise TypeError("_tracker_factory must be callable or None")
        if not callable(_thread_factory):
            raise TypeError("_thread_factory must be callable")

        self.server: AdbServer | None = server
        self._bus = event_bus
        self._devices = snapshot_state
        self._tracking_publisher = AdbDevicesSnapshotStateBackedTrackingPublisher(
            self._devices,
            self._bus,
        )
        self._policy = policy
        self._tracker_factory = _tracker_factory
        self._thread_factory = _thread_factory
        self._lock = Lock()
        self._subscriptions: tuple[EventSubscriptionToken, ...] = ()
        self._desired_tracking = False
        self._tracker: AdbDevicesTracker | None = None
        self._tracking_active = False
        self._server_identity: AdbServer | None = None
        self._latest_server_epoch: ServerEpoch | None = None
        self._start_in_progress = False
        self._attempt_threads: set[Thread] = set()
        self._closed = False

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
        """Declare tracking intent and establish a tracker for the current server."""

        server = self.server
        with self._lock:
            self._require_open()
            if self._desired_tracking:
                raise RuntimeError("track-devices supervisor is already started")
            self._ensure_subscriptions_locked()
            self._desired_tracking = True
            self._server_identity = server
            if server is not None:
                self._latest_server_epoch = server.epoch
            if server is None:
                return False
            tracker = self._create_tracker_locked()
            self._start_in_progress = True

        return self._attempt_start(tracker)

    def reconcile(self, server: AdbServer | None) -> None:
        """Reconcile tracking intent against the current active server."""

        if server is not None and not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer or None")
        tracker_to_close: AdbDevicesTracker | None = None
        launch: tuple[Thread, AdbDevicesTracker] | None = None

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
            self.server = server
            self._server_identity = server_identity
            if epoch is not None and (
                self._latest_server_epoch is None
                or epoch > self._latest_server_epoch
            ):
                self._latest_server_epoch = epoch
            if server is None:
                tracker_to_close = self._detach_tracker_locked()
            elif server_changed and self._tracker is not None:
                tracker_to_close = self._detach_tracker_locked()
            if (
                server is not None
                and self._tracker is None
                and not self._start_in_progress
            ):
                tracker = self._create_tracker_locked()
                thread = self._thread_factory(
                    target=self._run_start_attempt,
                    args=(tracker,),
                    name=(
                        "adb-tracking-reconciliation-"
                        f"{server.endpoint.host}-{server.endpoint.port}-{server.epoch}"
                    ),
                )
                self._start_in_progress = True
                self._attempt_threads.add(thread)
                launch = (thread, tracker)

        if tracker_to_close is not None:
            tracker_to_close.close()
        if launch is not None:
            thread, tracker = launch
            try:
                thread.start()
            except BaseException:
                with self._lock:
                    self._attempt_threads.discard(thread)
                    if self._tracker is tracker:
                        self._detach_tracker_locked()
                tracker.close()
                raise

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._desired_tracking = False
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
        with self._lock:
            tracker = self._tracker
            if (
                self._closed
                or not self._desired_tracking
                or tracker is None
                or event.server != tracker.server
            ):
                return
            self._tracking_active = True

    def _on_tracking_failed(self, event: AdbDevicesTrackingFailed) -> None:
        request_server_reconciliation = False
        server: AdbServer | None = None
        with self._lock:
            current = self._tracker
            if self._closed or current is None or event.server != current.server:
                return
            tracker = self._detach_tracker_locked()
            if event.failure is AdbDevicesTrackingFailure.SERVER_CONNECTION:
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
        with self._lock:
            current = self._tracker
            if self._closed or current is None or event.server != current.server:
                return
            tracker = self._detach_tracker_locked()
        assert tracker is not None
        tracker.close()

    def _on_server_retired(self, event: AdbServerRetired) -> None:
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
            tracker = self._detach_tracker_locked()
        if tracker is not None:
            tracker.close()

    def _on_server_recovered(self, event: AdbServerRecovered) -> None:
        with self._lock:
            if self._closed or not self._desired_tracking:
                return
            if (
                self._latest_server_epoch is not None
                and event.server.epoch <= self._latest_server_epoch
            ):
                return
        self.reconcile(event.server)

    def _run_start_attempt(self, tracker: AdbDevicesTracker) -> None:
        active_thread = current_thread()
        try:
            with self._lock:
                if (
                    self._closed
                    or not self._desired_tracking
                    or self._tracker is not tracker
                ):
                    return
            self._attempt_start(tracker)
        finally:
            with self._lock:
                self._attempt_threads.discard(active_thread)

    def _attempt_start(self, tracker: AdbDevicesTracker) -> bool:
        try:
            tracker.start()
        except AdbServerConnectionError as exc:
            return self._complete_start_attempt(
                tracker,
                started=False,
                failure=AdbDevicesTrackingFailure.SERVER_CONNECTION,
                diagnostic=str(exc),
            )
        except AdbServiceError as exc:
            return self._complete_start_attempt(
                tracker,
                started=False,
                failure=AdbDevicesTrackingFailure.SERVICE,
                diagnostic=str(exc),
            )
        except AdbProtocolError as exc:
            return self._complete_start_attempt(
                tracker,
                started=False,
                failure=AdbDevicesTrackingFailure.PROTOCOL,
                diagnostic=str(exc),
            )
        except RuntimeError:
            return self._complete_start_attempt(tracker, started=False)
        except BaseException:
            tracker_to_close: AdbDevicesTracker | None = None
            with self._lock:
                if self._tracker is tracker:
                    tracker_to_close = self._detach_tracker_locked()
            if tracker_to_close is not None:
                tracker_to_close.close()
            raise
        return self._complete_start_attempt(tracker, started=True)

    def _complete_start_attempt(
        self,
        tracker: AdbDevicesTracker,
        *,
        started: bool,
        failure: AdbDevicesTrackingFailure | None = None,
        diagnostic: str | None = None,
    ) -> bool:
        request_server_reconciliation = False
        reconciliation_server: AdbServer | None = None
        tracker_to_close: AdbDevicesTracker | None = None
        publish_failure = False

        with self._lock:
            if self._tracker is not tracker:
                return False
            self._start_in_progress = False
            keep_tracker = (
                started
                and not self._closed
                and self._desired_tracking
                and self._server_identity == tracker.server
                and tracker.active
            )
            if keep_tracker:
                self._tracking_active = True
            else:
                tracker_to_close = self._detach_tracker_locked()
                publish_failure = failure is not None
                if failure is AdbDevicesTrackingFailure.SERVER_CONNECTION:
                    reconciliation_server = self._server_identity
                    request_server_reconciliation = (
                        self._desired_tracking and reconciliation_server is not None
                    )

        if tracker_to_close is not None:
            tracker_to_close.close()
        if publish_failure:
            assert failure is not None
            self._bus.publish(
                AdbDevicesTrackingFailed(
                    tracker.server,
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
        return keep_tracker

    def _create_tracker_locked(self) -> AdbDevicesTracker:
        if self._tracker is not None:
            raise RuntimeError("a tracker already exists")
        server = self.server
        if server is None:
            raise RuntimeError("cannot create tracker without an active server")
        factory = self._tracker_factory
        tracker = (
            SmartSocketAdbDevicesTracker(
                server,
                self._tracking_publisher,
                startup_timeout_seconds=self._policy.episode_timeout_seconds,
            )
            if factory is None
            else factory(server, self._tracking_publisher)
        )
        if not isinstance(tracker, AdbDevicesTracker):
            raise TypeError("tracker factory must return AdbDevicesTracker")
        if tracker.server != server:
            raise ValueError("tracker factory returned a mismatched server lifetime")
        self._tracker = tracker
        self._tracking_active = False
        return tracker

    def _detach_tracker_locked(self) -> AdbDevicesTracker | None:
        tracker = self._tracker
        if tracker is not None:
            self._tracking_publisher.end_tracking(tracker.server)
        self._tracker = None
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
            raise RuntimeError("track-devices supervisor is closed")


__all__ = ["AdbDevicesTrackingSupervisor"]
