from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock, Thread, current_thread

from adb.server.identity import AdbServer
from adb.supervision.model import AdbConfiguredTransportSupervisionPolicy
from adb.supervision.signal import (
    AdbConfiguredTransportRecoveryExhausted,
    AdbConfiguredTransportResolutionChanged,
)
from adb.transport.configuration import AdbConfiguredTransport
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
    disappearance_recovery_pending: bool = False
    active_recovery_thread: Thread | None = None
    active_recovery_token: object | None = None


class AdbConfiguredTransportSupervisor:
    """Long-lived configured-transport projection across disposable tracker scopes.

    The current tracker is the sole projection authority while it is alive. Every
    ``AdbDevicesTrackingStarted`` event begins a fresh baseline: cached per-registration
    resolutions are cleared, so an absence in a newly created tracker never becomes a
    disappearance edge from the destroyed tracker that preceded it.

    Integer tracking generations are unnecessary because tracker scopes are not reused.
    Recovery tokens remain private concurrency fences for independent transport ensure work.
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
        self.server = server
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
        self._latest_observation: AdbDevicesSnapshotObserved | None = None
        self._closed = False

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

    def register(
        self,
        configuration: AdbConfiguredTransport,
        policy: AdbConfiguredTransportSupervisionPolicy | None = None,
    ) -> None:
        if not isinstance(configuration, AdbConfiguredTransport):
            raise TypeError("configuration must be AdbConfiguredTransport")
        if policy is None:
            policy = AdbConfiguredTransportSupervisionPolicy()
        if not isinstance(policy, AdbConfiguredTransportSupervisionPolicy):
            raise TypeError("policy must be AdbConfiguredTransportSupervisionPolicy")
        if policy.recovery_ensure_policy is not None:
            establishment_supported = self._ensurer.supports_establishment(configuration)
            if not isinstance(establishment_supported, bool):
                raise TypeError("ensurer supports_establishment() must return bool")
            if not establishment_supported:
                raise ValueError(
                    "automatic disappearance recovery is not supported for this configured transport"
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
            registration = _ConfiguredTransportRegistration(configuration, policy)
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
        if event.server != self.server:
            return
        with self._lock:
            if self._closed:
                return
            self._tracking_active = True
            self._latest_observation = None
            for registration in self._registrations.values():
                registration.resolution = None
                registration.disappearance_recovery_pending = False
                registration.active_recovery_token = None

    def _on_snapshot_observed(self, event: AdbDevicesSnapshotObserved) -> None:
        if event.server != self.server:
            return
        publications: list[object] = []
        recovery_launch_requests: list[AdbConfiguredTransport] = []
        with self._lock:
            if self._closed or not self._tracking_active:
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
        if event.server != self.server:
            return
        with self._lock:
            if self._closed or not self._tracking_active:
                return
            self._tracking_active = False
            self._latest_observation = None
            for registration in self._registrations.values():
                registration.disappearance_recovery_pending = False
                registration.active_recovery_token = None

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
            registration.disappearance_recovery_pending = False
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
            and registration.policy.recovery_ensure_policy is not None
        )
        if recovery_launch_requested:
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
                AdbTransportEnsureReadiness(self.server, configuration, ensure_policy),
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
                "ADB device serial matches multiple configured transports; use AdbConfiguredTransport"
            )
        return matches[0] if matches else None


__all__ = ["AdbConfiguredTransportSupervisor"]
