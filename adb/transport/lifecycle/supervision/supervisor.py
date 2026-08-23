from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock, Thread, current_thread

from adb.server.identity import AdbServer
from adb.transport.lifecycle.supervision.policy import AdbConfiguredTransportSupervisionPolicy
from adb.transport.lifecycle.supervision.signal import (
    AdbConfiguredTransportRecoveryExhausted,
    AdbConfiguredTransportResolutionChanged,
)
from adb.transport.configuration import AdbConfiguredTransport
from adb.transport.inventory.tracking.identity import AdbDevicesTrackingScopeIdentity
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
from adb.transport.inventory.tracking.signal import (
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


def _require_bool(value: object, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be bool")
    return value


@dataclass(slots=True)
class _ConfiguredTransportRegistration:
    configuration: AdbConfiguredTransport
    policy: AdbConfiguredTransportSupervisionPolicy
    recovery_enabled: bool
    resolution: AdbConfiguredTransportResolution | None = None
    recovery_pending: bool = False
    active_recovery_thread: Thread | None = None
    active_recovery_token: object | None = None


class AdbConfiguredTransportSupervisor:
    """Project runtime-scoped registrations across server and tracker lifetimes.

    Registrations are long-lived and survive replacement of the current ``AdbServer``.
    Each new server/tracker scope starts a fresh observation baseline; prior resolutions
    never create disappearance events in the replacement scope.
    """

    def __init__(
        self,
        server: AdbServer,
        event_bus: EventBus,
        ensurer: AdbTransportEnsurer,
        *,
        _thread_factory: _ThreadFactory = _default_thread_factory,
    ) -> None:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        if not callable(getattr(event_bus, "publish", None)) or not callable(
            getattr(event_bus, "subscribe", None)
        ) or not callable(getattr(event_bus, "unsubscribe", None)):
            raise TypeError("event_bus must satisfy EventBus")
        if not isinstance(ensurer, AdbTransportEnsurer):
            raise TypeError("ensurer must satisfy AdbTransportEnsurer")
        self.server: AdbServer | None = server
        self.endpoint = server.endpoint
        self._bus = event_bus
        self._ensurer = ensurer
        self._thread_factory = _thread_factory
        self._lock = Lock()
        self._registrations: dict[
            AdbConfiguredTransport,
            _ConfiguredTransportRegistration,
        ] = {}
        self._subscriptions: tuple[EventSubscriptionToken, ...] = ()
        self._tracking_active = False
        self._tracking_scope: AdbDevicesTrackingScopeIdentity | None = None
        self._latest_tracking_generation: int | None = None
        self._latest_observation: AdbDevicesSnapshotObserved | None = None
        self._latest_server_epoch = server.epoch
        self._recovery_threads: set[Thread] = set()
        self._closed = False

        # Per-registration tokens fence late recovery results without coupling independent ensures.

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("configured transport supervisor is closed")
            if self._subscriptions:
                raise RuntimeError("configured transport supervisor is already started")
            self._subscriptions = (
                self._bus.subscribe(AdbDevicesTrackingStarted, self._on_tracking_started),
                self._bus.subscribe(AdbDevicesSnapshotObserved, self._on_snapshot_observed),
                self._bus.subscribe(AdbDevicesTrackingFailed, self._on_tracking_terminal),
                self._bus.subscribe(AdbDevicesTrackingStopped, self._on_tracking_terminal),
            )

    def reconcile(self, server: AdbServer | None) -> None:
        """Rebind long-lived registrations to the current server lifetime.

        Server replacement clears tracker-local resolution and invalidates recovery work
        started for the previous lifetime, while preserving the registration set itself.
        Older server epochs are ignored.
        """

        if server is not None and not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer or None")
        with self._lock:
            if self._closed:
                raise RuntimeError("configured transport supervisor is closed")
            if server is not None and server.endpoint != self.endpoint:
                raise ValueError(
                    "recovered server endpoint does not match configured transport endpoint"
                )
            if server is not None and server.epoch < self._latest_server_epoch:
                return
            if (
                server is not None
                and self.server is None
                and server.epoch == self._latest_server_epoch
            ):
                # Once a lifetime has been retired to ``None``, only a newer epoch may
                # become current; a late recovery signal must not resurrect the old one.
                return
            if server == self.server:
                return

            self.server = server
            if server is not None and server.epoch > self._latest_server_epoch:
                self._latest_server_epoch = server.epoch
            self._reset_scope_locked()

    def register(
        self,
        configuration: AdbConfiguredTransport,
        policy: AdbConfiguredTransportSupervisionPolicy | None = None,
        *,
        recovery_enabled: bool | None = None,
    ) -> None:
        if not isinstance(configuration, AdbConfiguredTransport):
            raise TypeError("configuration must be AdbConfiguredTransport")
        if policy is None:
            policy = AdbConfiguredTransportSupervisionPolicy()
        if not isinstance(policy, AdbConfiguredTransportSupervisionPolicy):
            raise TypeError("policy must be AdbConfiguredTransportSupervisionPolicy")
        enabled = (
            policy.recovery_ensure_policy is not None
            if recovery_enabled is None
            else _require_bool(recovery_enabled, field_name="recovery_enabled")
        )
        if enabled and policy.recovery_ensure_policy is None:
            raise ValueError("automatic recovery requires a recovery ensure policy")
        if policy.recovery_ensure_policy is not None:
            establishment_supported = self._ensurer.supports_establishment(configuration)
            if not isinstance(establishment_supported, bool):
                raise TypeError("ensurer supports_establishment() must return bool")
            if not establishment_supported:
                raise ValueError(
                    "automatic recovery after disappearance is not supported for this configured transport"
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
                and candidate.expected_connection_type is configuration.expected_connection_type
                for candidate in self._registrations
            ):
                raise ValueError(
                    "ADB configured transport serial and connection type are already registered"
                )
            registration = _ConfiguredTransportRegistration(configuration, policy, enabled)
            self._registrations[configuration] = registration
            observation = self._latest_observation if self._tracking_active else None
            if observation is not None:
                publication, recovery_launch_requested = self._project_registration_locked(
                    registration,
                    observation,
                )

        if publication is not None:
            self._bus.publish(publication)
        if recovery_launch_requested:
            self._launch_recovery(configuration)

    def set_recovery_enabled(
        self,
        configuration: AdbConfiguredTransport | AdbDeviceSerial,
        enabled: bool,
    ) -> None:
        """Mutate recovery intent without changing the long-lived registration itself."""

        if not isinstance(configuration, (AdbConfiguredTransport, AdbDeviceSerial)):
            raise TypeError("configuration must be AdbConfiguredTransport or AdbDeviceSerial")
        normalized = _require_bool(enabled, field_name="enabled")
        with self._lock:
            if self._closed:
                raise RuntimeError("configured transport supervisor is closed")
            key = self._resolve_registration_key_locked(configuration)
            if key is None:
                raise ValueError("ADB configured transport is not registered")
            registration = self._registrations[key]
            if registration.recovery_enabled is normalized:
                return
            if normalized and registration.policy.recovery_ensure_policy is None:
                raise ValueError("automatic recovery requires a recovery ensure policy")
            registration.recovery_enabled = normalized
            if not normalized:
                registration.recovery_pending = False
                registration.active_recovery_token = None
                # Any active attempt is fenced but allowed to finish against its captured
                # server lifetime; disabling recovery must not block a later re-enable.
                registration.active_recovery_thread = None

    def unregister(
        self,
        configuration: AdbConfiguredTransport | AdbDeviceSerial,
    ) -> bool:
        if not isinstance(configuration, (AdbConfiguredTransport, AdbDeviceSerial)):
            raise TypeError("configuration must be AdbConfiguredTransport or AdbDeviceSerial")
        with self._lock:
            key = self._resolve_registration_key_locked(configuration)
            registration = None if key is None else self._registrations.pop(key)
            thread = None if registration is None else registration.active_recovery_thread
        if thread is not None and thread is not current_thread():
            thread.join()
        return registration is not None

    def resolution(
        self,
        configuration: AdbConfiguredTransport | AdbDeviceSerial,
    ) -> AdbConfiguredTransportResolution | None:
        if not isinstance(configuration, (AdbConfiguredTransport, AdbDeviceSerial)):
            raise TypeError("configuration must be AdbConfiguredTransport or AdbDeviceSerial")
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
            self._tracking_active = False
            self._tracking_scope = None
            self._latest_observation = None
            threads = tuple(self._recovery_threads)
            self._recovery_threads.clear()
            self._registrations.clear()
        for token in subscriptions:
            self._bus.unsubscribe(token)
        for thread in threads:
            if thread is not current_thread():
                thread.join()

    def _on_tracking_started(self, event: AdbDevicesTrackingStarted) -> None:
        with self._lock:
            if self._closed or event.server != self.server:
                return
            if (
                self._latest_tracking_generation is not None
                and event.generation <= self._latest_tracking_generation
            ):
                return
            self._latest_tracking_generation = event.generation
            self._reset_scope_locked()
            self._tracking_active = True
            self._tracking_scope = event.scope

    def _on_snapshot_observed(self, event: AdbDevicesSnapshotObserved) -> None:
        publications: list[object] = []
        recovery_launch_requests: list[AdbConfiguredTransport] = []
        with self._lock:
            if (
                self._closed
                or event.server != self.server
                or not self._tracking_active
                or event.scope != self._tracking_scope
            ):
                return
            self._latest_observation = event
            for registration in self._registrations.values():
                publication, recovery_launch_requested = self._project_registration_locked(
                    registration,
                    event,
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
        with self._lock:
            if (
                self._closed
                or event.server != self.server
                or not self._tracking_active
                or event.scope != self._tracking_scope
            ):
                return
            self._reset_scope_locked()

    def _project_registration_locked(
        self,
        registration: _ConfiguredTransportRegistration,
        observation: AdbDevicesSnapshotObserved,
    ) -> tuple[AdbConfiguredTransportResolutionChanged | None, bool]:
        previous = registration.resolution
        current = resolve_configured_transport(
            registration.configuration,
            observation.snapshot,
        )
        changed = previous != current
        registration.resolution = current

        if current.status is not AdbConfiguredTransportResolutionStatus.ABSENT:
            registration.recovery_pending = False
            registration.active_recovery_token = None

        publication = (
            AdbConfiguredTransportResolutionChanged(previous, current)
            if changed
            else None
        )
        recoverable_disappearance = (
            previous is not None
            and previous.status is AdbConfiguredTransportResolutionStatus.RESOLVED
            and current.status is AdbConfiguredTransportResolutionStatus.ABSENT
        )
        recovery_launch_requested = (
            recoverable_disappearance
            and registration.recovery_enabled
            and registration.policy.recovery_ensure_policy is not None
        )
        if recovery_launch_requested:
            registration.recovery_pending = True
            recovery_launch_requested = registration.active_recovery_thread is None
        return publication, recovery_launch_requested

    def _launch_recovery(self, configuration: AdbConfiguredTransport) -> None:
        with self._lock:
            registration = self._registrations.get(configuration)
            server = self.server
            if registration is None or self._closed or server is None:
                return
            if registration.active_recovery_thread is not None:
                return
            if not registration.recovery_pending:
                return
            recovery_token = object()
            thread = self._thread_factory(
                target=self._run_recovery,
                args=(configuration, server, recovery_token),
                name=(
                    "adb-transport-recovery-"
                    f"{configuration.expected_connection_type.name.lower()}-"
                    f"{configuration.serial.value}"
                ),
            )
            registration.recovery_pending = False
            registration.active_recovery_token = recovery_token
            registration.active_recovery_thread = thread
            self._recovery_threads.add(thread)
            try:
                thread.start()
            except BaseException:
                self._recovery_threads.discard(thread)
                if registration.active_recovery_thread is thread:
                    registration.active_recovery_thread = None
                if registration.active_recovery_token is recovery_token:
                    registration.active_recovery_token = None
                raise

    def _run_recovery(
        self,
        configuration: AdbConfiguredTransport,
        server: AdbServer,
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
                AdbTransportEnsureReadiness(server, configuration, ensure_policy),
            )
            if not isinstance(result, AdbTransportEnsureResult):
                raise TypeError("ensurer must return AdbTransportEnsureResult")
            if result.operation.configuration != configuration:
                raise ValueError(
                    "ensure result configuration does not match supervised transport"
                )
            if result.operation.server != server:
                raise ValueError("ensure result server does not match recovery server lifetime")
            with self._lock:
                registration = self._registrations.get(configuration)
                result_is_current = (
                    registration is not None
                    and not self._closed
                    and self.server == server
                    and registration.active_recovery_token is recovery_token
                )
            if not result_is_current:
                return
            if result.status is not AdbTransportEnsureStatus.SATISFIED:
                self._bus.publish(
                    AdbConfiguredTransportRecoveryExhausted(configuration, result)
                )
        finally:
            launch_pending_recovery = False
            with self._lock:
                self._recovery_threads.discard(current_thread())
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
                        and registration.recovery_pending
                    )
            if launch_pending_recovery:
                self._launch_recovery(configuration)

    def _reset_scope_locked(self) -> None:
        self._tracking_active = False
        self._tracking_scope = None
        self._latest_observation = None
        for registration in self._registrations.values():
            registration.resolution = None
            registration.recovery_pending = False
            registration.active_recovery_token = None
            # The thread may still finish against its captured server lifetime, but it is no
            # longer allowed to affect this registration or block recovery in a successor.
            registration.active_recovery_thread = None

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
                "ADB device serial matches multiple configured transports; use AdbConfiguredTransport"
            )
        return matches[0] if matches else None


__all__ = ["AdbConfiguredTransportSupervisor"]
