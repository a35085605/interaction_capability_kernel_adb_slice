from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock, Thread, current_thread

from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServerIdentity
from adb.server.state import AdbServerStateView
from adb.transport_list.identity import AdbTransportListIdentity
from adb.transport.lifecycle.supervision.policy import AdbConfiguredTransportSupervisionPolicy
from adb.transport.lifecycle.supervision.signal import (
    AdbConfiguredTransportResolutionChanged,
)
from adb.transport.lifecycle.supervision.transition import (
    AdbConfiguredTransportPublishRecoveryExhausted,
    AdbConfiguredTransportRecoveryIdle,
    AdbConfiguredTransportRecoveryInstruction,
    AdbConfiguredTransportStartRecovery,
    decide_recovery_after_ensure,
    decide_recovery_after_projection,
)
from adb.transport.configuration import (
    AdbConfiguredTransport,
    AdbTcpTransportConfiguration,
    AdbUsbTransportConfiguration,
)
from adb.transport_list.model import AdbTransportList
from adb.transport_list.state import (
    AdbTransportListObserved,
    AdbTransportListState,
    AdbTransportListStateView,
)
from adb.transport.resolution import (
    AdbConfiguredTransportProjection,
    AdbConfiguredTransportResolutionStatus,
)
from adb.transport.lifecycle.ensure import (
    AdbTcpTransportEnsureReadiness,
    AdbTcpTransportEnsureResult,
    AdbTcpTransportEnsurer,
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
        transport_list_state: AdbTransportListStateView,
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
        if server_state.current_identity != server:
            raise ValueError("server_state current identity must match server")
        if not isinstance(transport_list_state, AdbTransportListStateView):
            raise TypeError("transport_list_state must satisfy AdbTransportListStateView")
        self._server_state = server_state
        self._projection_server: AdbServerIdentity | None = server
        self._bus = event_bus
        self._tcp_ensurer = tcp_ensurer
        self._transport_list_state = transport_list_state
        self._thread_factory = _thread_factory
        self._lock = Lock()
        self._registrations: dict[
            AdbConfiguredTransport,
            _ConfiguredTransportRegistration,
        ] = {}
        self._subscriptions: tuple[EventSubscriptionToken, ...] = ()
        self._transport_list_fence: AdbTransportListState | None = None
        self._recovery_threads: set[Thread] = set()
        self._closed = False

        # Per-registration tokens fence late recovery results without coupling independent ensures.

    @property
    def server(self) -> AdbServerIdentity | None:
        """Current server lifetime from the runtime authoritative state."""

        return self._server_state.current_identity

    @property
    def server_state(self) -> AdbServerStateView:
        """Authoritative server-state view shared with the owning runtime."""

        return self._server_state

    @property
    def transport_list_state(self) -> AdbTransportListStateView:
        """Current transport-list state used to seed newly registered transport
        projections.
        """

        return self._transport_list_state

    def start(self) -> None:
        """Subscribe to authoritative transport-list observations used for projection."""

        with self._lock:
            if self._closed:
                raise RuntimeError("configured transport supervisor is closed")
            if self._subscriptions:
                raise RuntimeError("configured transport supervisor is already started")
            self._subscriptions = (
                self._bus.subscribe(AdbTransportListObserved, self._on_transport_list_observed),
            )
            server = self._server_state.current_identity
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
            server = self._server_state.current_identity
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
        recovery_instruction: AdbConfiguredTransportRecoveryInstruction = (
            AdbConfiguredTransportRecoveryIdle()
        )
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
            transport_list_state = self._transport_list_state.snapshot()
            transport_list = transport_list_state.current
            transport_list_identity = transport_list_state.current_identity
            server = self._server_state.current_identity
            if (
                transport_list is not None
                and transport_list_identity is not None
                and server is not None
                and self._projection_server == server
                and self._crosses_transport_list_fence_locked(transport_list_state)
            ):
                publication, recovery_instruction = self._project_registration_locked(
                    registration,
                    server,
                    transport_list,
                    transport_list_identity,
                )

        if publication is not None:
            self._bus.publish(publication)
        self._apply_recovery_instruction(recovery_instruction)

    def unregister(
        self,
        configuration: AdbConfiguredTransport | AdbDeviceSerial,
    ) -> bool:
        """Remove one registration and join its active recovery worker unless
        called from that worker.
        """

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
        """Return the latest server- and list-identity-bound projection for one registration."""

        if not isinstance(configuration, (AdbConfiguredTransport, AdbDeviceSerial)):
            raise TypeError("configuration must be AdbConfiguredTransport or AdbDeviceSerial")
        with self._lock:
            key = self._resolve_registration_key_locked(configuration)
            registration = None if key is None else self._registrations.get(key)
            return None if registration is None else registration.projection

    def close(self) -> None:
        """Stop supervision, drop registrations, and join recovery workers other
        than the caller.
        """

        with self._lock:
            if self._closed:
                return
            self._closed = True
            subscriptions = self._subscriptions
            self._subscriptions = ()
            threads = tuple(self._recovery_threads)
            self._recovery_threads.clear()
            self._registrations.clear()
        for token in subscriptions:
            self._bus.unsubscribe(token)
        for thread in threads:
            if thread is not current_thread():
                thread.join()

    def _on_transport_list_observed(self, event: AdbTransportListObserved) -> None:
        publications: list[object] = []
        recovery_instructions: list[AdbConfiguredTransportRecoveryInstruction] = []
        with self._lock:
            server = self._server_state.current_identity
            if (
                self._closed
                or server is None
                or self._projection_server != server
            ):
                return
            state = self._transport_list_state.snapshot()
            if state != event.state or not self._crosses_transport_list_fence_locked(state):
                return
            transport_list = event.transport_list
            transport_list_identity = event.identity
            for registration in self._registrations.values():
                publication, recovery_instruction = self._project_registration_locked(
                    registration,
                    server,
                    transport_list,
                    transport_list_identity,
                )
                if publication is not None:
                    publications.append(publication)
                recovery_instructions.append(recovery_instruction)

        for publication in publications:
            self._bus.publish(publication)
        for recovery_instruction in recovery_instructions:
            self._apply_recovery_instruction(recovery_instruction)

    def _project_registration_locked(
        self,
        registration: _ConfiguredTransportRegistration,
        server: AdbServerIdentity,
        transport_list: AdbTransportList,
        transport_list_identity: AdbTransportListIdentity,
    ) -> tuple[
        AdbConfiguredTransportResolutionChanged | None,
        AdbConfiguredTransportRecoveryInstruction,
    ]:
        if not isinstance(server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")
        if server != self._projection_server:
            raise ValueError("transport list does not match projection server lifetime")
        if not isinstance(transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")
        if not isinstance(transport_list_identity, AdbTransportListIdentity):
            raise TypeError("transport_list_identity must be AdbTransportListIdentity")
        previous = registration.projection
        resolution = transport_list.resolve_configured_transport(registration.configuration)
        current = AdbConfiguredTransportProjection(
            server=server,
            transport_list=transport_list_identity,
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
        recovery_instruction = decide_recovery_after_projection(
            registration.configuration,
            registration.policy,
            current,
            recovery_active=registration.active_recovery_thread is not None,
        )
        return publication, recovery_instruction

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
            projection = registration.projection
            if projection is None or projection.server != server:
                return
            recovery_instruction = decide_recovery_after_projection(
                configuration,
                registration.policy,
                projection,
                recovery_active=registration.active_recovery_thread is not None,
            )
            if not isinstance(recovery_instruction, AdbConfiguredTransportStartRecovery):
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
            recovery_instruction = decide_recovery_after_ensure(configuration, result)
            if result.operation.server != server or result.operation.endpoint != endpoint:
                raise ValueError("ensure result server binding does not match recovery binding")
            with self._lock:
                registration = self._registrations.get(configuration)
                result_is_current = (
                    registration is not None
                    and not self._closed
                    and self._server_state.current_identity == server
                    and self._projection_server == server
                    and registration.active_recovery_token is recovery_token
                )
            if not result_is_current:
                return
            self._apply_recovery_instruction(recovery_instruction)
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

    def _apply_recovery_instruction(
        self,
        instruction: AdbConfiguredTransportRecoveryInstruction,
    ) -> None:
        """Execute one configured-transport recovery instruction through supervisor-owned effects."""

        if isinstance(instruction, AdbConfiguredTransportRecoveryIdle):
            return
        if isinstance(instruction, AdbConfiguredTransportStartRecovery):
            self._launch_recovery(instruction.configuration)
            return
        if isinstance(instruction, AdbConfiguredTransportPublishRecoveryExhausted):
            self._bus.publish(instruction.signal)
            return
        raise TypeError("instruction must be AdbConfiguredTransportRecoveryInstruction")

    def _reset_server_lifetime_locked(self) -> None:
        # ``AdbTransportListObserved`` intentionally carries no server identity. Record the
        # authoritative transport-list state visible at the lifetime transition so a delayed
        # publication from the retired server cannot be projected onto its successor. The first
        # observation committed for the successor necessarily produces a different state identity.
        self._transport_list_fence = self._transport_list_state.snapshot()
        for registration in self._registrations.values():
            registration.projection = None
            registration.active_recovery_token = None
            # The thread may still finish against its captured server lifetime, but it is no
            # longer allowed to affect this registration or block recovery in a successor.
            registration.active_recovery_thread = None

    def _crosses_transport_list_fence_locked(self, state: AdbTransportListState) -> bool:
        if not isinstance(state, AdbTransportListState):
            raise TypeError("state must be AdbTransportListState")
        fence = self._transport_list_fence
        return fence is None or state != fence

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
