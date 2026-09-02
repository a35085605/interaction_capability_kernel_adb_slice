from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock, Thread, current_thread

from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServerIdentity
from adb.server.state import AdbServerStateView
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
from adb.tracking.snapshot.state import (
    AdbTransportListInvalidated,
    AdbTransportListObservation,
    AdbTransportListObserved,
    AdbTransportListStateStore,
    AdbTransportListStateView,
    AdbTransportListStateWriter,
)
from adb.transport.resolution import (
    AdbConfiguredTransportProjection,
    AdbConfiguredTransportResolutionStatus,
)
from adb.transport.lifecycle.ensure import (
    AdbTcpTransportEnsureReadiness,
    AdbTcpTransportEnsureResult,
    AdbTcpTransportEnsureStatus,
    AdbTcpTransportEnsurer,
)
from adb.tracking.signal import (
    AdbTransportListSnapshotObserved,
    AdbTransportListWatchFailed,
    AdbTransportListWatchStarted,
    AdbTransportListWatchStopped,
)
from adb.transport.identity import AdbDeviceSerial
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
    projection: AdbConfiguredTransportProjection | None = None
    active_recovery_thread: Thread | None = None
    active_recovery_token: object | None = None


class AdbConfiguredTransportSupervisor:
    """Project runtime-scoped transport registrations onto server-scoped transport-list
    observations with optional TCP absence recovery.
    """

    def __init__(
        self,
        server: AdbServerIdentity,
        event_bus: EventBus,
        tcp_ensurer: AdbTcpTransportEnsurer | None,
        *,
        server_state: AdbServerStateView,
        transport_list_state: AdbTransportListStateView | None = None,
        _thread_factory: _ThreadFactory = _default_thread_factory,
    ) -> None:
        if not isinstance(server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")
        if not callable(getattr(event_bus, "publish", None)) or not callable(
            getattr(event_bus, "subscribe", None)
        ) or not callable(getattr(event_bus, "unsubscribe", None)):
            raise TypeError("event_bus must satisfy EventBus")
        if tcp_ensurer is not None and not isinstance(tcp_ensurer, AdbTcpTransportEnsurer):
            raise TypeError("tcp_ensurer must satisfy AdbTcpTransportEnsurer or be None")
        if not isinstance(server_state, AdbServerStateView):
            raise TypeError("server_state must satisfy AdbServerStateView")
        if server_state.current != server:
            raise ValueError("server_state current server must match server")
        owns_transport_list_state = transport_list_state is None
        if transport_list_state is None:
            transport_list_state = AdbTransportListStateStore()
        if not isinstance(transport_list_state, AdbTransportListStateView):
            raise TypeError(
                "transport_list_state must satisfy AdbTransportListStateView or be None"
            )
        self._server_state = server_state
        self._projection_server: AdbServerIdentity | None = server
        self._bus = event_bus
        self._tcp_ensurer = tcp_ensurer
        self._transport_list_state = transport_list_state
        self._transport_list_writer: AdbTransportListStateWriter | None = (
            transport_list_state
            if owns_transport_list_state
            and isinstance(transport_list_state, AdbTransportListStateWriter)
            else None
        )
        self._thread_factory = _thread_factory
        self._lock = Lock()
        self._registrations: dict[
            AdbConfiguredTransport,
            _ConfiguredTransportRegistration,
        ] = {}
        self._subscriptions: tuple[EventSubscriptionToken, ...] = ()
        self._watch_active = False
        self._transport_list_needs_invalidation = False
        self._recovery_threads: set[Thread] = set()
        self._closed = False

        # Per-registration tokens fence late recovery results without coupling independent ensures.

    @property
    def server(self) -> AdbServerIdentity | None:
        """Current server lifetime from the runtime authoritative state."""

        return self._server_state.current

    @property
    def server_state(self) -> AdbServerStateView:
        """Authoritative server-state view shared with the owning runtime."""

        return self._server_state

    @property
    def transport_list_state(self) -> AdbTransportListStateView:
        """Current transport-list snapshot state used to seed newly registered transport
        projections.
        """

        return self._transport_list_state

    def start(self) -> None:
        """Subscribe to transport-list watch events used for configured-transport projection."""

        with self._lock:
            if self._closed:
                raise RuntimeError("configured transport supervisor is closed")
            if self._subscriptions:
                raise RuntimeError("configured transport supervisor is already started")
            self._subscriptions = (
                self._bus.subscribe(AdbTransportListWatchStarted, self._on_watch_started),
                self._bus.subscribe(AdbTransportListSnapshotObserved, self._on_snapshot_observed),
                self._bus.subscribe(AdbTransportListWatchFailed, self._on_watch_terminal),
                self._bus.subscribe(AdbTransportListWatchStopped, self._on_watch_terminal),
            )
            server = self._server_state.current
            if server != self._projection_server:
                self._projection_server = server
                self._reset_server_lifetime_locked()

    def reconcile(self) -> None:
        """Rebind runtime-scoped registrations to the current server lifetime and reset
        server-scoped projection and recovery state.
        """

        with self._lock:
            if self._closed:
                raise RuntimeError("configured transport supervisor is closed")
            server = self._server_state.current
            if server == self._projection_server:
                return
            self._projection_server = server
            self._reset_server_lifetime_locked()

    def register(
        self,
        configuration: AdbConfiguredTransport,
        policy: AdbConfiguredTransportSupervisionPolicy | None = None,
    ) -> None:
        """Register one transport and project current transport-list evidence when available."""

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
                and candidate.type is configuration.type
                for candidate in self._registrations
            ):
                raise ValueError(
                    "ADB configured transport serial and transport type are already registered"
                )
            registration = _ConfiguredTransportRegistration(configuration, policy)
            self._registrations[configuration] = registration
            observation = self._transport_list_state.current if self._watch_active else None
            server = self._server_state.current
            if (
                observation is not None
                and server is not None
                and observation.server == server
                and self._projection_server == server
            ):
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
        """Remove one registration and wait for any active recovery attempt."""

        if not isinstance(configuration, (AdbConfiguredTransport, AdbDeviceSerial)):
            raise TypeError("configuration must be AdbConfiguredTransport or AdbDeviceSerial")
        with self._lock:
            key = self._resolve_registration_key_locked(configuration)
            registration = None if key is None else self._registrations.pop(key)
            thread = None if registration is None else registration.active_recovery_thread
        if thread is not None and thread is not current_thread():
            thread.join()
        return registration is not None

    def projection(
        self,
        configuration: AdbConfiguredTransport | AdbDeviceSerial,
    ) -> AdbConfiguredTransportProjection | None:
        """Return the latest server- and snapshot-bound projection for one registration."""

        if not isinstance(configuration, (AdbConfiguredTransport, AdbDeviceSerial)):
            raise TypeError("configuration must be AdbConfiguredTransport or AdbDeviceSerial")
        with self._lock:
            key = self._resolve_registration_key_locked(configuration)
            registration = None if key is None else self._registrations.get(key)
            return None if registration is None else registration.projection

    def close(self) -> None:
        """Stop supervision, drop registrations, and join active recovery workers."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            subscriptions = self._subscriptions
            self._subscriptions = ()
            self._watch_active = False
            threads = tuple(self._recovery_threads)
            self._recovery_threads.clear()
            self._registrations.clear()
        for token in subscriptions:
            self._bus.unsubscribe(token)
        for thread in threads:
            if thread is not current_thread():
                thread.join()

    def _on_watch_started(self, event: AdbTransportListWatchStarted) -> None:
        with self._lock:
            server = self._server_state.current
            if (
                self._closed
                or event.server != server
                or self._projection_server != server
            ):
                return
            writer = self._transport_list_writer
            if writer is not None and self._transport_list_needs_invalidation:
                expected = self._transport_list_state.snapshot()
                if expected.current is not None:
                    invalidation = writer.invalidate(expected)
                    if not isinstance(invalidation, AdbTransportListInvalidated):
                        return
                self._transport_list_needs_invalidation = False
            self._watch_active = True

    def _on_snapshot_observed(self, event: AdbTransportListSnapshotObserved) -> None:
        publications: list[object] = []
        recovery_launch_requests: list[AdbConfiguredTransport] = []
        with self._lock:
            server = self._server_state.current
            if (
                self._closed
                or event.server != server
                or self._projection_server != server
                or not self._watch_active
            ):
                return
            writer = self._transport_list_writer
            event_observation = AdbTransportListObservation(event.server, event.snapshot)
            if writer is not None:
                result = writer.observe(event_observation)
                if not isinstance(result, AdbTransportListObserved):
                    return
                observation = result.observation
            else:
                observation = self._transport_list_state.current
                if observation != event_observation:
                    return
            for registration in self._registrations.values():
                publication, recovery_launch_requested = self._project_registration_locked(
                    registration,
                    observation,
                )
                if publication is not None:
                    publications.append(publication)
                if recovery_launch_requested:
                    recovery_launch_requests.append(registration.configuration)

        for publication in publications:
            self._bus.publish(publication)
        for recovery_configuration in recovery_launch_requests:
            self._launch_recovery(recovery_configuration)

    def _on_watch_terminal(
        self,
        event: AdbTransportListWatchFailed | AdbTransportListWatchStopped,
    ) -> None:
        with self._lock:
            server = self._server_state.current
            if (
                self._closed
                or event.server != server
                or self._projection_server != server
                or not self._watch_active
            ):
                return
            self._watch_active = False

    def _project_registration_locked(
        self,
        registration: _ConfiguredTransportRegistration,
        observation: AdbTransportListObservation,
    ) -> tuple[AdbConfiguredTransportResolutionChanged | None, bool]:
        if observation.server != self._projection_server:
            raise ValueError(
                "transport-list observation does not match projection server lifetime"
            )
        previous = registration.projection
        resolution = observation.snapshot.resolve_configured_transport(
            registration.configuration
        )
        current = AdbConfiguredTransportProjection(
            server=observation.server,
            snapshot_epoch=observation.epoch,
            resolution=resolution,
        )
        changed = previous is None or previous.resolution != current.resolution
        registration.projection = current

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
            state = self._server_state.snapshot()
            server = state.server
            endpoint = state.endpoint
            if (
                registration is None
                or self._closed
                or server is None
                or endpoint is None
                or self._projection_server != server
            ):
                return
            if registration.active_recovery_thread is not None:
                return
            projection = registration.projection
            if (
                projection is None
                or projection.server != server
                or projection.status is not AdbConfiguredTransportResolutionStatus.ABSENT
                or registration.policy.tcp_recovery_ensure_policy is None
            ):
                return
            recovery_token = object()
            thread = self._thread_factory(
                target=self._run_recovery,
                args=(configuration, server, endpoint, recovery_token),
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
        server: AdbServerIdentity,
        endpoint: AdbServerEndpoint,
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
                AdbTcpTransportEnsureReadiness(
                    server,
                    endpoint,
                    configuration,
                    ensure_policy,
                ),
            )
            if not isinstance(result, AdbTcpTransportEnsureResult):
                raise TypeError("TCP ensurer must return AdbTcpTransportEnsureResult")
            if result.operation.configuration != configuration:
                raise ValueError(
                    "ensure result configuration does not match supervised transport"
                )
            if result.operation.server != server or result.operation.endpoint != endpoint:
                raise ValueError("ensure result server binding does not match recovery binding")
            with self._lock:
                registration = self._registrations.get(configuration)
                result_is_current = (
                    registration is not None
                    and not self._closed
                    and self._server_state.current == server
                    and self._projection_server == server
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
        self._watch_active = False
        if self._transport_list_writer is not None:
            self._transport_list_needs_invalidation = True
        for registration in self._registrations.values():
            registration.projection = None
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
