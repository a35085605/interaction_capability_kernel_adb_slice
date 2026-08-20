from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock, Thread, current_thread

from adb.server.endpoint import AdbServerEndpoint
from adb.supervision.model import AdbConfiguredTransportSupervisionPolicy
from adb.supervision.signal import (
    AdbConfiguredTransportRecoveryExhausted,
    AdbConfiguredTransportResolutionChanged,
)
from adb.transport.configuration import AdbConfiguredTransport
from adb.transport.inventory.model import AdbDevicesTrackingSessionId
from adb.transport.inventory.resolution import (
    AdbConfiguredTransportResolution,
    AdbConfiguredTransportResolutionStatus,
    resolve_configured_transport,
)
from adb.transport.lifecycle.ensure import (
    AdbTransportEnsureReadiness,
    AdbTransportEnsureResult,
    AdbTransportEnsureStatus,
    AdbTransportEnsurer,
)
from adb.transport.signal import (
    AdbDevicesSnapshotObserved,
    AdbDevicesTrackingFailed,
    AdbDevicesTrackingStarted,
    AdbDevicesTrackingStopped,
)
from adb.transport.selection import AdbDeviceSerial
from eventing import EventBus, EventSubscriptionToken


_ThreadFactory = Callable[..., Thread]


def _default_thread_factory(*args, **kwargs) -> Thread:
    thread = Thread(*args, **kwargs)
    thread.daemon = True
    return thread


@dataclass(slots=True)
class _ConfiguredTransportRegistration:
    configuration: AdbConfiguredTransport
    policy: AdbConfiguredTransportSupervisionPolicy
    resolution: AdbConfiguredTransportResolution | None = None
    session_id: AdbDevicesTrackingSessionId | None = None
    disappearance_recovery_pending: bool = False
    active_recovery_thread: Thread | None = None
    active_recovery_token: object | None = None


