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
from adb.transport.configuration import (
    AdbConfiguredTransport,
    AdbTcpTransportConfiguration,
    AdbUsbTransportConfiguration,
)
from adb.tracking.state import (
    AdbDevicesSnapshotState,
    AdbDevicesSnapshotView,
    AdbDevicesSnapshotWriter,
)
from adb.tracking.model import AdbDevicesSnapshot
from adb.transport.resolution import (
    AdbConfiguredTransportResolution,
    AdbConfiguredTransportResolutionStatus,
    resolve_configured_transport,
)
from adb.transport.lifecycle.ensure import (
    AdbTcpTransportEnsureReadiness,
    AdbTcpTransportEnsureResult,
    AdbTcpTransportEnsureStatus,
    AdbTcpTransportEnsurer,
)
from adb.tracking.signal import (
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
    active_recovery_thread: Thread | None = None
    active_recovery_token: object | None = None


class AdbConfiguredTransportSupervisor:
    """Project and reconcile runtime-scoped registrations from device observations.

    Registrations are long-lived and survive replacement of the current ``AdbServer``, including
    replacements that use a different endpoint. Re-establishing observation within one server
    lifetime does not reset transport resolution or invalidate recovery work; server replacement
    does. USB registrations are projection-only; an optional TCP ensurer reconciles currently
    absent TCP transports.
    """

    def __init__(
        self,
        server: AdbServer,
        event_bus: EventBus,
        tcp_ensurer: AdbTcpTransportEnsurer | None,
        *,
        devices: AdbDevicesSnapshotView | None = None,
        _thread_factory: _ThreadFactory = _default_thread_factory,
    ) -> None:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        if not callable(getattr(event_bus, "publish", None)) or not callable(
            getattr(event_bus, "subscribe", None)
        ) or not callable(getattr(event_bus, "unsubscribe", None)):
            raise TypeError("event_bus must satisfy EventBus")
        if tcp_ensurer is not None and not isinstance(tcp_ensurer, AdbTcpTransportEnsurer):
            raise TypeError("tcp_ensurer must satisfy AdbTcpTransportEnsurer or be None")
        owns_devices = devices is None
        if devices is None:
            devices = AdbDevicesSnapshotState()
        if not isinstance(devices, AdbDevicesSnapshotView):
            raise TypeError("devices must satisfy AdbDevicesSnapshotView or be None")
        self.server: AdbServer | None = server
        self._bus = event_bus
        self._tcp_ensurer = tcp_ensurer
        self._devices = devices
        self._devices_writer: AdbDevicesSnapshotWriter | None = (
            devices
            if owns_devices and isinstance(devices, AdbDevicesSnapshotWriter)
            else None
        )
        self._thread_factory = _thread_factory
        self._lock = Lock()
        self._registrations: dict[
            AdbConfiguredTransport,
            _ConfiguredTransportRegistration,
        ] = {}
        self._subscriptions: tuple[EventSubscriptionToken, ...] = ()
        self._tracking_active = False
        self._latest_server_epoch = server.epoch
        self._recovery_threads: set[Thread] = set()
        self._closed = False

        # Per-registration tokens fence late recovery results without coupling independent ensures.

    @property
    def devices(self) -> AdbDevicesSnapshotView:
        """Current tracked-devices state used to seed newly registered transport projections."""

        return self._devices

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

        Server replacement clears server-local resolution and invalidates recovery work
        started for the previous lifetime, while preserving the registration set itself.
        Older server epochs are ignored.
        """

        if server is not None and not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer or None")
        with self._lock:
            if self._closed:
                raise RuntimeError("configured transport supervisor is closed")
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
            self._reset_server_lifetime_locked()

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
        match configuration.transport:
            case AdbUsbTransportConfiguration():
                if policy.tcp_recovery_ensure_policy is not None:
                    raise ValueError("USB configured transports cannot enable TCP recovery")
            case AdbTcpTransportConfiguration():
                if (
                    policy.tcp_recovery_ensure_policy is not None
                    and self._tcp_ensurer is None
                ):
                    raise ValueError("TCP automatic recovery requires a TCP transport ensurer")
            case _:
                raise TypeError("unsupported configured transport type")

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
            revision = self._devices.current if self._tracking_active else None
            server = self.server
            if (
                revision is not None
                and server is not None
                and revision.server_epoch == server.epoch
            ):
                publication, recovery_launch_requested = self._project_registration_locked(
                    registration,
                    revision.snapshot,
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
            writer = self._devices_writer
            if writer is not None and not writer.advance_server(event.server_epoch):
                return
            self._tracking_active = True

    def _on_snapshot_observed(self, event: AdbDevicesSnapshotObserved) -> None:
        publications: list[object] = []
        recovery_launch_requests: list[AdbConfiguredTransport] = []
        with self._lock:
            if (
                self._closed
                or event.server != self.server
                or not self._tracking_active
            ):
                return
            writer = self._devices_writer
            if writer is not None and writer.observe(event.server_epoch, event.snapshot) is None:
                return
            for registration in self._registrations.values():
                publication, recovery_launch_requested = self._project_registration_locked(
                    registration,
                    event.snapshot,
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
            ):
                return
            self._tracking_active = False

    def _project_registration_locked(
        self,
        registration: _ConfiguredTransportRegistration,
        snapshot: AdbDevicesSnapshot,
    ) -> tuple[AdbConfiguredTransportResolutionChanged | None, bool]:
        previous = registration.resolution
        current = resolve_configured_transport(
            registration.configuration,
            snapshot,
        )
        changed = previous != current
        registration.resolution = current

        if current.status is not AdbConfiguredTransportResolutionStatus.ABSENT:
            registration.active_recovery_token = None

        publication = (
            AdbConfiguredTransportResolutionChanged(previous, current)
            if changed
            else None
        )
        match registration.configuration.transport:
            case AdbUsbTransportConfiguration():
                recovery_launch_requested = False
            case AdbTcpTransportConfiguration():
                recovery_launch_requested = (
                    current.status is AdbConfiguredTransportResolutionStatus.ABSENT
                    and registration.policy.tcp_recovery_ensure_policy is not None
                )
            case _:
                raise TypeError("unsupported configured transport type")
        recovery_launch_requested = (
            recovery_launch_requested
            and registration.active_recovery_thread is None
        )
        return publication, recovery_launch_requested

    def _launch_recovery(self, configuration: AdbConfiguredTransport) -> None:
        with self._lock:
            registration = self._registrations.get(configuration)
            server = self.server
            if registration is None or self._closed or server is None:
                return
            if registration.active_recovery_thread is not None:
                return
            resolution = registration.resolution
            if (
                resolution is None
                or resolution.status is not AdbConfiguredTransportResolutionStatus.ABSENT
                or registration.policy.tcp_recovery_ensure_policy is None
            ):
                return
            recovery_token = object()
            thread = self._thread_factory(
                target=self._run_recovery,
                args=(configuration, server, recovery_token),
                name=f"adb-tcp-transport-recovery-{configuration.serial.value}",
            )
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
                ensure_policy = registration.policy.tcp_recovery_ensure_policy
            assert ensure_policy is not None
            ensurer = self._tcp_ensurer
            if ensurer is None:
                raise RuntimeError("TCP configured transport recovery has no ensurer")
            result = ensurer.ensure(
                AdbTcpTransportEnsureReadiness(server, configuration, ensure_policy),
            )
            if not isinstance(result, AdbTcpTransportEnsureResult):
                raise TypeError("TCP ensurer must return AdbTcpTransportEnsureResult")
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
            if result.status is not AdbTcpTransportEnsureStatus.SATISFIED:
                self._bus.publish(
                    AdbConfiguredTransportRecoveryExhausted(configuration, result)
                )
        finally:
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

    def _reset_server_lifetime_locked(self) -> None:
        self._tracking_active = False
        for registration in self._registrations.values():
            registration.resolution = None
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