class AdbConfiguredTransportSupervisor:
    """Long-lived projection and disappearance recovery for configured transports.

    The transport-inventory tracker remains server-wide and configuration-agnostic. Tracking
    observations are this supervisor's sole projection authority: the latest complete observation
    is cached atomically with its session identity and reused when a transport is registered.
    Fresh readiness probing remains the configured ensurer's responsibility.

    The supervisor holds each configured transport only for its explicit registration lifetime
    and may run one bounded readiness ensure operation after a ``RESOLVED`` to ``ABSENT``
    transition within one tracking generation. Initial absence, a tracking-generation boundary,
    and transitions from non-resolved evidence never imply recovery intent. Registrations may
    remain tracking-only; a policy requesting disappearance recovery is accepted only when the
    configured ensurer explicitly supports active establishment for that exact transport.
    """

    def __init__(
        self,
        endpoint: AdbServerEndpoint,
        event_bus: EventBus,
        ensurer: AdbTransportEnsurer,
        *,
        _thread_factory: _ThreadFactory = _default_thread_factory,
    ) -> None:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        if not callable(getattr(event_bus, "publish", None)) or not callable(
            getattr(event_bus, "subscribe", None)
        ) or not callable(getattr(event_bus, "unsubscribe", None)):
            raise TypeError("event_bus must satisfy EventBus")
        if not isinstance(ensurer, AdbTransportEnsurer):
            raise TypeError("ensurer must satisfy AdbTransportEnsurer")
        self.endpoint = endpoint
        self._bus = event_bus
        self._ensurer = ensurer
        self._thread_factory = _thread_factory
        self._lock = Lock()
        self._registrations: dict[
            AdbConfiguredTransport,
            _ConfiguredTransportRegistration,
        ] = {}
        self._subscriptions: tuple[EventSubscriptionToken, ...] = ()
        self._current_session_id: AdbDevicesTrackingSessionId | None = None
        self._latest_observation: AdbDevicesSnapshotObserved | None = None
        self._latest_generation = 0
        self._closed = False

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("configured transport supervisor is closed")
            if self._subscriptions:
                raise RuntimeError("configured transport supervisor is already started")
            started = self._bus.subscribe(
                AdbDevicesTrackingStarted,
                self._on_tracking_started,
            )
            snapshots = self._bus.subscribe(
                AdbDevicesSnapshotObserved,
                self._on_snapshot_observed,
            )
            failed = self._bus.subscribe(
                AdbDevicesTrackingFailed,
                self._on_tracking_terminal,
            )
            stopped = self._bus.subscribe(
                AdbDevicesTrackingStopped,
                self._on_tracking_terminal,
            )
            self._subscriptions = (started, snapshots, failed, stopped)

    def register(
        self,
        configuration: AdbConfiguredTransport,
        policy: AdbConfiguredTransportSupervisionPolicy | None = None,
    ) -> None:
        if not isinstance(configuration, AdbConfiguredTransport):
            raise TypeError("configuration must be AdbConfiguredTransport")
        if configuration.endpoint != self.endpoint:
            raise ValueError("configured transport endpoint does not match ADB server endpoint")
        if policy is None:
            policy = AdbConfiguredTransportSupervisionPolicy()
        if not isinstance(policy, AdbConfiguredTransportSupervisionPolicy):
            raise TypeError("policy must be AdbConfiguredTransportSupervisionPolicy")
        if policy.recovery_ensure_policy is not None:
            establishment_supported = (
                self._ensurer.supports_establishment(configuration)
            )
            if not isinstance(establishment_supported, bool):
                raise TypeError(
                    "ensurer supports_establishment() must return bool"
                )
            if not establishment_supported:
                raise ValueError(
                    "automatic disappearance recovery is not supported for this "
                    "configured transport"
                )

        publication: AdbConfiguredTransportResolutionChanged | None = None
        recovery_launch_requested = False
        with self._lock:
            if self._closed:
                raise RuntimeError("configured transport supervisor is closed")
            if not self._subscriptions:
                raise RuntimeError("configured transport supervisor must be started before register")
            if configuration in self._registrations:
                raise ValueError("ADB configured transport is already registered")
            if any(
                candidate.serial == configuration.serial
                and candidate.expected_connection_type
                is configuration.expected_connection_type
                for candidate in self._registrations
            ):
                raise ValueError(
                    "ADB configured transport serial and connection type are already registered"
                )
            registration = _ConfiguredTransportRegistration(configuration, policy)
            self._registrations[configuration] = registration
            observation = self._latest_observation
            if observation is not None:
                publication, recovery_launch_requested = (
                    self._project_registration_locked(registration, observation)
                )

        if publication is not None:
            self._bus.publish(publication)
        if recovery_launch_requested:
            self._launch_recovery(configuration)

    def unregister(
        self,
        configuration: AdbConfiguredTransport | AdbDeviceSerial,
    ) -> bool:
        """Release one exact registration, or the sole registration for a serial."""

        if not isinstance(configuration, (AdbConfiguredTransport, AdbDeviceSerial)):
            raise TypeError(
                "configuration must be AdbConfiguredTransport or AdbDeviceSerial"
            )
        with self._lock:
            key = self._resolve_registration_key_locked(configuration)
            registration = None if key is None else self._registrations.pop(key)
            thread = (
                None if registration is None else registration.active_recovery_thread
            )
        if thread is not None and thread is not current_thread():
            thread.join()
        return registration is not None

    def resolution(
        self,
        configuration: AdbConfiguredTransport | AdbDeviceSerial,
    ) -> AdbConfiguredTransportResolution | None:
        """Return one exact projection, or the sole projection for a serial."""

        if not isinstance(configuration, (AdbConfiguredTransport, AdbDeviceSerial)):
            raise TypeError(
                "configuration must be AdbConfiguredTransport or AdbDeviceSerial"
            )
        with self._lock:
            key = self._resolve_registration_key_locked(configuration)
            registration = None if key is None else self._registrations.get(key)
            return None if registration is None else registration.resolution

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            subscriptions = self._subscriptions
            self._subscriptions = ()
            self._current_session_id = None
            self._latest_observation = None
            threads = tuple(
                registration.active_recovery_thread
                for registration in self._registrations.values()
                if registration.active_recovery_thread is not None
            )
            self._registrations.clear()
        for token in subscriptions:
            self._bus.unsubscribe(token)
        for thread in threads:
            if thread is not current_thread():
                thread.join()

    def _on_tracking_started(self, event: AdbDevicesTrackingStarted) -> None:
        if event.session_id.endpoint != self.endpoint:
            return
        with self._lock:
            if self._closed:
                return
            if event.session_id == self._current_session_id:
                return
            if event.session_id.generation <= self._latest_generation:
                return
            self._current_session_id = event.session_id
            self._latest_observation = None
            self._latest_generation = event.session_id.generation
            for registration in self._registrations.values():
                registration.resolution = None
                registration.session_id = event.session_id
                registration.disappearance_recovery_pending = False
                registration.active_recovery_token = None

    def _on_snapshot_observed(self, event: AdbDevicesSnapshotObserved) -> None:
        if event.session_id.endpoint != self.endpoint:
            return
        publications: list[object] = []
        recovery_launch_requests: list[AdbConfiguredTransport] = []
        with self._lock:
            if self._closed:
                return
            if self._current_session_id != event.session_id:
                if event.session_id.generation <= self._latest_generation:
                    return
                # Tolerate a late subscription that missed Started while still fencing out
                # observations from generations already known to be terminal.
                self._current_session_id = event.session_id
                self._latest_generation = event.session_id.generation
            self._latest_observation = event
            for registration in self._registrations.values():
                publication, recovery_launch_requested = (
                    self._project_registration_locked(registration, event)
                )
                if publication is not None:
                    publications.append(publication)
                if recovery_launch_requested:
                    recovery_launch_requests.append(registration.configuration)

        for publication in publications:
            self._bus.publish(publication)
        for recovery_configuration in recovery_launch_requests:
            self._launch_recovery(recovery_configuration)

    def _on_tracking_terminal(
        self,
        event: AdbDevicesTrackingFailed | AdbDevicesTrackingStopped,
    ) -> None:
        if event.session_id.endpoint != self.endpoint:
            return
        with self._lock:
            if self._closed or self._current_session_id != event.session_id:
                return
            # Existing projections remain last-known evidence, but must not bootstrap a new
            # registration after their tracking generation has terminated.
            self._current_session_id = None
            self._latest_observation = None
            for registration in self._registrations.values():
                if registration.session_id == event.session_id:
                    registration.disappearance_recovery_pending = False
                    registration.active_recovery_token = None

    def _project_registration_locked(
        self,
        registration: _ConfiguredTransportRegistration,
        observation: AdbDevicesSnapshotObserved,
    ) -> tuple[AdbConfiguredTransportResolutionChanged | None, bool]:
        session_id = observation.session_id
        previous = registration.resolution
        current = resolve_configured_transport(
            registration.configuration,
            observation.snapshot,
        )
        baseline_changed = registration.session_id != session_id
        effective_previous = None if baseline_changed else previous
        changed = baseline_changed or previous != current
        registration.session_id = session_id
        registration.resolution = current

        if (
            baseline_changed
            or current.status is not AdbConfiguredTransportResolutionStatus.ABSENT
        ):
            registration.disappearance_recovery_pending = False
            registration.active_recovery_token = None

        publication = (
            AdbConfiguredTransportResolutionChanged(
                session_id,
                effective_previous,
                current,
            )
            if changed
            else None
        )
        recoverable_disappearance = (
            effective_previous is not None
            and effective_previous.status
            is AdbConfiguredTransportResolutionStatus.RESOLVED
            and current.status is AdbConfiguredTransportResolutionStatus.ABSENT
        )
        recovery_launch_requested = (
            recoverable_disappearance
            and registration.policy.recovery_ensure_policy is not None
        )
        if recovery_launch_requested:
            # Recovery intent is created only from this eligible edge. If an invalidated older
            # recovery thread is still unwinding, its finally block may consume the explicit
            # pending request; it must never reconstruct intent from ABSENT.
            registration.disappearance_recovery_pending = True
            recovery_launch_requested = registration.active_recovery_thread is None
        return publication, recovery_launch_requested

    def _launch_recovery(self, configuration: AdbConfiguredTransport) -> None:
        with self._lock:
            registration = self._registrations.get(configuration)
            if registration is None or self._closed:
                return
            if registration.active_recovery_thread is not None:
                return
            if not registration.disappearance_recovery_pending:
                return
            recovery_token = object()
            thread = self._thread_factory(
                target=self._run_recovery,
                args=(configuration, recovery_token),
                name=(
                    "adb-transport-recovery-"
                    f"{configuration.expected_connection_type.name.lower()}-"
                    f"{configuration.serial.value}"
                ),
            )
            registration.disappearance_recovery_pending = False
            registration.active_recovery_token = recovery_token
            registration.active_recovery_thread = thread
            try:
                # Publish and start under the same lock observed by close()/unregister(). This
                # prevents either method from trying to join a thread that has not started yet.
                thread.start()
            except BaseException:
                if registration.active_recovery_thread is thread:
                    registration.active_recovery_thread = None
                if registration.active_recovery_token is recovery_token:
                    registration.active_recovery_token = None
                raise

    def _run_recovery(
        self,
        configuration: AdbConfiguredTransport,
        recovery_token: object,
    ) -> None:
        try:
            with self._lock:
                registration = self._registrations.get(configuration)
                if (
                    registration is None
                    or self._closed
                    or registration.active_recovery_token is not recovery_token
                ):
                    return
                ensure_policy = registration.policy.recovery_ensure_policy
            assert ensure_policy is not None
            result = self._ensurer.ensure(
                AdbTransportEnsureReadiness(configuration, ensure_policy),
            )
            if not isinstance(result, AdbTransportEnsureResult):
                raise TypeError("ensurer must return AdbTransportEnsureResult")
            if result.operation.configuration != configuration:
                raise ValueError(
                    "ensure result configuration does not match supervised transport"
            )
            with self._lock:
                registration = self._registrations.get(configuration)
                result_is_current = (
                    registration is not None
                    and not self._closed
                    and registration.active_recovery_token is recovery_token
                )
            if not result_is_current:
                return
            if result.status is not AdbTransportEnsureStatus.SATISFIED:
                self._bus.publish(
                    AdbConfiguredTransportRecoveryExhausted(configuration, result)
                )
            # A satisfied readiness probe is deliberately not projected here. The long-lived
            # tracking stream remains authoritative and will publish the resulting inventory.
        finally:
            launch_pending_recovery = False
            with self._lock:
                registration = self._registrations.get(configuration)
                if (
                    registration is not None
                    and registration.active_recovery_thread is current_thread()
                ):
                    registration.active_recovery_thread = None
                    if registration.active_recovery_token is recovery_token:
                        registration.active_recovery_token = None
                    launch_pending_recovery = (
                        not self._closed
                        and registration.disappearance_recovery_pending
                    )
            if launch_pending_recovery:
                self._launch_recovery(configuration)

    def _resolve_registration_key_locked(
        self,
        configuration: AdbConfiguredTransport | AdbDeviceSerial,
    ) -> AdbConfiguredTransport | None:
        if isinstance(configuration, AdbConfiguredTransport):
            return configuration if configuration in self._registrations else None
        matches = tuple(
            candidate
            for candidate in self._registrations
            if candidate.serial == configuration
        )
        if len(matches) > 1:
            raise ValueError(
                "ADB device serial matches multiple configured transports; "
                "use AdbConfiguredTransport"
            )
        return matches[0] if matches else None


__all__ = ["AdbConfiguredTransportSupervisor"]
